from datetime import UTC, datetime, timedelta
from inspect import getsource
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.auth.sessions as auth_sessions_module
import app.customer_activation.repository as activation_repository_module
import app.customer_activation.service as activation_service_module
import app.otp.issuance as otp_issuance_module
import app.otp.repository as otp_repository_module
import app.otp.verification as otp_verification_module
import app.telegram.repository as telegram_repository_module
import app.telegram.service as telegram_service_module
from app.audit.models import AuditLog
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
from app.customer_activation.contracts import (
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    PreparedCustomerActivation,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    VerifyRegistrationOtp,
    mark_customer_activation_committed,
)
from app.customer_activation.repository import (
    CurrentSessionRotationConflict,
    SqlAlchemyCurrentSessionRotation,
)
from app.customer_activation.service import verify_and_activate_registration_customer
from app.db import create_database_session_factory
from app.otp.code import OtpCode
from app.otp.contracts import OtpChallengeStatus, OtpPurpose
from app.otp.crypto import compute_otp_code_mac
from app.otp.models import OtpChallenge, OtpChallengeEvent
from app.otp.repository import (
    activate_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
)
from app.settings import Settings
from app.shop.models import Shop
from tests.m11_seed import (
    REGISTRATION_DIGEST,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)

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


def _activation_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key="m11-atomicity-rate-key-at-least-32-characters",
        otp_hmac_key="m11-atomicity-otp-key-at-least-32-characters",
    )


def _seed_activation_attempt(
    session: Session,
    *,
    settings: Settings,
    phone: str,
) -> tuple[VerifyRegistrationOtp, UUID, UUID, UUID]:
    snapshot = seed_registration_snapshot(session, phone=phone)
    challenge = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=NOW,
    )
    create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    activate_challenge(
        session,
        challenge=challenge,
        code_mac=compute_otp_code_mac(
            otp_hmac_key=settings.require_otp_hmac_key(),
            challenge_id=challenge.id,
            user_id=snapshot.user_id,
            purpose=OtpPurpose.REGISTRATION,
            code=OtpCode("004271"),
        ),
        activated_at=NOW,
        expires_at=NOW + timedelta(hours=3),
    )
    current = create_authenticated_session(
        session,
        snapshot.user_id,
        "synthetic-atomicity-browser",
        NOW,
        settings=settings,
    )
    session.flush()
    command = VerifyRegistrationOtp(
        actor=CustomerActivationActor(snapshot.user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=current.session.id,
            browser_binding_digest=REGISTRATION_DIGEST,
        ),
        candidate_code="004271",
        now=NOW + timedelta(hours=2),
    )
    return command, challenge.id, snapshot.customer_id, current.session.id


def _activation_persistence_state(
    session: Session,
    *,
    challenge_id: UUID,
    customer_id: UUID,
    current_session_id: UUID,
) -> tuple[object, ...]:
    challenge = session.get(OtpChallenge, challenge_id)
    customer = session.get(Customer, customer_id)
    current = session.get(AuthSession, current_session_id)
    assert challenge is not None
    assert customer is not None
    assert current is not None
    return (
        challenge.status,
        challenge.consumed_at,
        challenge.failed_attempts,
        customer.onboarding_status,
        customer.activated_at,
        session.scalar(select(func.count()).select_from(OtpChallengeEvent)),
        session.scalar(select(func.count()).select_from(AuditLog)),
        session.scalar(select(func.count()).select_from(AuthSession)),
        current.revoked_at,
    )


