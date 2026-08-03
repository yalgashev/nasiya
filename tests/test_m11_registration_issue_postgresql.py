from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from inspect import getsource, signature
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.customer_activation.service as activation_service
from app.audit.models import AuditLog
from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.customer.models import Customer
from app.customer_activation.contracts import (
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    CustomerAlreadyActive,
    RegistrationOtpCooldown,
    RegistrationOtpPendingDelivery,
    RegistrationOtpPrerequisiteFailed,
    RegistrationOtpRateLimited,
    RegistrationPrerequisiteError,
    RegistrationReadinessSnapshot,
)
from app.customer_activation.rate_limit import RegistrationIssuanceRateLimitPolicy
from app.customer_activation.service import (
    AuthenticatedActivationContext,
    issue_registration_otp,
    request_new_registration_otp,
)
from app.customer_document.models import CustomerDocument
from app.customer_identity.crypto import (
    CustomerIdentityAesKey,
    CustomerIdentityBlindIndexKey,
    CustomerIdentityCryptoConfig,
    CustomerIdentityKeyId,
)
from app.customer_identity.models import CustomerIdentity
from app.offers.enums import OfferStatus
from app.offers.models import OfferAcceptance, OfferVersion
from app.otp.contracts import OtpChallengeEventAction, OtpChallengeStatus, OtpPurpose
from app.otp.crypto import OtpBrowserBindingDigest
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import (
    OtpChallengeInsertConflict,
    activate_challenge,
    consume_registration_challenge,
    create_pending_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
    invalidate_registration_challenge_for_state_change,
    lock_outstanding_challenge_set_by_user,
    lock_registration_candidate_set_by_browser,
    record_registration_failed_attempt,
    supersede_and_cancel_same_purpose_challenges,
)
from app.otp.web_presentation import OtpWebLanguage
from app.settings import Settings
from app.storage.models import ObjectFile, ObjectFileStatus
from app.telegram.bot_api import TelegramBotApiClient
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink
from tests.m11_seed import (
    NOW,
    REGISTRATION_DIGEST,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)

pytestmark = pytest.mark.integration
LOGIN_DIGEST = OtpBrowserBindingDigest("b" * 64)


def activation_context(
    snapshot: RegistrationReadinessSnapshot,
) -> AuthenticatedActivationContext:
    return activation_context_for(user_id=snapshot.user_id)


def activation_context_for(
    *,
    user_id: UUID,
    phone: str = "+998900001325",
) -> AuthenticatedActivationContext:
    return AuthenticatedActivationContext(
        actor=CustomerActivationActor(user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=user_id,
            browser_binding_digest=REGISTRATION_DIGEST,
        ),
        trusted_client_ip=ResolvedClientIp("203.0.113.41"),
        _canonical_account_phone=phone,
    )


def unavailable_identity_crypto_config() -> CustomerIdentityCryptoConfig:
    key_id = CustomerIdentityKeyId("identity-v2")
    return CustomerIdentityCryptoConfig(
        active_key_id=key_id,
        encryption_keys={
            key_id: CustomerIdentityAesKey.from_bytes(bytes(reversed(range(32))))
        },
        blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(bytes(range(32))),
    )


def capability_counts(session: Session) -> tuple[int, ...]:
    return tuple(
        session.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            OtpChallenge,
            OtpDispatch,
            OtpChallengeEvent,
            AuditLog,
            AuthSession,
        )
    )


def registration_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=(
            "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test"
        ),
        session_cookie_secure=False,
        rate_limit_hmac_key=("test-registration-rate-key-at-least-32-characters"),
    )


def create_registration_with_dispatch(
    session: Session,
    *,
    snapshot: RegistrationReadinessSnapshot,
    now: datetime,
) -> tuple[OtpChallenge, OtpDispatch]:
    challenge = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=now,
    )
    dispatch = create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=now,
    )
    return challenge, dispatch


