from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.customer.models import Customer
from app.customer_activation.contracts import (
    CurrentRegistrationAcceptanceSelection,
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    RegistrationPrerequisiteError,
    RegistrationReadinessComponent,
    RegistrationReadinessComponentStatus,
    RegistrationReadinessComponentView,
    RegistrationReadinessSnapshot,
    RegistrationReadinessState,
    RegistrationReadinessView,
)
from app.customer_activation.repository import (
    SqlAlchemyCustomerDocumentReadiness,
    SqlAlchemyCustomerIdentityReadiness,
    SqlAlchemyRegistrationOfferReadiness,
)
from app.customer_activation.service import (
    AuthenticatedActivationContext,
    get_registration_readiness,
    select_current_registration_acceptance,
)
from app.customer_document.models import CustomerDocument
from app.customer_identity.contracts import IdentityRevision
from app.customer_identity.models import CustomerIdentity
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance, OfferText, OfferVersion
from app.otp.crypto import OtpBrowserBindingDigest
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.storage.models import ObjectFile, ObjectFileStatus
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink
from tests.m11_seed import (
    NOW,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_CUSTOMER_ID = UUID("22222222-2222-4222-8222-222222222222")
_LINK_ID = UUID("33333333-3333-4333-8333-333333333333")
_ACCEPTANCE_ID = UUID("44444444-4444-4444-8444-444444444444")
_DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
_LINKED_AT = datetime(2026, 8, 2, 9, 30, tzinfo=UTC)
_DIGEST_TEXT = "a" * 64


def _snapshot(**overrides: object) -> RegistrationReadinessSnapshot:
    values: dict[str, object] = {
        "user_id": _USER_ID,
        "customer_id": _CUSTOMER_ID,
        "telegram_link_id": _LINK_ID,
        "telegram_linked_at": _LINKED_AT,
        "registration_offer_acceptance_id": _ACCEPTANCE_ID,
        "customer_identity_revision": IdentityRevision(3),
        "customer_document_id": _DOCUMENT_ID,
        "browser_binding_digest": OtpBrowserBindingDigest(_DIGEST_TEXT),
    }
    values.update(overrides)
    return RegistrationReadinessSnapshot(**values)  # type: ignore[arg-type]


def _components(
    *,
    incomplete: RegistrationReadinessComponent | None = None,
) -> tuple[RegistrationReadinessComponentView, ...]:
    return tuple(
        RegistrationReadinessComponentView(
            component=component,
            status=(
                RegistrationReadinessComponentStatus.INCOMPLETE
                if component is incomplete
                else RegistrationReadinessComponentStatus.COMPLETE
            ),
        )
        for component in RegistrationReadinessComponent
    )


def _activation_context(
    snapshot: RegistrationReadinessSnapshot,
) -> AuthenticatedActivationContext:
    return AuthenticatedActivationContext(
        actor=CustomerActivationActor(snapshot.user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=UUID("66666666-6666-4666-8666-666666666666"),
            browser_binding_digest=snapshot.browser_binding_digest,
        ),
        trusted_client_ip=ResolvedClientIp("203.0.113.38"),
        _canonical_account_phone="+998900001328",
    )


def test_readiness_snapshot_has_exact_minimal_typed_fields() -> None:
    snapshot = _snapshot()

    assert tuple(field.name for field in fields(snapshot)) == (
        "user_id",
        "customer_id",
        "telegram_link_id",
        "telegram_linked_at",
        "registration_offer_acceptance_id",
        "customer_identity_revision",
        "customer_document_id",
        "browser_binding_digest",
    )
    assert snapshot.telegram_linked_at == _LINKED_AT
    assert snapshot.customer_identity_revision == IdentityRevision(3)
    with pytest.raises(FrozenInstanceError):
        snapshot.customer_id = _USER_ID  # type: ignore[misc]


def test_readiness_snapshot_requires_uuid_aware_time_revision_and_digest() -> None:
    invalid = (
        {"user_id": str(_USER_ID)},
        {"customer_id": str(_CUSTOMER_ID)},
        {"telegram_link_id": str(_LINK_ID)},
        {"telegram_linked_at": datetime(2026, 8, 2, 9, 30)},
        {"registration_offer_acceptance_id": str(_ACCEPTANCE_ID)},
        {"customer_identity_revision": 3},
        {"customer_document_id": str(_DOCUMENT_ID)},
        {"browser_binding_digest": _DIGEST_TEXT},
    )

    for values in invalid:
        with pytest.raises((TypeError, ValueError)):
            _snapshot(**values)
    with pytest.raises(ValueError):
        IdentityRevision(0)


def test_readiness_snapshot_contains_only_exact_redacted_evidence() -> None:
    rendered = repr(_snapshot())

    for forbidden in (
        str(_USER_ID),
        str(_CUSTOMER_ID),
        str(_LINK_ID),
        str(_ACCEPTANCE_ID),
        str(_DOCUMENT_ID),
        _LINKED_AT.isoformat(),
        _DIGEST_TEXT,
    ):
        assert forbidden not in rendered
    assert rendered.count("<redacted>") == 7
    assert "customer_identity_revision=3" in rendered


def test_readiness_snapshot_has_no_forbidden_product_or_storage_fields() -> None:
    names = {field.name for field in fields(RegistrationReadinessSnapshot)}
    forbidden = {
        "phone",
        "chat_id",
        "offer_title",
        "offer_body",
        "content_hash",
        "language",
        "first_name",
        "last_name",
        "jshshir",
        "document_number",
        "ciphertext",
        "nonce",
        "key_id",
        "blind_index",
        "object_file_id",
        "bucket",
        "object_key",
        "checksum",
        "presigned_url",
    }

    assert forbidden.isdisjoint(names)


def test_readiness_view_is_pii_free_and_has_no_provider_status_or_ids() -> None:
    view = RegistrationReadinessView(
        state=RegistrationReadinessState.READY_FOR_OTP,
        components=_components(),
    )

    assert tuple(component.component.value for component in view.components) == (
        "TELEGRAM_LINK",
        "OFFER_ACCEPTANCE",
        "CUSTOMER_IDENTITY",
        "CUSTOMER_DOCUMENT",
    )
    assert all(
        component.status is RegistrationReadinessComponentStatus.COMPLETE
        for component in view.components
    )
    field_names = {field.name for field in fields(view)} | {
        field.name for field in fields(RegistrationReadinessComponentView)
    }
    assert {"id", "provider_status", "dispatch_status", "chat_id"}.isdisjoint(
        field_names
    )


def test_readiness_view_state_and_component_invariants_are_exact() -> None:
    with pytest.raises(ValueError, match="complete components"):
        RegistrationReadinessView(
            state=RegistrationReadinessState.READY_FOR_OTP,
            components=_components(
                incomplete=RegistrationReadinessComponent.CUSTOMER_DOCUMENT
            ),
        )
    with pytest.raises(ValueError, match="incomplete item"):
        RegistrationReadinessView(
            state=RegistrationReadinessState.INCOMPLETE,
            components=_components(),
        )
    with pytest.raises(ValueError, match="component set"):
        RegistrationReadinessView(
            state=RegistrationReadinessState.ACTIVE,
            components=_components()[:-1],
        )


@pytest.mark.integration
def test_readiness_adapters_lock_exact_minimum_snapshot_evidence(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001328",
        )
        current_acceptance = session.get(
            OfferAcceptance,
            snapshot.registration_offer_acceptance_id,
        )
        assert current_acceptance is not None
        second_text = OfferText(
            offer_version_id=current_acceptance.offer_version_id,
            language=OfferLanguage.RU.value,
            title="Synthetic registration offer",
            body="Synthetic registration offer body",
            content_hash="e" * 64,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(second_text)
        session.flush()
        earliest = OfferAcceptance(
            user_id=snapshot.user_id,
            offer_version_id=current_acceptance.offer_version_id,
            offer_text_id=second_text.id,
            purpose=OfferPurpose.REGISTRATION.value,
            language=OfferLanguage.RU.value,
            version_number=current_acceptance.version_number,
            content_hash=second_text.content_hash,
            accepted_at=NOW - timedelta(seconds=1),
            user_agent=None,
        )
        session.add(earliest)
        session.flush()

        acceptance_id = SqlAlchemyRegistrationOfferReadiness(
            session
        ).lock_earliest_exact_current_acceptance(
            actor_user_id=snapshot.user_id,
        )
        revision = SqlAlchemyCustomerIdentityReadiness(
            session,
            crypto_config=synthetic_identity_crypto_config(),
        ).lock_complete_identity_revision(customer_id=snapshot.customer_id)
        document_id = SqlAlchemyCustomerDocumentReadiness(
            session
        ).lock_current_available_document(customer_id=snapshot.customer_id)

        assert acceptance_id == earliest.id
        assert revision == snapshot.customer_identity_revision
        assert document_id == snapshot.customer_document_id


@pytest.mark.integration
def test_readiness_adapters_reject_cross_user_or_inexact_evidence(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        seed_registration_snapshot(
            session,
            phone="+998900001329",
        )

        assert (
            SqlAlchemyRegistrationOfferReadiness(
                session
            ).lock_earliest_exact_current_acceptance(actor_user_id=uuid4())
            is None
        )
        assert (
            SqlAlchemyCustomerIdentityReadiness(
                session,
                crypto_config=synthetic_identity_crypto_config(),
            ).lock_complete_identity_revision(customer_id=uuid4())
            is None
        )
        assert (
            SqlAlchemyCustomerDocumentReadiness(
                session
            ).lock_current_available_document(customer_id=uuid4())
            is None
        )


@pytest.mark.integration
def test_current_acceptance_selects_earliest_accepted_at_then_id(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001330",
        )
        current_acceptance = session.get(
            OfferAcceptance,
            snapshot.registration_offer_acceptance_id,
        )
        assert current_acceptance is not None
        second_text = OfferText(
            offer_version_id=current_acceptance.offer_version_id,
            language=OfferLanguage.RU.value,
            title="Synthetic registration offer RU",
            body="Synthetic registration offer body RU",
            content_hash="d" * 64,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(second_text)
        session.flush()
        lower_id = UUID("00000000-0000-4000-8000-000000000001")
        tied = OfferAcceptance(
            id=lower_id,
            user_id=snapshot.user_id,
            offer_version_id=current_acceptance.offer_version_id,
            offer_text_id=second_text.id,
            purpose=OfferPurpose.REGISTRATION.value,
            language=OfferLanguage.RU.value,
            version_number=current_acceptance.version_number,
            content_hash=second_text.content_hash,
            accepted_at=current_acceptance.accepted_at,
            user_agent=None,
        )
        session.add(tied)
        session.flush()

        selection = select_current_registration_acceptance(
            session,
            actor=CustomerActivationActor(snapshot.user_id),
        )

        assert isinstance(selection, CurrentRegistrationAcceptanceSelection)
        assert selection.succeeded
        assert selection.error is None
        assert selection.acceptance_id_for_snapshot() == lower_id
        assert str(lower_id) not in repr(selection)


@pytest.mark.integration
def test_current_acceptance_distinguishes_safe_unavailable_states(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001331",
        )
        current_acceptance = session.get(
            OfferAcceptance,
            snapshot.registration_offer_acceptance_id,
        )
        assert current_acceptance is not None
        session.delete(current_acceptance)
        session.flush()
        not_accepted = select_current_registration_acceptance(
            session,
            actor=CustomerActivationActor(snapshot.user_id),
        )
        current_version = session.get(
            OfferVersion,
            current_acceptance.offer_version_id,
        )
        assert current_version is not None
        current_version.status = OfferStatus.APPROVED.value
        current_version.current_by_user_id = None
        current_version.current_at = None
        session.flush()
        unavailable = select_current_registration_acceptance(
            session,
            actor=CustomerActivationActor(snapshot.user_id),
        )

        assert not_accepted.error is (
            RegistrationPrerequisiteError.REGISTRATION_OFFER_NOT_ACCEPTED
        )
        assert unavailable.error is RegistrationPrerequisiteError.OFFER_UNAVAILABLE
        assert not not_accepted.succeeded
        assert not unavailable.succeeded
        with pytest.raises(ValueError, match="was not selected"):
            not_accepted.acceptance_id_for_snapshot()


@pytest.mark.integration
def test_readiness_get_is_complete_and_zero_side_effect(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001328",
        )
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(m2_test_database, "before_cursor_execute", capture_statement)
    try:
        with Session(m2_test_database) as session:
            before = tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    Customer,
                    OtpChallenge,
                    OtpDispatch,
                    OtpChallengeEvent,
                    AuditLog,
                    AuthRateLimit,
                    AuthSession,
                )
            )
            statements.clear()
            readiness = get_registration_readiness(
                session,
                context=_activation_context(snapshot),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            readiness_statements = tuple(statements)
            after = tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    Customer,
                    OtpChallenge,
                    OtpDispatch,
                    OtpChallengeEvent,
                    AuditLog,
                    AuthRateLimit,
                    AuthSession,
                )
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_statement)

    assert readiness.state is RegistrationReadinessState.READY_FOR_OTP
    assert all(
        component.status is RegistrationReadinessComponentStatus.COMPLETE
        for component in readiness.components
    )
    assert before == after
    assert all("FOR UPDATE" not in statement for statement in readiness_statements)
    assert all(
        not statement.startswith(("INSERT ", "UPDATE ", "DELETE "))
        for statement in readiness_statements
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("missing_component", "mutation"),
    (
        (
            RegistrationReadinessComponent.TELEGRAM_LINK,
            "telegram_link",
        ),
        (
            RegistrationReadinessComponent.OFFER_ACCEPTANCE,
            "offer_acceptance",
        ),
        (
            RegistrationReadinessComponent.CUSTOMER_IDENTITY,
            "identity",
        ),
        (
            RegistrationReadinessComponent.CUSTOMER_DOCUMENT,
            "document",
        ),
    ),
)
def test_each_missing_readiness_gate_is_safe_and_side_effect_free(
    m2_test_database: Engine,
    missing_component: RegistrationReadinessComponent,
    mutation: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001328",
        )
        if mutation == "telegram_link":
            link = session.get(TelegramLink, snapshot.telegram_link_id)
            assert link is not None
            link.telegram_chat_id = None
            link.unlinked_at = NOW
            link.updated_at = NOW
        elif mutation == "offer_acceptance":
            acceptance = session.get(
                OfferAcceptance,
                snapshot.registration_offer_acceptance_id,
            )
            assert acceptance is not None
            session.delete(acceptance)
        elif mutation == "identity":
            identity = session.get(CustomerIdentity, snapshot.customer_id)
            assert identity is not None
            session.delete(identity)
        else:
            document = session.get(CustomerDocument, snapshot.customer_document_id)
            assert document is not None
            object_file = session.get(ObjectFile, document.object_file_id)
            assert object_file is not None
            object_file.status = ObjectFileStatus.DELETE_PENDING.value

    with Session(m2_test_database) as session:
        readiness = get_registration_readiness(
            session,
            context=_activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )

    status_by_component = {
        component.component: component.status for component in readiness.components
    }
    assert readiness.state is RegistrationReadinessState.INCOMPLETE
    assert (
        status_by_component[missing_component]
        is RegistrationReadinessComponentStatus.INCOMPLETE
    )


@pytest.mark.integration
def test_readiness_active_customer_is_terminal(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001328",
        )
        customer = session.get(Customer, snapshot.customer_id)
        assert customer is not None
        activation_time = NOW + timedelta(seconds=1)
        customer.onboarding_status = "active"
        customer.activated_at = activation_time
        customer.updated_at = activation_time

    with Session(m2_test_database) as session:
        before = tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                Customer,
                OtpChallenge,
                OtpDispatch,
                OtpChallengeEvent,
                AuditLog,
                AuthRateLimit,
                AuthSession,
            )
        )
        readiness = get_registration_readiness(
            session,
            context=_activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        after = tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                Customer,
                OtpChallenge,
                OtpDispatch,
                OtpChallengeEvent,
                AuditLog,
                AuthRateLimit,
                AuthSession,
            )
        )

    assert readiness.state is RegistrationReadinessState.ACTIVE
    assert before == after == (1, 0, 0, 0, 0, 0, 0)


@pytest.mark.integration
def test_readiness_missing_customer_never_creates_one(
    m2_test_database: Engine,
) -> None:
    phone = "+998900001329"
    with Session(m2_test_database) as session, session.begin():
        user = User(phone=phone, is_active=True)
        session.add(user)
        session.flush()
        user_id = user.id
    context = AuthenticatedActivationContext(
        actor=CustomerActivationActor(user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=UUID("77777777-7777-4777-8777-777777777777"),
            browser_binding_digest=OtpBrowserBindingDigest("7" * 64),
        ),
        trusted_client_ip=ResolvedClientIp("203.0.113.39"),
        _canonical_account_phone=phone,
    )

    with Session(m2_test_database) as session:
        before = session.scalar(select(func.count()).select_from(Customer))
        readiness = get_registration_readiness(
            session,
            context=context,
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        after = session.scalar(select(func.count()).select_from(Customer))

    assert readiness.state is RegistrationReadinessState.INCOMPLETE
    assert before == after == 0
