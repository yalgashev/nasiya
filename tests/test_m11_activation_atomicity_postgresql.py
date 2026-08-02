from datetime import UTC, datetime, timedelta
from inspect import getsource
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.auth.sessions as auth_sessions_module
import app.customer_activation.repository as activation_repository_module
import app.otp.issuance as otp_issuance_module
import app.otp.repository as otp_repository_module
import app.otp.verification as otp_verification_module
import app.telegram.service as telegram_service_module
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import RawSessionToken, create_authenticated_session
from app.customer.models import (
    CUSTOMER_ONBOARDING_STATUS_ACTIVE,
    CUSTOMER_ONBOARDING_STATUS_DRAFT,
    Customer,
)
from app.customer.ports import (
    CustomerActivationTransitionOutcome,
    CustomerLifecycleStatus,
    OwnCustomerLifecyclePort,
)
from app.customer.repository import (
    get_existing_own_customer_status,
    load_existing_own_customer,
    lock_existing_own_customer_for_update,
    transition_existing_own_customer_draft_to_active,
)
from app.customer_activation.contracts import mark_customer_activation_committed
from app.customer_activation.repository import (
    CurrentSessionRotationConflict,
    SqlAlchemyCurrentSessionRotation,
)
from app.db import create_database_session_factory
from app.settings import Settings
from app.shop.models import Shop

NOW = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)


def add_user_and_customer(
    session: Session,
    *,
    phone: str,
) -> tuple[User, Customer]:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()
    return user, customer