@pytest.mark.integration
def test_rotation_is_current_only_and_preserves_shop_and_other_sessions(
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
@pytest.mark.parametrize(
    ("failure_stage", "phone"),
    (
        ("consume_flush", "+998900001411"),
        ("otp_event", "+998900001412"),
        ("customer_transition", "+998900001413"),
        ("central_audit", "+998900001414"),
        ("session_replacement", "+998900001415"),
    ),
)
def test_activation_write_fault_matrix_rolls_back_every_boundary(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    failure_stage: str,
    phone: str,
) -> None:
    settings = _activation_settings(m2_test_database)
    with Session(m2_test_database) as session, session.begin():
        command, challenge_id, customer_id, current_session_id = (
            _seed_activation_attempt(session, settings=settings, phone=phone)
        )

    def fail_after_consume(
        session: Session,
        **kwargs: object,
    ) -> object:
        original_consume(session, **kwargs)
        session.flush()
        raise RuntimeError("synthetic activation boundary failure")

    def fail_after_transition(
        session: Session,
        **kwargs: object,
    ) -> object:
        original_transition(session, **kwargs)
        session.flush()
        raise RuntimeError("synthetic activation boundary failure")

    def fail_write(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic activation boundary failure")

    original_consume = activation_service_module.consume_registration_challenge
    original_transition = (
        activation_service_module.transition_existing_own_customer_draft_to_active
    )
    if failure_stage == "consume_flush":
        monkeypatch.setattr(
            activation_service_module,
            "consume_registration_challenge",
            fail_after_consume,
        )
    elif failure_stage == "otp_event":
        monkeypatch.setattr(
            otp_repository_module,
            "append_challenge_event",
            fail_write,
        )
    elif failure_stage == "customer_transition":
        monkeypatch.setattr(
            activation_service_module,
            "transition_existing_own_customer_draft_to_active",
            fail_after_transition,
        )
    elif failure_stage == "central_audit":
        monkeypatch.setattr(
            activation_service_module,
            "append_audit_event",
            fail_write,
        )
    else:
        monkeypatch.setattr(
            SqlAlchemyCurrentSessionRotation,
            "replace_current_authenticated_session",
            fail_write,
        )

    writer = Session(m2_test_database)
    try:
        with pytest.raises(
            RuntimeError,
            match="synthetic activation boundary failure",
        ):
            with writer.begin():
                verify_and_activate_registration_customer(
                    writer,
                    command=command,
                    settings=settings,
                    identity_crypto_config=synthetic_identity_crypto_config(),
                )
        assert writer.scalar(select(1)) == 1
    finally:
        writer.close()

    with Session(m2_test_database) as session:
        state = _activation_persistence_state(
            session,
            challenge_id=challenge_id,
            customer_id=customer_id,
            current_session_id=current_session_id,
        )

    assert state == (
        OtpChallengeStatus.ACTIVE.value,
        None,
        0,
        CUSTOMER_ONBOARDING_STATUS_DRAFT,
        None,
        0,
        0,
        1,
        None,
    )


def test_central_audit_failure_rolls_back_consume_activation_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with monkeypatch.context() as scoped:
        test_activation_write_fault_matrix_rolls_back_every_boundary(
            scoped,
            m2_test_database,
            "central_audit",
            "+998900001426",
        )


def test_otp_event_failure_rolls_back_activation_audit_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with monkeypatch.context() as scoped:
        test_activation_write_fault_matrix_rolls_back_every_boundary(
            scoped,
            m2_test_database,
            "otp_event",
            "+998900001427",
        )


@pytest.mark.parametrize("failure_boundary", ("session", "commit"))
def test_session_or_cookie_failure_rolls_back_and_releases_no_cookie(
    failure_boundary: str,
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    if failure_boundary == "session":
        with monkeypatch.context() as scoped:
            test_activation_write_fault_matrix_rolls_back_every_boundary(
                scoped,
                m2_test_database,
                "session_replacement",
                "+998900001428",
            )
    else:
        test_transaction_commit_failure_keeps_activation_uncommitted_and_cookie_sealed(
            m2_test_database
        )


@pytest.mark.integration
def test_transaction_commit_failure_keeps_activation_uncommitted_and_cookie_sealed(
    m2_test_database: Engine,
) -> None:
    settings = _activation_settings(m2_test_database)
    with Session(m2_test_database) as session, session.begin():
        command, challenge_id, customer_id, current_session_id = (
            _seed_activation_attempt(
                session,
                settings=settings,
                phone="+998900001416",
            )
        )

    writer = Session(m2_test_database)
    prepared: PreparedCustomerActivation | None = None

    def fail_commit(_session: Session) -> None:
        if _session.in_nested_transaction():
            return
        raise RuntimeError("synthetic activation commit failure")

    event.listen(writer, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="synthetic activation commit failure"):
            with writer.begin():
                result = verify_and_activate_registration_customer(
                    writer,
                    command=command,
                    settings=settings,
                    identity_crypto_config=synthetic_identity_crypto_config(),
                )
                assert isinstance(result, PreparedCustomerActivation)
                prepared = result
        assert writer.scalar(select(1)) == 1
    finally:
        event.remove(writer, "before_commit", fail_commit)
        writer.close()

    assert prepared is not None
    assert not hasattr(prepared, "release_cookie_token")
    with Session(m2_test_database) as session:
        state = _activation_persistence_state(
            session,
            challenge_id=challenge_id,
            customer_id=customer_id,
            current_session_id=current_session_id,
        )
    assert state == (
        OtpChallengeStatus.ACTIVE.value,
        None,
        0,
        CUSTOMER_ONBOARDING_STATUS_DRAFT,
        None,
        0,
        0,
        1,
        None,
    )


@pytest.mark.integration
def test_post_commit_response_failure_replay_is_zero_write_already_active(
    m2_test_database: Engine,
) -> None:
    settings = _activation_settings(m2_test_database)
    with Session(m2_test_database) as session, session.begin():
        command, challenge_id, customer_id, current_session_id = (
            _seed_activation_attempt(
                session,
                settings=settings,
                phone="+998900001417",
            )
        )

    with Session(m2_test_database) as session, session.begin():
        prepared = verify_and_activate_registration_customer(
            session,
            command=command,
            settings=settings,
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        assert isinstance(prepared, PreparedCustomerActivation)
    committed = mark_customer_activation_committed(prepared)

    with pytest.raises(RuntimeError, match="synthetic response preparation failure"):
        _ = committed
        raise RuntimeError("synthetic response preparation failure")

    with Session(m2_test_database) as session:
        replacement_session_id = session.scalar(
            select(AuthSession.id).where(
                AuthSession.id != current_session_id,
                AuthSession.revoked_at.is_(None),
            )
        )
    assert replacement_session_id is not None
    replay_command = VerifyRegistrationOtp(
        actor=command.actor,
        browser=CustomerActivationBrowserContext(
            current_session_id=replacement_session_id,
            browser_binding_digest=REGISTRATION_DIGEST,
        ),
        candidate_code="004271",
        now=command.now + timedelta(minutes=1),
    )
    with Session(m2_test_database) as session, session.begin():
        replay = verify_and_activate_registration_customer(
            session,
            command=replay_command,
            settings=settings,
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
    assert replay == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.ALREADY_ACTIVE
    )

    with Session(m2_test_database) as session:
        state = _activation_persistence_state(
            session,
            challenge_id=challenge_id,
            customer_id=customer_id,
            current_session_id=current_session_id,
        )
    assert state == (
        OtpChallengeStatus.CONSUMED.value,
        command.now,
        0,
        CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        command.now,
        1,
        1,
        2,
        command.now,
    )


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
    otp_id_recheck_source = getsource(
        otp_repository_module.get_outstanding_challenge_ids_by_user_for_purposes
    )
    issue_source = getsource(otp_issuance_module._issue_challenge_for_target)
    verify_source = getsource(otp_verification_module.check_login_otp_candidate)
    invalidate_source = getsource(
        otp_verification_module._invalidate_verification_candidate
    )
    consume_link_source = getsource(telegram_service_module.consume_start_token)
    unlink_source = getsource(telegram_service_module.unlink)
    link_token_issue_source = getsource(
        telegram_service_module._issue_token_for_link_state_after_rate_limit
    )
    rotation_source = getsource(
        activation_repository_module.SqlAlchemyCurrentSessionRotation.replace_current_authenticated_session
    )
    token_lock_sources = tuple(
        getsource(lock_callable)
        for lock_callable in (
            telegram_repository_module.lock_outstanding_telegram_link_token_set_by_user,
            telegram_repository_module.lock_telegram_link_token_set_by_ids,
        )
    )
    link_set_source = getsource(
        telegram_repository_module.lock_telegram_link_change_set
    )
    prelocked_mutation_sources = tuple(
        getsource(mutation)
        for mutation in (
            telegram_repository_module.link_unverified_private_chat_from_prelocked_state,
            telegram_repository_module.relink_unverified_private_chat_from_prelocked_state,
            telegram_repository_module.link_phone_verified_private_chat_from_prelocked_state,
            telegram_repository_module.relink_phone_verified_private_chat_from_prelocked_state,
            telegram_repository_module.unlink_verified_private_chat_from_prelocked_state,
        )
    )
    locked_invalidation_source = getsource(
        telegram_repository_module.invalidate_locked_outstanding_telegram_link_tokens
    )
    contact_binding_source = getsource(
        telegram_service_module.bind_start_token_for_contact
    )
    contact_binding_mutation_source = getsource(
        telegram_repository_module.bind_locked_telegram_link_token_for_contact
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
    assert (
        consume_link_source.index(
            "get_pending_telegram_link_token_ids_by_contact_binding"
        )
        < consume_link_source.index("lock_telegram_link_token_set_by_ids")
        < consume_link_source.rindex(
            "get_pending_telegram_link_token_ids_by_contact_binding"
        )
        < consume_link_source.index("_lock_link_change_otp_state")
        < consume_link_source.index("_lock_active_user")
        < consume_link_source.index("_link_change_otp_state_is_current")
        < consume_link_source.index("lock_telegram_link_change_set")
        < consume_link_source.index("lock_existing_own_customer_for_update")
    )
    assert (
        unlink_source.index("lock_outstanding_telegram_link_token_set_by_user")
        < unlink_source.index("_lock_link_change_otp_state")
        < unlink_source.index("_lock_active_user")
        < unlink_source.index("_link_change_otp_state_is_current")
        < unlink_source.index("get_outstanding_telegram_link_token_ids_by_user")
        < unlink_source.index("get_telegram_link_by_user_for_update")
        < unlink_source.index("lock_existing_own_customer_for_update")
        < unlink_source.index("invalidate_locked_outstanding_telegram_link_tokens")
        < unlink_source.index("unlink_verified_private_chat")
    )
    assert "invalidate_outstanding_telegram_link_tokens" not in unlink_source
    assert "with_for_update" not in otp_id_recheck_source
    assert ".order_by(OtpChallenge.id.asc())" in otp_id_recheck_source
    assert (
        link_token_issue_source.index("invalidate_and_insert_telegram_link_token")
        < link_token_issue_source.index("_lock_active_user")
        < link_token_issue_source.index("get_telegram_link_by_user_for_update")
    )
    assert link_token_issue_source.count("has_active_telegram_link") == 1
    for token_lock_source in token_lock_sources:
        assert token_lock_source.index(
            ".order_by(TelegramLinkToken.id.asc())"
        ) < token_lock_source.index(".with_for_update()")
    assert link_set_source.index(
        ".order_by(TelegramLink.id.asc())"
    ) < link_set_source.index(".with_for_update()")
    for mutation_source in prelocked_mutation_sources:
        assert "with_for_update" not in mutation_source
        assert "get_telegram_link_by_user_for_update" not in mutation_source
    assert "select(" not in locked_invalidation_source
    assert "update(" not in locked_invalidation_source
    assert "with_for_update" not in locked_invalidation_source
    assert (
        contact_binding_source.index("get_telegram_link_token_ids_for_contact_binding")
        < contact_binding_source.index("lock_telegram_link_token_set_by_ids")
        < contact_binding_source.rindex(
            "get_telegram_link_token_ids_for_contact_binding"
        )
        < contact_binding_source.index("bind_locked_telegram_link_token_for_contact")
    )
    for forbidden_lock in (
        "_lock_active_user",
        "_lock_link_change_otp_state",
        "lock_telegram_link_change_set",
        "lock_existing_own_customer_for_update",
    ):
        assert forbidden_lock not in contact_binding_source
    assert (
        contact_binding_mutation_source.index(
            "token.pending_contact_binding_mac = None"
        )
        < contact_binding_mutation_source.index("session.flush()")
        < contact_binding_mutation_source.index(
            "target.pending_contact_binding_mac = stored_binding_mac"
        )
        < contact_binding_mutation_source.rindex("session.flush()")
    )
    assert rotation_source.index("with_for_update") < rotation_source.index(
        "rotate_session"
    )
    for source in (
        otp_lock_source,
        issue_source,
        verify_source,
        rotation_source,
        consume_link_source,
        unlink_source,
        link_token_issue_source,
        *token_lock_sources,
        link_set_source,
        *prelocked_mutation_sources,
        locked_invalidation_source,
        contact_binding_source,
        contact_binding_mutation_source,
    ):
        assert ".commit(" not in source
        assert ".rollback(" not in source
        assert ".close(" not in source
        assert "sleep(" not in source
        assert "nowait" not in source
        assert "lock_timeout" not in source
        assert "pg_" + "advisory" not in source