def test_login_and_registration_coexist_without_cross_purpose_supersession(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001321",
        )
        registration, registration_dispatch = create_registration_with_dispatch(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        login = create_pending_challenge(
            session,
            browser_binding_digest=REGISTRATION_DIGEST,
            now=NOW,
            purpose=OtpPurpose.LOGIN,
            user_id=snapshot.user_id,
            telegram_link_id=snapshot.telegram_link_id,
            telegram_linked_at=snapshot.telegram_linked_at,
        )
        login_dispatch = create_pending_dispatch(
            session,
            challenge_id=login.id,
            locale="uz-Latn",
            now=NOW,
        )
        locked = lock_outstanding_challenge_set_by_user(
            session,
            user_id=snapshot.user_id,
            purpose=OtpPurpose.REGISTRATION,
        )
        candidate = lock_registration_candidate_set_by_browser(
            session,
            browser_binding_digest=REGISTRATION_DIGEST,
        )

        assert locked.challenges == candidate.challenges == (registration,)
        assert locked.dispatches == candidate.dispatches == (registration_dispatch,)
        assert (
            supersede_and_cancel_same_purpose_challenges(
                session,
                locked=locked,
                purpose=OtpPurpose.REGISTRATION,
                now=NOW + timedelta(seconds=1),
            )
            == 1
        )
        assert registration.status == OtpChallengeStatus.SUPERSEDED.value
        assert registration_dispatch.status == "CANCELLED"
        assert login.status == OtpChallengeStatus.PENDING_DISPATCH.value
        assert login_dispatch.status == "PENDING"
        event_actions = [
            event.action for event in session.scalars(select(OtpChallengeEvent))
        ]
        assert event_actions == [OtpChallengeEventAction.SUPERSEDED.value]


def test_registration_state_failed_attempt_burn_and_consume_events_are_exact(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001322",
        )
        stale, stale_dispatch = create_registration_with_dispatch(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        invalidate_registration_challenge_for_state_change(
            session,
            challenge=stale,
            dispatch=stale_dispatch,
            now=NOW + timedelta(seconds=1),
        )

        burned = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW + timedelta(seconds=2),
        )
        activate_challenge(
            session,
            challenge=burned,
            code_mac="e" * 64,
            activated_at=NOW + timedelta(seconds=3),
            expires_at=NOW + timedelta(minutes=3),
        )
        record_registration_failed_attempt(
            session,
            challenge=burned,
            now=NOW + timedelta(seconds=4),
            max_attempts=1,
        )

        consumed = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW + timedelta(seconds=5),
        )
        activate_challenge(
            session,
            challenge=consumed,
            code_mac="f" * 64,
            activated_at=NOW + timedelta(seconds=6),
            expires_at=NOW + timedelta(minutes=3),
        )
        consume_registration_challenge(
            session,
            challenge=consumed,
            now=NOW + timedelta(seconds=7),
        )

        assert stale.status == OtpChallengeStatus.INVALIDATED.value
        assert stale_dispatch.status == "CANCELLED"
        assert burned.status == OtpChallengeStatus.BURNED.value
        assert burned.failed_attempts == 1
        assert consumed.status == OtpChallengeStatus.CONSUMED.value
        assert [
            event.action
            for event in session.scalars(
                select(OtpChallengeEvent).order_by(OtpChallengeEvent.occurred_at)
            )
        ] == [
            OtpChallengeEventAction.INVALIDATED_BY_REGISTRATION_STATE_CHANGE.value,
            OtpChallengeEventAction.VERIFY_FAILED.value,
            OtpChallengeEventAction.BURNED.value,
            OtpChallengeEventAction.CONSUMED.value,
        ]


def test_registration_context_validation_and_expected_conflict_keep_session_usable(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001323",
        )
        with pytest.raises(ValueError, match="registration context"):
            create_pending_challenge(
                session,
                browser_binding_digest=LOGIN_DIGEST,
                now=NOW,
                purpose=OtpPurpose.LOGIN,
                customer_id=snapshot.customer_id,
            )
        with pytest.raises(ValueError, match="real identity snapshot"):
            create_pending_challenge(
                session,
                browser_binding_digest=REGISTRATION_DIGEST,
                now=NOW,
                purpose=OtpPurpose.REGISTRATION,
            )

        create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        conflicting_snapshot = RegistrationReadinessSnapshot(
            user_id=snapshot.user_id,
            customer_id=snapshot.customer_id,
            telegram_link_id=snapshot.telegram_link_id,
            telegram_linked_at=snapshot.telegram_linked_at,
            registration_offer_acceptance_id=(
                snapshot.registration_offer_acceptance_id
            ),
            customer_identity_revision=snapshot.customer_identity_revision,
            customer_document_id=snapshot.customer_document_id,
            browser_binding_digest=OtpBrowserBindingDigest("9" * 64),
        )
        with pytest.raises(OtpChallengeInsertConflict):
            create_pending_registration_challenge(
                session,
                snapshot=conflicting_snapshot,
                now=NOW + timedelta(seconds=1),
            )

        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(OtpChallenge)) == 1
        assert (
            "challenge_id"
            not in signature(lock_registration_candidate_set_by_browser).parameters
        )
        assert (
            "purpose"
            not in signature(lock_registration_candidate_set_by_browser).parameters
        )


def test_runtime_lock_trace_is_dispatch_then_challenge(
    m2_test_database: Engine,
) -> None:
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
        with Session(m2_test_database) as session, session.begin():
            snapshot = seed_registration_snapshot(
                session,
                phone="+998900001324",
            )
            create_registration_with_dispatch(
                session,
                snapshot=snapshot,
                now=NOW,
            )
            session.flush()
            statements.clear()

            lock_registration_candidate_set_by_browser(
                session,
                browser_binding_digest=REGISTRATION_DIGEST,
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_statement)

    row_locks = [statement for statement in statements if "FOR UPDATE" in statement]
    dispatch_position = next(
        index
        for index, statement in enumerate(row_locks)
        if "otp_dispatches" in statement
    )
    challenge_position = next(
        index
        for index, statement in enumerate(row_locks)
        if "otp_challenges" in statement
    )
    assert dispatch_position < challenge_position