@pytest.mark.integration
def test_rotation_is_current_only_preserves_safe_context_and_is_commit_composable(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        user, _customer = add_user_and_customer(
            session,
            phone="+998900001319",
        )
        shop = Shop(
            name="Synthetic shop",
            phone="+998900009999",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(shop)
        session.flush()
        current = create_authenticated_session(
            session,
            user.id,
            "synthetic-browser",
            NOW,
            settings=Settings(),
        )
        other = create_authenticated_session(
            session,
            user.id,
            "other-browser",
            NOW,
            settings=Settings(),
        )
        session.flush()
        current.session.active_shop_id = shop.id
        shop_id = shop.id
        current_id = current.session.id
        current_expires_at = current.session.expires_at
        current_raw_token = current.raw_token.as_cookie_value()
        other_id = other.session.id
        other_token_hash = other.session.token_hash
        user_id = user.id

    with session_factory.begin() as session:
        prepared = SqlAlchemyCurrentSessionRotation(
            session,
            settings=Settings(),
        ).replace_current_authenticated_session(
            actor_user_id=user_id,
            current_session_id=current_id,
            now=NOW + timedelta(minutes=1),
        )
        assert prepared is not None
        replacement_id = prepared._rotation.replacement_session_id
        assert session.get(AuthSession, current_id).revoked_at == NOW + timedelta(
            minutes=1
        )
        replacement = session.get(AuthSession, replacement_id)
        assert replacement is not None
        assert replacement.expires_at == current_expires_at
        assert replacement.active_shop_id == shop_id
        assert replacement.user_agent == "synthetic-browser"
        other_stored = session.get(AuthSession, other_id)
        assert other_stored is not None
        assert other_stored.revoked_at is None
        assert other_stored.token_hash == other_token_hash

    committed = mark_customer_activation_committed(prepared)
    assert committed.release_cookie_token().as_cookie_value() != current_raw_token


@pytest.mark.integration
def test_rotation_missing_or_foreign_current_session_is_zero_write_noop(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        user, _customer = add_user_and_customer(
            session,
            phone="+998900001320",
        )
        current = create_authenticated_session(
            session,
            user.id,
            "synthetic-browser",
            NOW,
            settings=Settings(),
        )
        session.flush()
        current_id = current.session.id
        user_id = user.id

    with session_factory.begin() as session:
        before = session.scalar(select(func.count()).select_from(AuthSession))
        result = SqlAlchemyCurrentSessionRotation(
            session,
            settings=Settings(),
        ).replace_current_authenticated_session(
            actor_user_id=uuid4(),
            current_session_id=current_id,
            now=NOW + timedelta(minutes=1),
        )
        after = session.scalar(select(func.count()).select_from(AuthSession))
        stored = session.get(AuthSession, current_id)
        assert result is None
        assert before == after == 1
        assert stored is not None and stored.user_id == user_id
        assert stored.revoked_at is None


@pytest.mark.integration
def test_rotation_expected_token_conflict_keeps_outer_session_usable(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        user, _customer = add_user_and_customer(
            session,
            phone="+998900001321",
        )
        current = create_authenticated_session(
            session,
            user.id,
            "synthetic-browser",
            NOW,
            settings=Settings(),
        )
        session.flush()
        current_id = current.session.id
        current_token = current.raw_token
        user_id = user.id

    monkeypatch.setattr(
        auth_sessions_module,
        "create_session_token",
        lambda: RawSessionToken(current_token.as_cookie_value()),
    )
    with session_factory.begin() as session:
        with pytest.raises(CurrentSessionRotationConflict):
            SqlAlchemyCurrentSessionRotation(
                session,
                settings=Settings(),
            ).replace_current_authenticated_session(
                actor_user_id=user_id,
                current_session_id=current_id,
                now=NOW + timedelta(minutes=1),
            )
        assert session.scalar(select(1)) == 1
        stored = session.get(AuthSession, current_id)
        assert stored is not None and stored.revoked_at is None
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 1


@pytest.mark.integration
def test_own_customer_load_lock_and_missing_transition_never_create_customer(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        first_user, first_customer = add_user_and_customer(
            session,
            phone="+998900001311",
        )
        second_user, second_customer = add_user_and_customer(
            session,
            phone="+998900001312",
        )
        first_user_id = first_user.id
        first_customer_id = first_customer.id
        second_user_id = second_user.id
        second_customer_id = second_customer.id

    with session_factory() as session:
        assert (
            load_existing_own_customer(
                session,
                actor_user_id=first_user_id,
            ).id
            == first_customer_id
        )
        assert (
            lock_existing_own_customer_for_update(
                session,
                actor_user_id=second_user_id,
            ).id
            == second_customer_id
        )
        assert (
            get_existing_own_customer_status(
                session,
                actor_user_id=first_user_id,
            )
            is CustomerLifecycleStatus.DRAFT
        )
        before_count = session.scalar(select(func.count()).select_from(Customer))
        missing_result = transition_existing_own_customer_draft_to_active(
            session,
            actor_user_id=uuid4(),
            expected_status=CustomerLifecycleStatus.DRAFT,
            now=NOW,
        )
        after_count = session.scalar(select(func.count()).select_from(Customer))

        assert missing_result.outcome is CustomerActivationTransitionOutcome.MISSING
        assert before_count == after_count == 2
        with pytest.raises(ValueError, match="expected status must be draft"):
            transition_existing_own_customer_draft_to_active(
                session,
                actor_user_id=first_user_id,
                expected_status=CustomerLifecycleStatus.ACTIVE,
                now=NOW,
            )
        assert (
            get_existing_own_customer_status(
                session,
                actor_user_id=first_user_id,
            )
            is CustomerLifecycleStatus.DRAFT
        )
        assert (
            get_existing_own_customer_status(
                session,
                actor_user_id=second_user_id,
            )
            is CustomerLifecycleStatus.DRAFT
        )


@pytest.mark.integration
def test_draft_to_active_transition_is_exact_and_already_active_is_zero_write(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        user, customer = add_user_and_customer(
            session,
            phone="+998900001313",
        )
        customer_id = customer.id
        user_id = user.id

    with session_factory.begin() as session:
        first = transition_existing_own_customer_draft_to_active(
            session,
            actor_user_id=user_id,
            expected_status=CustomerLifecycleStatus.DRAFT,
            now=NOW + timedelta(minutes=1),
        )
        activated = session.get(Customer, customer_id)
        assert activated is not None
        first_snapshot = (
            activated.onboarding_status,
            activated.activated_at,
            activated.updated_at,
        )
        second = transition_existing_own_customer_draft_to_active(
            session,
            actor_user_id=user_id,
            expected_status=CustomerLifecycleStatus.DRAFT,
            now=NOW + timedelta(minutes=2),
        )
        second_snapshot = (
            activated.onboarding_status,
            activated.activated_at,
            activated.updated_at,
        )

        assert first.outcome is CustomerActivationTransitionOutcome.ACTIVATED
        assert first_snapshot == (
            CUSTOMER_ONBOARDING_STATUS_ACTIVE,
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=1),
        )
        assert second.outcome is CustomerActivationTransitionOutcome.ALREADY_ACTIVE
        assert second_snapshot == first_snapshot


@pytest.mark.integration
def test_customer_transition_is_owned_by_caller_transaction(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as setup_session:
        user, _customer = add_user_and_customer(
            setup_session,
            phone="+998900001314",
        )
        user_id = user.id

    writer = session_factory()
    observer = session_factory()
    try:
        result = transition_existing_own_customer_draft_to_active(
            writer,
            actor_user_id=user_id,
            expected_status=CustomerLifecycleStatus.DRAFT,
            now=NOW + timedelta(minutes=1),
        )

        assert result.outcome is CustomerActivationTransitionOutcome.ACTIVATED
        assert (
            get_existing_own_customer_status(
                observer,
                actor_user_id=user_id,
            )
            is CustomerLifecycleStatus.DRAFT
        )

        writer.rollback()
        observer.expire_all()
        assert (
            get_existing_own_customer_status(
                observer,
                actor_user_id=user_id,
            )
            is CustomerLifecycleStatus.DRAFT
        )
    finally:
        writer.close()
        observer.close()


def test_customer_lifecycle_port_is_sqlalchemy_free_and_reverse_is_absent() -> None:
    port_source = getsource(OwnCustomerLifecyclePort)
    repository_source = getsource(transition_existing_own_customer_draft_to_active)

    assert "sqlalchemy" not in port_source.casefold()
    assert "deactivate" not in port_source.casefold()
    assert ".commit(" not in repository_source
    assert ".rollback(" not in repository_source
    assert ".close(" not in repository_source


def test_corrected_lock_paths_are_static_forward_subsequences() -> None:
    otp_lock_source = getsource(
        otp_repository_module._lock_outstanding_challenge_set_for_purposes
    )
    issue_source = getsource(otp_issuance_module._issue_challenge_for_target)
    verify_source = getsource(otp_verification_module.check_login_otp_candidate)
    invalidate_source = getsource(
        otp_verification_module._invalidate_verification_candidate
    )
    consume_link_source = getsource(telegram_service_module.consume_start_token)
    unlink_source = getsource(telegram_service_module.unlink)
    rotation_source = getsource(
        activation_repository_module.SqlAlchemyCurrentSessionRotation.replace_current_authenticated_session
    )

    assert otp_lock_source.index("select(OtpDispatch)") < otp_lock_source.index(
        "select(OtpChallenge)\n            .where(\n                OtpChallenge.id.in_"
    )
    assert issue_source.index(
        "lock_outstanding_challenge_set_by_user_and_browser"
    ) < issue_source.index("_lock_and_revalidate_target")
    assert verify_source.index(
        "lock_verification_candidate_set_by_browser"
    ) < verify_source.index("_revalidate_current_login_target")
    assert "load_dispatch_by_challenge_for_update" not in invalidate_source
    assert consume_link_source.index("_lock_link_change_otp_state") < (
        consume_link_source.index("lock_telegram_link_change_set")
    )
    assert unlink_source.index("_lock_link_change_otp_state") < unlink_source.index(
        "unlink_verified_private_chat"
    )
    assert rotation_source.index("with_for_update") < rotation_source.index(
        "rotate_session"
    )
    for source in (otp_lock_source, issue_source, verify_source, rotation_source):
        assert ".commit(" not in source
        assert ".rollback(" not in source
        assert ".close(" not in source