def test_registration_issue_atomically_creates_snapshot_dispatch_and_event(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )

    with Session(m2_test_database) as session, session.begin():
        result = issue_registration_otp(
            session,
            context=activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )

    with Session(m2_test_database) as session:
        challenges = tuple(session.scalars(select(OtpChallenge)))
        dispatches = tuple(session.scalars(select(OtpDispatch)))
        events = tuple(session.scalars(select(OtpChallengeEvent)))

    assert isinstance(result, RegistrationOtpPendingDelivery)
    assert len(challenges) == len(dispatches) == len(events) == 1
    challenge = challenges[0]
    dispatch = dispatches[0]
    event_row = events[0]
    assert challenge.purpose == OtpPurpose.REGISTRATION.value
    assert challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert challenge.code_mac is None
    assert challenge.user_id == snapshot.user_id
    assert challenge.customer_id == snapshot.customer_id
    assert (
        challenge.registration_offer_acceptance_id
        == snapshot.registration_offer_acceptance_id
    )
    assert challenge.customer_identity_revision == (
        snapshot.customer_identity_revision.value
    )
    assert challenge.customer_document_id == snapshot.customer_document_id
    assert challenge.browser_binding_digest == (
        snapshot.browser_binding_digest.as_stored_value()
    )
    assert dispatch.challenge_id == challenge.id
    assert dispatch.status == "PENDING"
    assert dispatch.locale == "uz-Latn"
    assert event_row.challenge_id == challenge.id
    assert event_row.action == OtpChallengeEventAction.ISSUED.value
    assert event_row.safe_code is None


def test_registration_issue_not_ready_creates_zero_capability(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
        acceptance = session.get(
            OfferAcceptance,
            snapshot.registration_offer_acceptance_id,
        )
        assert acceptance is not None
        session.delete(acceptance)

    with Session(m2_test_database) as session, session.begin():
        result = issue_registration_otp(
            session,
            context=activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.RU,
            now=NOW + timedelta(seconds=1),
        )

    with Session(m2_test_database) as session:
        counts = tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (OtpChallenge, OtpDispatch, OtpChallengeEvent)
        )

    assert isinstance(result, RegistrationOtpPrerequisiteFailed)
    assert result.error is (
        RegistrationPrerequisiteError.REGISTRATION_OFFER_NOT_ACCEPTED
    )
    assert counts == (0, 0, 0)


def test_registration_issue_runtime_lock_order_is_forward(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
        create_registration_with_dispatch(
            session,
            snapshot=snapshot,
            now=NOW,
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
        normalized = " ".join(statement.split())
        if "FOR UPDATE" in normalized:
            statements.append(normalized)

    event.listen(m2_test_database, "before_cursor_execute", capture_statement)
    try:
        with Session(m2_test_database) as session, session.begin():
            result = issue_registration_otp(
                session,
                context=activation_context(snapshot),
                identity_crypto_config=synthetic_identity_crypto_config(),
                language=OtpWebLanguage.UZ_LATN,
                now=NOW + timedelta(seconds=1),
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_statement)

    assert isinstance(result, RegistrationOtpPendingDelivery)
    tables = (
        "otp_dispatches",
        "otp_challenges",
        "users",
        "telegram_links",
        "customers",
        "offer_versions",
        "offer_acceptances",
        "customer_identities",
        "object_files",
        "customer_documents",
    )
    positions = [
        next(index for index, statement in enumerate(statements) if table in statement)
        for table in tables
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        pytest.param(
            "inactive_user",
            RegistrationPrerequisiteError.CUSTOMER_DRAFT_REQUIRED,
            id="T-M11-014-user-inactive",
        ),
        pytest.param("active_customer", None, id="T-M11-014-customer-active"),
        pytest.param(
            "no_link",
            RegistrationPrerequisiteError.TELEGRAM_NOT_LINKED,
            id="T-M11-014-link-missing",
        ),
        pytest.param(
            "unverified_link",
            RegistrationPrerequisiteError.TELEGRAM_PHONE_NOT_VERIFIED,
            id="T-M11-014-link-phone-unverified",
        ),
        pytest.param(
            "cross_owner_link",
            RegistrationPrerequisiteError.TELEGRAM_NOT_LINKED,
            id="T-M11-014-link-cross-owner",
        ),
        pytest.param(
            "no_current_offer",
            RegistrationPrerequisiteError.OFFER_UNAVAILABLE,
            id="T-M11-014-offer-unavailable",
        ),
        pytest.param(
            "no_exact_acceptance",
            RegistrationPrerequisiteError.REGISTRATION_OFFER_NOT_ACCEPTED,
            id="T-M11-014-acceptance-missing",
        ),
        pytest.param(
            "missing_identity",
            RegistrationPrerequisiteError.CUSTOMER_IDENTITY_UNAVAILABLE,
            id="T-M11-014-identity-missing",
        ),
        pytest.param(
            "tampered_identity",
            RegistrationPrerequisiteError.CUSTOMER_IDENTITY_UNAVAILABLE,
            id="T-M11-014-identity-tampered",
        ),
        pytest.param(
            "key_unavailable_identity",
            RegistrationPrerequisiteError.CUSTOMER_IDENTITY_UNAVAILABLE,
            id="T-M11-014-identity-key-unavailable",
        ),
        pytest.param(
            "missing_document",
            RegistrationPrerequisiteError.CUSTOMER_DOCUMENT_UNAVAILABLE,
            id="T-M11-014-document-missing",
        ),
        pytest.param(
            "non_current_document",
            RegistrationPrerequisiteError.CUSTOMER_DOCUMENT_UNAVAILABLE,
            id="T-M11-014-document-non-current",
        ),
        pytest.param(
            "non_available_object",
            RegistrationPrerequisiteError.CUSTOMER_DOCUMENT_UNAVAILABLE,
            id="T-M11-014-object-non-available",
        ),
    ),
)
def test_registration_issue_failure_matrix_is_zero_capability(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    case: str,
    expected_error: RegistrationPrerequisiteError | None,
) -> None:
    provider_calls: list[object] = []

    async def provider_canary(*args: object, **kwargs: object) -> object:
        provider_calls.append((args, kwargs))
        raise AssertionError("Issue failure reached Telegram network boundary")

    monkeypatch.setattr(TelegramBotApiClient, "send_message", provider_canary)
    with Session(m2_test_database) as session:
        transaction = session.begin()
        try:
            snapshot = seed_registration_snapshot(
                session,
                phone="+998900001325",
            )
            crypto_config = synthetic_identity_crypto_config()
            if case == "inactive_user":
                user = session.get(User, snapshot.user_id)
                assert user is not None
                user.is_active = False
                user.updated_at = NOW + timedelta(seconds=1)
            elif case == "active_customer":
                customer = session.get(Customer, snapshot.customer_id)
                assert customer is not None
                customer.onboarding_status = "active"
                customer.activated_at = NOW + timedelta(seconds=1)
                customer.updated_at = NOW + timedelta(seconds=1)
            elif case == "no_link":
                link = session.get(TelegramLink, snapshot.telegram_link_id)
                assert link is not None
                link.telegram_chat_id = None
                link.unlinked_at = NOW + timedelta(seconds=1)
                link.phone_verified_at = None
                link.updated_at = NOW + timedelta(seconds=1)
            elif case == "unverified_link":
                link = session.get(TelegramLink, snapshot.telegram_link_id)
                assert link is not None
                link.phone_verified_at = None
                link.updated_at = NOW + timedelta(seconds=1)
            elif case == "cross_owner_link":
                other_user = User(
                    phone="+998900001499",
                    password_hash=None,
                    is_active=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(other_user)
                session.flush()
                link = session.get(TelegramLink, snapshot.telegram_link_id)
                assert link is not None
                link.user_id = other_user.id
            elif case == "no_current_offer":
                acceptance = session.get(
                    OfferAcceptance,
                    snapshot.registration_offer_acceptance_id,
                )
                assert acceptance is not None
                version = session.get(OfferVersion, acceptance.offer_version_id)
                assert version is not None
                version.status = OfferStatus.APPROVED.value
                version.current_by_user_id = None
                version.current_at = None
            elif case == "no_exact_acceptance":
                acceptance = session.get(
                    OfferAcceptance,
                    snapshot.registration_offer_acceptance_id,
                )
                assert acceptance is not None
                session.delete(acceptance)
            elif case == "missing_identity":
                identity = session.get(CustomerIdentity, snapshot.customer_id)
                assert identity is not None
                session.delete(identity)
            elif case == "tampered_identity":
                identity = session.get(CustomerIdentity, snapshot.customer_id)
                assert identity is not None
                identity.ciphertext = bytes([identity.ciphertext[0] ^ 1]) + bytes(
                    identity.ciphertext[1:]
                )
                identity.updated_at = NOW + timedelta(seconds=1)
            elif case == "key_unavailable_identity":
                crypto_config = unavailable_identity_crypto_config()
            elif case == "missing_document":
                document = session.get(CustomerDocument, snapshot.customer_document_id)
                assert document is not None
                session.delete(document)
            elif case == "non_current_document":
                document = session.get(CustomerDocument, snapshot.customer_document_id)
                assert document is not None
                source_object = session.get(ObjectFile, document.object_file_id)
                assert source_object is not None
                replacement_object_id = uuid4()
                replacement_object = ObjectFile(
                    id=replacement_object_id,
                    bucket=source_object.bucket,
                    object_key=f"v1/objects/{replacement_object_id.hex}.png",
                    content_type=source_object.content_type,
                    size_bytes=source_object.size_bytes,
                    checksum_sha256="e" * 64,
                    width_px=source_object.width_px,
                    height_px=source_object.height_px,
                    status=ObjectFileStatus.AVAILABLE.value,
                    created_by_user_id=snapshot.user_id,
                    created_at=NOW,
                    updated_at=NOW,
                    available_at=NOW,
                )
                replacement = CustomerDocument(
                    customer_id=snapshot.customer_id,
                    object_file_id=replacement_object.id,
                    submission_id=uuid4(),
                    status="SUPERSEDED",
                    attached_by_user_id=snapshot.user_id,
                    attached_at=NOW,
                    superseded_by_document_id=document.id,
                    superseded_at=NOW + timedelta(seconds=1),
                )
                session.add(replacement_object)
                session.flush()
                session.add(replacement)
                session.flush()
                document.status = "SUPERSEDED"
                document.superseded_by_document_id = replacement.id
                document.superseded_at = NOW + timedelta(seconds=1)
            else:
                document = session.get(CustomerDocument, snapshot.customer_document_id)
                assert document is not None
                object_file = session.get(ObjectFile, document.object_file_id)
                assert object_file is not None
                object_file.status = ObjectFileStatus.DELETE_PENDING.value
            session.flush()

            result = issue_registration_otp(
                session,
                context=activation_context(snapshot),
                identity_crypto_config=crypto_config,
                language=OtpWebLanguage.UZ_LATN,
                now=NOW + timedelta(seconds=2),
            )
            counts = capability_counts(session)
        finally:
            transaction.rollback()

    if case == "active_customer":
        assert isinstance(result, CustomerAlreadyActive)
    else:
        assert isinstance(result, RegistrationOtpPrerequisiteFailed)
        assert result.error is expected_error
        rendered = repr(result)
        for forbidden in (
            str(snapshot.customer_id),
            str(snapshot.registration_offer_acceptance_id),
            str(snapshot.customer_document_id),
            str(snapshot.customer_identity_revision.value),
        ):
            assert forbidden not in rendered
    assert counts == (0, 0, 0, 0, 0)
    assert provider_calls == []
    assert "TelegramBotApiClient" not in getsource(issue_registration_otp)


def test_registration_issue_missing_customer_never_creates_one(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user = User(
            phone="+998900001325",
            password_hash=None,
            is_active=True,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(user)
        session.flush()
        link = TelegramLink(
            user_id=user.id,
            telegram_chat_id=9_980_001_325,
            linked_at=NOW,
            phone_verified_at=NOW,
            updated_at=NOW,
        )
        session.add(link)
        session.flush()
        user_id = user.id

    with Session(m2_test_database) as session, session.begin():
        result = issue_registration_otp(
            session,
            context=activation_context_for(user_id=user_id),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )
        customer_count = session.scalar(select(func.count()).select_from(Customer))
        counts = capability_counts(session)

    assert isinstance(result, RegistrationOtpPrerequisiteFailed)
    assert result.error is RegistrationPrerequisiteError.CUSTOMER_DRAFT_REQUIRED
    assert customer_count == 0
    assert counts == (0, 0, 0, 0, 0)


def test_registration_rate_rejection_preserves_attempts_and_skips_domain(
    m2_test_database: Engine,
) -> None:
    settings = registration_settings()
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
    outcomes = []
    for offset in range(5):
        with Session(m2_test_database) as session, session.begin():
            user = session.get(User, snapshot.user_id)
            assert user is not None
            outcomes.append(
                RegistrationIssuanceRateLimitPolicy(
                    session=session,
                    settings=settings,
                ).check_and_record(
                    current_user=user,
                    client_ip=ResolvedClientIp("203.0.113.41"),
                    now=NOW + timedelta(seconds=offset),
                )
            )

    with Session(m2_test_database) as session:
        rate_counts = tuple(
            row.attempt_count for row in session.scalars(select(AuthRateLimit))
        )
        counts = capability_counts(session)

    assert [result.allowed for result in outcomes] == [True, True, True, False, False]
    assert outcomes[-1].error_code is not None
    assert rate_counts == (4, 4, 4)
    assert counts == (0, 0, 0, 0, 0)


def test_registration_issue_repository_failure_rolls_back_all_capability(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )

    def fail_event_append(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic safe repository failure")

    monkeypatch.setattr(
        activation_service,
        "append_challenge_event",
        fail_event_append,
    )
    with pytest.raises(RuntimeError, match="synthetic safe repository failure"):
        with Session(m2_test_database) as session, session.begin():
            issue_registration_otp(
                session,
                context=activation_context(snapshot),
                identity_crypto_config=synthetic_identity_crypto_config(),
                language=OtpWebLanguage.UZ_LATN,
                now=NOW + timedelta(seconds=1),
            )

    with Session(m2_test_database) as session:
        assert capability_counts(session) == (0, 0, 0, 0, 0)


def test_sequential_registration_issue_supersedes_only_registration(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
        login = create_pending_challenge(
            session,
            browser_binding_digest=REGISTRATION_DIGEST,
            now=NOW,
            purpose=OtpPurpose.LOGIN,
            user_id=snapshot.user_id,
            telegram_link_id=snapshot.telegram_link_id,
            telegram_linked_at=snapshot.telegram_linked_at,
        )
        login_dispatch = create_pending_dispatch(
            session,
            challenge_id=login.id,
            locale="uz-Latn",
            now=NOW,
        )
        login_id = login.id
        login_dispatch_id = login_dispatch.id

    for offset in (1, 2):
        with Session(m2_test_database) as session, session.begin():
            result = issue_registration_otp(
                session,
                context=activation_context(snapshot),
                identity_crypto_config=synthetic_identity_crypto_config(),
                language=OtpWebLanguage.UZ_LATN,
                now=NOW + timedelta(seconds=offset),
            )
            assert isinstance(result, RegistrationOtpPendingDelivery)

    with Session(m2_test_database) as session:
        login_row = session.get(OtpChallenge, login_id)
        login_dispatch_row = session.get(OtpDispatch, login_dispatch_id)
        registrations = tuple(
            session.scalars(
                select(OtpChallenge)
                .where(OtpChallenge.purpose == OtpPurpose.REGISTRATION.value)
                .order_by(OtpChallenge.created_at, OtpChallenge.id)
            )
        )
        registration_dispatches = tuple(
            session.scalars(
                select(OtpDispatch)
                .join(OtpChallenge, OtpChallenge.id == OtpDispatch.challenge_id)
                .where(OtpChallenge.purpose == OtpPurpose.REGISTRATION.value)
                .order_by(OtpDispatch.created_at, OtpDispatch.id)
            )
        )
        event_actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).order_by(
                    OtpChallengeEvent.occurred_at,
                    OtpChallengeEvent.id,
                )
            )
        )

    assert login_row is not None
    assert login_row.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert login_dispatch_row is not None
    assert login_dispatch_row.status == "PENDING"
    assert [row.status for row in registrations] == ["SUPERSEDED", "PENDING_DISPATCH"]
    assert [row.status for row in registration_dispatches] == ["CANCELLED", "PENDING"]
    assert len(event_actions) == 3
    assert event_actions.count(OtpChallengeEventAction.ISSUED.value) == 2
    assert event_actions.count(OtpChallengeEventAction.SUPERSEDED.value) == 1


def test_concurrent_registration_issue_has_one_durable_winner(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
    original_lock = activation_service.lock_outstanding_challenge_set_by_user
    discovered = Barrier(2)

    def synchronized_lock(*args: object, **kwargs: object) -> object:
        locked = original_lock(*args, **kwargs)
        discovered.wait(timeout=5)
        return locked

    monkeypatch.setattr(
        activation_service,
        "lock_outstanding_challenge_set_by_user",
        synchronized_lock,
    )

    def issue() -> RegistrationOtpPendingDelivery:
        with Session(m2_test_database) as session, session.begin():
            result = issue_registration_otp(
                session,
                context=activation_context(snapshot),
                identity_crypto_config=synthetic_identity_crypto_config(),
                language=OtpWebLanguage.UZ_LATN,
                now=NOW + timedelta(seconds=1),
            )
            assert isinstance(result, RegistrationOtpPendingDelivery)
            return result

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(issue) for _ in range(2)]
        completed, pending = wait(futures, timeout=10)
        assert not pending
        assert len(completed) == 2
        assert all(
            isinstance(future.result(), RegistrationOtpPendingDelivery)
            for future in completed
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        challenge_ids = tuple(session.scalars(select(OtpChallenge.id)))
        dispatch_challenge_ids = tuple(
            session.scalars(select(OtpDispatch.challenge_id))
        )
        event_challenge_ids = tuple(
            session.scalars(select(OtpChallengeEvent.challenge_id))
        )

    assert len(challenge_ids) == 1
    assert dispatch_challenge_ids == challenge_ids
    assert event_challenge_ids == challenge_ids


def test_new_code_cooldown_and_supersession_are_registration_local(
    m2_test_database: Engine,
) -> None:
    factory = sessionmaker(bind=m2_test_database, expire_on_commit=False)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
    with Session(m2_test_database) as session, session.begin():
        initial = issue_registration_otp(
            session,
            context=activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )
    assert isinstance(initial, RegistrationOtpPendingDelivery)

    before_boundary = request_new_registration_otp(
        factory,
        context=activation_context(snapshot),
        settings=registration_settings(),
        identity_crypto_config=synthetic_identity_crypto_config(),
        language=OtpWebLanguage.RU,
        now=NOW + timedelta(seconds=60, microseconds=999_999),
    )
    with Session(m2_test_database) as session:
        before_challenges = tuple(session.scalars(select(OtpChallenge)))
        before_dispatches = tuple(session.scalars(select(OtpDispatch)))
        before_events = tuple(session.scalars(select(OtpChallengeEvent)))

    at_boundary = request_new_registration_otp(
        factory,
        context=activation_context(snapshot),
        settings=registration_settings(),
        identity_crypto_config=synthetic_identity_crypto_config(),
        language=OtpWebLanguage.RU,
        now=NOW + timedelta(seconds=61),
    )
    with Session(m2_test_database) as session:
        challenges = tuple(
            session.scalars(select(OtpChallenge).order_by(OtpChallenge.created_at))
        )
        dispatches = tuple(
            session.scalars(select(OtpDispatch).order_by(OtpDispatch.created_at))
        )
        event_actions = tuple(session.scalars(select(OtpChallengeEvent.action)))

    assert isinstance(before_boundary, RegistrationOtpCooldown)
    assert len(before_challenges) == len(before_dispatches) == len(before_events) == 1
    assert before_challenges[0].status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert isinstance(at_boundary, RegistrationOtpPendingDelivery)
    assert [challenge.status for challenge in challenges] == [
        OtpChallengeStatus.SUPERSEDED.value,
        OtpChallengeStatus.PENDING_DISPATCH.value,
    ]
    assert [dispatch.status for dispatch in dispatches] == ["CANCELLED", "PENDING"]
    assert event_actions.count(OtpChallengeEventAction.ISSUED.value) == 2
    assert event_actions.count(OtpChallengeEventAction.SUPERSEDED.value) == 1
    assert all(challenge.code_mac is None for challenge in challenges)


def test_new_registration_code_cross_owner_preserves_existing_capability(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    factory = sessionmaker(bind=m2_test_database, expire_on_commit=False)
    provider_calls: list[object] = []

    async def provider_canary(*args: object, **kwargs: object) -> object:
        provider_calls.append((args, kwargs))
        raise AssertionError("Cross-owner new-code reached Telegram transport")

    monkeypatch.setattr(TelegramBotApiClient, "send_message", provider_canary)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
        issued = issue_registration_otp(
            session,
            context=activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )
        assert isinstance(issued, RegistrationOtpPendingDelivery)
        challenge = session.scalar(select(OtpChallenge))
        dispatch = session.scalar(select(OtpDispatch))
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        assert challenge is not None
        assert dispatch is not None
        assert link is not None
        other_user = User(
            phone="+998900001498",
            password_hash=None,
            is_active=True,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(other_user)
        session.flush()
        link.user_id = other_user.id
        link.updated_at = NOW + timedelta(seconds=2)
        session.flush()
        challenge_id = challenge.id
        dispatch_id = dispatch.id
        expected_challenge = (
            challenge.status,
            challenge.failed_attempts,
            challenge.code_mac,
            challenge.activated_at,
            challenge.expires_at,
            challenge.consumed_at,
            challenge.terminal_at,
            challenge.updated_at,
        )
        expected_dispatch = (
            dispatch.status,
            dispatch.claimed_at,
            dispatch.prepared_at,
            dispatch.sent_at,
            dispatch.terminal_at,
            dispatch.failure_code,
            dispatch.updated_at,
        )
        expected_link = (
            link.user_id,
            link.telegram_chat_id,
            link.linked_at,
            link.phone_verified_at,
            link.unlinked_at,
            link.updated_at,
        )

    result = request_new_registration_otp(
        factory,
        context=activation_context(snapshot),
        settings=registration_settings(),
        identity_crypto_config=synthetic_identity_crypto_config(),
        language=OtpWebLanguage.RU,
        now=NOW + timedelta(seconds=62),
    )

    with Session(m2_test_database) as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        customer = session.get(Customer, snapshot.customer_id)
        events = tuple(
            session.scalars(
                select(OtpChallengeEvent)
                .where(OtpChallengeEvent.challenge_id == challenge_id)
                .order_by(
                    OtpChallengeEvent.occurred_at.asc(),
                    OtpChallengeEvent.id.asc(),
                )
            )
        )
        rates = tuple(session.scalars(select(AuthRateLimit)))
        counts = capability_counts(session)
        assert challenge is not None
        assert dispatch is not None
        assert link is not None
        assert customer is not None

    assert result == RegistrationOtpPrerequisiteFailed(
        RegistrationPrerequisiteError.TELEGRAM_NOT_LINKED
    )
    assert (
        challenge.status,
        challenge.failed_attempts,
        challenge.code_mac,
        challenge.activated_at,
        challenge.expires_at,
        challenge.consumed_at,
        challenge.terminal_at,
        challenge.updated_at,
    ) == expected_challenge
    assert (
        dispatch.status,
        dispatch.claimed_at,
        dispatch.prepared_at,
        dispatch.sent_at,
        dispatch.terminal_at,
        dispatch.failure_code,
        dispatch.updated_at,
    ) == expected_dispatch
    assert (
        link.user_id,
        link.telegram_chat_id,
        link.linked_at,
        link.phone_verified_at,
        link.unlinked_at,
        link.updated_at,
    ) == expected_link
    assert [(row.action, row.safe_code) for row in events] == [
        (OtpChallengeEventAction.ISSUED.value, None)
    ]
    assert customer.onboarding_status == "draft"
    assert len(rates) == 3
    assert {row.attempt_count for row in rates} == {1}
    assert counts == (1, 1, 1, 0, 0)
    assert provider_calls == []


def test_new_registration_code_rate_limit_precedes_cooldown_and_is_not_refunded(
    m2_test_database: Engine,
) -> None:
    factory = sessionmaker(bind=m2_test_database, expire_on_commit=False)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
    with Session(m2_test_database) as session, session.begin():
        issue_registration_otp(
            session,
            context=activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )

    results = tuple(
        request_new_registration_otp(
            factory,
            context=activation_context(snapshot),
            settings=registration_settings(),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=2 + offset),
        )
        for offset in range(5)
    )
    with Session(m2_test_database) as session:
        rate_attempts = tuple(
            row.attempt_count for row in session.scalars(select(AuthRateLimit))
        )
        counts = capability_counts(session)

    assert all(isinstance(result, RegistrationOtpCooldown) for result in results[:3])
    assert all(isinstance(result, RegistrationOtpRateLimited) for result in results[3:])
    assert rate_attempts == (4, 4, 4)
    assert counts == (1, 1, 1, 0, 0)


def test_new_registration_code_active_customer_is_noop_before_cooldown(
    m2_test_database: Engine,
) -> None:
    factory = sessionmaker(bind=m2_test_database, expire_on_commit=False)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
    with Session(m2_test_database) as session, session.begin():
        issue_registration_otp(
            session,
            context=activation_context(snapshot),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )
    with Session(m2_test_database) as session, session.begin():
        customer = session.get(Customer, snapshot.customer_id)
        assert customer is not None
        customer.onboarding_status = "active"
        customer.activated_at = NOW + timedelta(seconds=2)
        customer.updated_at = NOW + timedelta(seconds=2)

    result = request_new_registration_otp(
        factory,
        context=activation_context(snapshot),
        settings=registration_settings(),
        identity_crypto_config=synthetic_identity_crypto_config(),
        language=OtpWebLanguage.UZ_LATN,
        now=NOW + timedelta(seconds=3),
    )
    with Session(m2_test_database) as session:
        challenge = session.scalar(select(OtpChallenge))
        counts = capability_counts(session)

    assert isinstance(result, CustomerAlreadyActive)
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert counts == (1, 1, 1, 0, 0)


def test_concurrent_new_code_and_issue_converge_to_one_capability(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    factory = sessionmaker(bind=m2_test_database, expire_on_commit=False)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001325",
        )
    original_lock = activation_service.lock_outstanding_challenge_set_by_user
    discovered = Barrier(2)

    def synchronized_lock(*args: object, **kwargs: object) -> object:
        locked = original_lock(*args, **kwargs)
        discovered.wait(timeout=5)
        return locked

    monkeypatch.setattr(
        activation_service,
        "lock_outstanding_challenge_set_by_user",
        synchronized_lock,
    )

    def initial_issue() -> object:
        with Session(m2_test_database) as session, session.begin():
            return issue_registration_otp(
                session,
                context=activation_context(snapshot),
                identity_crypto_config=synthetic_identity_crypto_config(),
                language=OtpWebLanguage.UZ_LATN,
                now=NOW + timedelta(seconds=1),
            )

    def new_code() -> object:
        return request_new_registration_otp(
            factory,
            context=activation_context(snapshot),
            settings=registration_settings(),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = (executor.submit(initial_issue), executor.submit(new_code))
        completed, pending = wait(futures, timeout=10)
        assert not pending
        assert all(
            isinstance(future.result(), RegistrationOtpPendingDelivery)
            for future in completed
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        assert capability_counts(session) == (1, 1, 1, 0, 0)


def test_concurrent_multi_browser_issue_is_user_purpose_capped(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001399",
        )
    first_context = activation_context(snapshot)
    second_context = AuthenticatedActivationContext(
        actor=CustomerActivationActor(snapshot.user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=UUID("44444444-4444-4444-8444-444444444444"),
            browser_binding_digest=OtpBrowserBindingDigest("d" * 64),
        ),
        trusted_client_ip=ResolvedClientIp("203.0.113.42"),
        _canonical_account_phone="+998900001399",
    )
    original_lock = activation_service.lock_outstanding_challenge_set_by_user
    discovered = Barrier(2)

    def synchronized_lock(*args: object, **kwargs: object) -> object:
        locked = original_lock(*args, **kwargs)
        discovered.wait(timeout=5)
        return locked

    monkeypatch.setattr(
        activation_service,
        "lock_outstanding_challenge_set_by_user",
        synchronized_lock,
    )

    def issue(context: AuthenticatedActivationContext) -> object:
        with Session(m2_test_database) as session, session.begin():
            return issue_registration_otp(
                session,
                context=context,
                identity_crypto_config=synthetic_identity_crypto_config(),
                language=OtpWebLanguage.UZ_LATN,
                now=NOW + timedelta(seconds=1),
            )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = (
            executor.submit(issue, first_context),
            executor.submit(issue, second_context),
        )
        completed, pending = wait(futures, timeout=10)
        assert not pending
        assert all(
            isinstance(future.result(), RegistrationOtpPendingDelivery)
            for future in completed
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        assert capability_counts(session) == (1, 1, 1, 0, 0)
        challenge = session.scalar(select(OtpChallenge))

    assert challenge is not None
    assert challenge.browser_binding_digest in {
        REGISTRATION_DIGEST.as_stored_value(),
        second_context.browser.browser_binding_digest.as_stored_value(),
    }
