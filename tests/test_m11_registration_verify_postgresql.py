from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from inspect import getsource
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer_activation.service as activation_service_module
import app.otp.repository as otp_repository_module
from app.audit.contracts import AuditEvent
from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import create_authenticated_session, resolve_by_raw_token
from app.customer.models import Customer
from app.customer.ports import CustomerLifecycleStatus
from app.customer.repository import transition_existing_own_customer_draft_to_active
from app.customer_activation.contracts import (
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    PreparedCustomerActivation,
    RegistrationOtpCandidateLookupKey,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    RegistrationReadinessSnapshot,
    VerifyRegistrationOtp,
    mark_customer_activation_committed,
    parse_registration_otp_candidate,
)
from app.customer_activation.service import (
    CustomerActivationSessionUnavailable,
    RegistrationSnapshotRecheckOutcome,
    RegistrationSnapshotRecheckResult,
    ResolvedRegistrationOtpCandidate,
    check_registration_otp_candidate_code,
    check_registration_otp_input_boundary,
    recheck_registration_activation_snapshot,
    resolve_and_recheck_registration_otp_candidate,
    resolve_registration_otp_candidate,
    verify_and_activate_registration_customer,
)
from app.customer_document.contracts import (
    CustomerDocumentActor,
    CustomerDocumentSubmissionId,
    ExpectedCurrentCustomerDocument,
    UploadOwnCustomerDocument,
)
from app.customer_document.coordinator import (
    CustomerDocumentServiceError,
    upload_and_attach_own_customer_document,
)
from app.customer_document.models import CustomerDocument
from app.customer_identity.contracts import CustomerIdentityActor, SaveCustomerIdentity
from app.customer_identity.crypto import (
    CustomerIdentityAesKey,
    CustomerIdentityBlindIndexKey,
    CustomerIdentityCryptoConfig,
    CustomerIdentityKeyId,
)
from app.customer_identity.models import CustomerIdentity
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.service import (
    CustomerIdentityServiceError,
    save_own_customer_identity,
)
from app.db import create_database_session_factory
from app.offers.authorization import PlatformAdminActor, require_platform_admin_actor
from app.offers.content import canonicalize_offer_text, compute_offer_content_hash
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance, OfferText, OfferVersion
from app.offers.service import make_offer_version_current
from app.otp.code import OtpCode
from app.otp.contracts import OtpChallengeEventAction, OtpChallengeStatus, OtpPurpose
from app.otp.crypto import OtpBrowserBindingDigest, compute_otp_code_mac
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import (
    activate_challenge,
    burn_challenge,
    consume_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
    expire_challenge,
    invalidate_challenge,
    supersede_challenge,
)
from app.settings import Settings
from app.shop.models import Shop
from app.storage.models import ObjectFile, ObjectFileStatus
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink
from tests.m11_seed import (
    NOW as SEED_NOW,
)
from tests.m11_seed import (
    REGISTRATION_DIGEST,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)
from tests.storage_fake import FakeObjectStorageService

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
_DIGEST = "c" * 64
_NOW = datetime(2026, 8, 2, 12, 30, tzinfo=UTC)
_OTP_HMAC_KEY = SecretStr("m11-synthetic-registration-otp-hmac-key")
_RATE_HMAC_KEY = "m11-synthetic-rate-limit-key-at-least-32-characters"


def _actor() -> CustomerActivationActor:
    return CustomerActivationActor(_USER_ID)


def _browser() -> CustomerActivationBrowserContext:
    return CustomerActivationBrowserContext(
        current_session_id=_SESSION_ID,
        browser_binding_digest=OtpBrowserBindingDigest(_DIGEST),
    )


def _settings(engine: Engine, *, max_attempts: int = 5) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=_RATE_HMAC_KEY,
        otp_hmac_key=_OTP_HMAC_KEY,
        otp_registration_max_verify_attempts=max_attempts,
    )


def _verify_command(
    *,
    user_id: UUID,
    digest: OtpBrowserBindingDigest = REGISTRATION_DIGEST,
    code: str = "004271",
    now: datetime = _NOW,
    current_session_id: UUID | None = None,
) -> VerifyRegistrationOtp:
    return VerifyRegistrationOtp(
        actor=CustomerActivationActor(user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=current_session_id or uuid4(),
            browser_binding_digest=digest,
        ),
        candidate_code=code,
        now=now,
    )


def _create_active_registration_candidate(
    session: Session,
    *,
    snapshot: RegistrationReadinessSnapshot,
    code: str = "004271",
    expires_at: datetime = _NOW + timedelta(minutes=3),
) -> tuple[OtpChallenge, OtpDispatch]:
    challenge = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=SEED_NOW,
    )
    dispatch = create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=SEED_NOW,
    )
    typed_code = OtpCode(code)
    activate_challenge(
        session,
        challenge=challenge,
        code_mac=compute_otp_code_mac(
            otp_hmac_key=_OTP_HMAC_KEY,
            challenge_id=challenge.id,
            user_id=snapshot.user_id,
            purpose=OtpPurpose.REGISTRATION,
            code=typed_code,
        ),
        activated_at=SEED_NOW + timedelta(seconds=1),
        expires_at=expires_at,
    )
    return challenge, dispatch


def _create_approved_registration_switch_target(
    session: Session,
    *,
    snapshot: RegistrationReadinessSnapshot,
    admin_phone: str,
) -> tuple[PlatformAdminActor, UUID, UUID]:
    admin = User(
        phone=admin_phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=True,
        created_at=SEED_NOW,
        updated_at=SEED_NOW,
    )
    session.add(admin)
    session.flush()
    actor = require_platform_admin_actor(admin)
    acceptance = session.get(
        OfferAcceptance,
        snapshot.registration_offer_acceptance_id,
    )
    assert acceptance is not None
    target = OfferVersion(
        purpose=OfferPurpose.REGISTRATION.value,
        version_number=2,
        status=OfferStatus.APPROVED.value,
        created_by_user_id=admin.id,
        created_at=SEED_NOW,
        legal_review_authority="Synthetic Legal",
        legal_reviewed_at=SEED_NOW,
        legal_review_reference="M11-SYNTHETIC-SWITCH",
        approved_by_user_id=admin.id,
        approved_at=SEED_NOW,
        current_by_user_id=None,
        current_at=None,
    )
    session.add(target)
    session.flush()
    for language, marker in (
        (OfferLanguage.UZ_LATN, "1"),
        (OfferLanguage.UZ_CYRL, "2"),
        (OfferLanguage.RU, "3"),
    ):
        title = f"Synthetic {language.value} registration offer"
        body = f"Synthetic {language.value} registration body {marker}"
        session.add(
            OfferText(
                offer_version_id=target.id,
                language=language.value,
                title=title,
                body=body,
                content_hash=compute_offer_content_hash(
                    canonicalize_offer_text(title=title, body=body)
                ),
                created_at=SEED_NOW,
                updated_at=SEED_NOW,
            )
        )
    session.flush()
    return actor, acceptance.offer_version_id, target.id


def test_verify_command_has_only_server_context_candidate_string_and_time() -> None:
    command = VerifyRegistrationOtp(
        actor=_actor(),
        browser=_browser(),
        candidate_code="004271",
        now=_NOW,
    )

    assert tuple(field.name for field in fields(command)) == (
        "actor",
        "browser",
        "candidate_code",
        "now",
    )
    assert {
        "purpose",
        "customer_id",
        "challenge_id",
        "dispatch_id",
        "telegram_link_id",
        "acceptance_id",
        "identity_id",
        "document_id",
        "object_file_id",
        "session_id",
    }.isdisjoint(field.name for field in fields(command))


def test_verify_command_repr_redacts_code_actor_session_digest_and_time() -> None:
    command = VerifyRegistrationOtp(
        actor=_actor(),
        browser=_browser(),
        candidate_code="004271",
        now=_NOW,
    )
    rendered = repr(command)

    for forbidden in (
        "004271",
        str(_USER_ID),
        str(_SESSION_ID),
        _DIGEST,
        _NOW.isoformat(),
    ):
        assert forbidden not in rendered
    assert "candidate_code=<redacted>" in rendered


@pytest.mark.parametrize("raw_code", ["000000", " 004271\n", "999999"])
def test_registration_candidate_parser_accepts_exact_ascii_code(raw_code: str) -> None:
    candidate = parse_registration_otp_candidate(raw_code)

    assert candidate.requires_dummy_mac is False
    assert candidate.code is not None
    assert candidate.code.as_internal_value() == raw_code.strip()
    assert raw_code.strip() not in repr(candidate)


@pytest.mark.parametrize(
    "raw_code",
    ["", "12345", "1234567", "１２３４５６", "123 456", "ABC123"],
)
def test_malformed_registration_candidate_becomes_redacted_dummy_contract(
    raw_code: str,
) -> None:
    candidate = parse_registration_otp_candidate(raw_code)

    assert candidate.requires_dummy_mac is True
    assert candidate.code is None
    if raw_code:
        assert raw_code not in repr(candidate)


def test_candidate_lookup_key_is_browser_bound_and_internal_registration_only() -> None:
    key = RegistrationOtpCandidateLookupKey(
        browser_binding_digest=OtpBrowserBindingDigest(_DIGEST)
    )

    assert tuple(field.name for field in fields(key)) == (
        "browser_binding_digest",
        "purpose",
    )
    assert key.purpose is OtpPurpose.REGISTRATION
    assert _DIGEST not in repr(key)
    with pytest.raises(TypeError):
        RegistrationOtpCandidateLookupKey(  # type: ignore[call-arg]
            browser_binding_digest=OtpBrowserBindingDigest(_DIGEST),
            purpose=OtpPurpose.LOGIN,
        )


def test_verification_outcomes_are_exact_safe_and_detail_free() -> None:
    assert tuple(outcome.value for outcome in RegistrationOtpVerificationOutcome) == (
        "ACTIVATED",
        "ALREADY_ACTIVE",
        "OTP_INVALID",
        "CUSTOMER_ACTIVATION_CHANGED",
        "RATE_LIMITED",
        "SESSION_EXPIRED",
        "PREREQUISITE_FAILED",
    )
    for outcome in RegistrationOtpVerificationOutcome:
        result = RegistrationOtpVerificationResult(outcome)
        assert tuple(field.name for field in fields(result)) == ("outcome",)
        rendered = repr(result)
        for forbidden in (
            "challenge_id",
            "attempt",
            "gate",
            "customer_id",
            "provider",
        ):
            assert forbidden not in rendered


def test_verify_contract_rejects_untyped_or_naive_values_without_echo() -> None:
    with pytest.raises(TypeError):
        VerifyRegistrationOtp(
            actor=_actor(),
            browser=_browser(),
            candidate_code=4271,  # type: ignore[arg-type]
            now=_NOW,
        )
    with pytest.raises(ValueError):
        VerifyRegistrationOtp(
            actor=_actor(),
            browser=_browser(),
            candidate_code="sensitive-malformed-candidate",
            now=datetime(2026, 8, 2, 11, 30),
        )


@pytest.mark.parametrize(
    "candidate_input",
    ["", "12345", "1234567", "123-456", "abcdef", "１２３４５６"],
)
def test_malformed_verify_input_runs_one_neutral_mac_shape_and_is_generic(
    candidate_input: str,
) -> None:
    dummy_calls: list[OtpCode | None] = []

    def record_dummy(_key: SecretStr, candidate: OtpCode | None) -> None:
        dummy_calls.append(candidate)

    result = check_registration_otp_input_boundary(
        VerifyRegistrationOtp(
            actor=_actor(),
            browser=_browser(),
            candidate_code=candidate_input,
            now=_NOW,
        ),
        otp_hmac_key=_OTP_HMAC_KEY,
        dummy_work=record_dummy,
    )

    assert result == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )
    assert dummy_calls == [None]
    if candidate_input:
        assert candidate_input not in repr(result)


def test_valid_verify_input_preserves_leading_zero_without_integer_conversion() -> None:
    dummy_calls: list[OtpCode | None] = []

    result = check_registration_otp_input_boundary(
        VerifyRegistrationOtp(
            actor=_actor(),
            browser=_browser(),
            candidate_code="004271",
            now=_NOW,
        ),
        otp_hmac_key=_OTP_HMAC_KEY,
        dummy_work=lambda _key, candidate: dummy_calls.append(candidate),
    )

    assert result.code is not None
    assert result.code.as_internal_value() == "004271"
    assert dummy_calls == []
    source = getsource(check_registration_otp_input_boundary)
    assert "int(" not in source
    assert "challenge_id" not in source
    assert "customer_id" not in source


def test_default_malformed_path_executes_neutral_compare_without_echo() -> None:
    result = check_registration_otp_input_boundary(
        VerifyRegistrationOtp(
            actor=_actor(),
            browser=_browser(),
            candidate_code="synthetic-malformed-candidate",
            now=_NOW,
        ),
        otp_hmac_key=_OTP_HMAC_KEY,
    )

    assert result.outcome is RegistrationOtpVerificationOutcome.OTP_INVALID
    assert "synthetic-malformed-candidate" not in repr(result)


@pytest.mark.integration
def test_registration_candidate_resolves_by_browser_and_server_purpose_only(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001341",
        )
        challenge, dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id
        dispatch_id = dispatch.id

    with Session(m2_test_database) as session, session.begin():
        result = resolve_registration_otp_candidate(
            session,
            command=_verify_command(user_id=snapshot.user_id),
            settings=_settings(m2_test_database),
        )

        assert isinstance(result, ResolvedRegistrationOtpCandidate)
        assert result.challenge.id == challenge_id
        assert result.dispatch is not None and result.dispatch.id == dispatch_id
        assert result.code.as_internal_value() == "004271"
        rendered = repr(result)
        assert "004271" not in rendered
        assert str(challenge_id) not in rendered
        assert str(dispatch_id) not in rendered


@pytest.mark.integration
def test_missing_candidate_rechecks_server_owned_active_customer_as_completed(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001342",
        )
        transition_existing_own_customer_draft_to_active(
            session,
            actor_user_id=snapshot.user_id,
            expected_status=CustomerLifecycleStatus.DRAFT,
            now=SEED_NOW + timedelta(minutes=1),
        )

    dummy_calls: list[OtpCode | None] = []
    with Session(m2_test_database) as session, session.begin():
        result = resolve_registration_otp_candidate(
            session,
            command=_verify_command(user_id=snapshot.user_id),
            settings=_settings(m2_test_database),
            dummy_work=lambda _key, candidate: dummy_calls.append(candidate),
        )

    assert result == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.ALREADY_ACTIVE
    )
    assert dummy_calls == []


@pytest.mark.integration
def test_missing_and_cross_browser_candidates_are_same_generic_invalid(
    m2_test_database: Engine,
) -> None:
    other_digest = OtpBrowserBindingDigest("d" * 64)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001343",
        )
        other_snapshot = replace(
            snapshot,
            browser_binding_digest=other_digest,
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=other_snapshot,
        )
        challenge_id = challenge.id

    outcomes: list[RegistrationOtpVerificationResult] = []
    dummy_calls: list[str] = []
    for digest in (REGISTRATION_DIGEST, OtpBrowserBindingDigest("e" * 64)):
        with Session(m2_test_database) as session, session.begin():
            result = resolve_registration_otp_candidate(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    digest=digest,
                ),
                settings=_settings(m2_test_database),
                dummy_work=lambda _key, candidate: dummy_calls.append(
                    candidate.as_internal_value()
                    if candidate is not None
                    else "missing"
                ),
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            outcomes.append(result)

    assert (
        outcomes
        == [
            RegistrationOtpVerificationResult(
                RegistrationOtpVerificationOutcome.OTP_INVALID
            )
        ]
        * 2
    )
    assert dummy_calls == ["004271", "004271"]
    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        assert stored is not None
        assert stored.status == OtpChallengeStatus.ACTIVE.value


@pytest.mark.integration
@pytest.mark.parametrize(
    "terminalizer,phone",
    [
        (consume_challenge, "+998900001344"),
        (supersede_challenge, "+998900001345"),
        (expire_challenge, "+998900001346"),
        (burn_challenge, "+998900001347"),
        (invalidate_challenge, "+998900001348"),
    ],
)
def test_terminal_registration_candidates_share_generic_invalid_mapping(
    m2_test_database: Engine,
    terminalizer: Callable[..., OtpChallenge],
    phone: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone=phone,
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        terminalizer(
            session,
            challenge=challenge,
            now=_NOW - timedelta(seconds=1),
        )

    dummy_calls: list[OtpCode | None] = []
    with Session(m2_test_database) as session, session.begin():
        result = resolve_registration_otp_candidate(
            session,
            command=_verify_command(user_id=snapshot.user_id),
            settings=_settings(m2_test_database),
            dummy_work=lambda _key, candidate: dummy_calls.append(candidate),
        )

    assert result == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )
    assert len(dummy_calls) == 1
    assert dummy_calls[0] is not None
    assert dummy_calls[0].as_internal_value() == "004271"


@pytest.mark.integration
@pytest.mark.parametrize("invalid_class", ["pending", "expired", "attempt_limit"])
def test_non_active_expired_and_attempt_limit_candidates_are_generic_invalid(
    m2_test_database: Engine,
    invalid_class: str,
) -> None:
    phone_suffix = {"pending": 49, "expired": 50, "attempt_limit": 51}[invalid_class]
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone=f"+9989000013{phone_suffix}",
        )
        if invalid_class == "pending":
            create_pending_registration_challenge(
                session,
                snapshot=snapshot,
                now=SEED_NOW,
            )
        else:
            challenge, _dispatch = _create_active_registration_candidate(
                session,
                snapshot=snapshot,
                expires_at=(
                    _NOW if invalid_class == "expired" else _NOW + timedelta(minutes=3)
                ),
            )
            if invalid_class == "attempt_limit":
                challenge.failed_attempts = 1

    with Session(m2_test_database) as session, session.begin():
        result = resolve_registration_otp_candidate(
            session,
            command=_verify_command(user_id=snapshot.user_id),
            settings=_settings(
                m2_test_database,
                max_attempts=1 if invalid_class == "attempt_limit" else 5,
            ),
        )

    assert result == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )


@pytest.mark.integration
def test_live_snapshot_recheck_is_exact_and_runtime_lock_order_is_forward(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001352",
        )
        _create_active_registration_candidate(session, snapshot=snapshot)

    row_locks: list[str] = []

    def capture_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.split())
        if "FOR UPDATE" in normalized:
            row_locks.append(normalized)

    event.listen(m2_test_database, "before_cursor_execute", capture_lock)
    try:
        with Session(m2_test_database) as session, session.begin():
            command = _verify_command(user_id=snapshot.user_id)
            candidate = resolve_registration_otp_candidate(
                session,
                command=command,
                settings=_settings(m2_test_database),
            )
            assert isinstance(candidate, ResolvedRegistrationOtpCandidate)
            result = recheck_registration_activation_snapshot(
                session,
                command=command,
                candidate=candidate,
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_lock)

    assert result == RegistrationSnapshotRecheckResult(
        outcome=RegistrationSnapshotRecheckOutcome.READY,
        candidate=candidate,
    )
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
        next(index for index, statement in enumerate(row_locks) if table in statement)
        for table in tables
    ]
    assert positions == sorted(positions)


@pytest.mark.integration
@pytest.mark.parametrize(
    "changed_part,phone,expected",
    [
        (
            "user",
            "+998900001353",
            RegistrationSnapshotRecheckOutcome.REGISTRATION_STATE_CHANGED,
        ),
        (
            "link",
            "+998900001354",
            RegistrationSnapshotRecheckOutcome.LINK_CHANGED,
        ),
        (
            "customer",
            "+998900001355",
            RegistrationSnapshotRecheckOutcome.REGISTRATION_STATE_CHANGED,
        ),
        (
            "offer",
            "+998900001356",
            RegistrationSnapshotRecheckOutcome.REGISTRATION_STATE_CHANGED,
        ),
        (
            "identity",
            "+998900001357",
            RegistrationSnapshotRecheckOutcome.REGISTRATION_STATE_CHANGED,
        ),
        (
            "object",
            "+998900001358",
            RegistrationSnapshotRecheckOutcome.REGISTRATION_STATE_CHANGED,
        ),
    ],
)
def test_each_live_snapshot_gate_fails_closed_without_otp_mutation(
    m2_test_database: Engine,
    changed_part: str,
    phone: str,
    expected: RegistrationSnapshotRecheckOutcome,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(session, phone=phone)
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id
        if changed_part == "user":
            session.get(User, snapshot.user_id).is_active = False
        elif changed_part == "link":
            link = session.get(TelegramLink, snapshot.telegram_link_id)
            assert link is not None
            link.linked_at = link.linked_at + timedelta(seconds=1)
        elif changed_part == "customer":
            customer = session.get(Customer, snapshot.customer_id)
            assert customer is not None
            customer.onboarding_status = "active"
            customer.activated_at = _NOW
            customer.updated_at = _NOW
        elif changed_part == "offer":
            acceptance = session.get(
                OfferAcceptance,
                snapshot.registration_offer_acceptance_id,
            )
            assert acceptance is not None
            acceptance.content_hash = "f" * 64
        elif changed_part == "identity":
            identity = session.get(CustomerIdentity, snapshot.customer_id)
            assert identity is not None
            identity.revision += 1
            identity.updated_at = _NOW
        elif changed_part == "object":
            document = session.get(CustomerDocument, snapshot.customer_document_id)
            assert document is not None
            object_file = session.get(ObjectFile, document.object_file_id)
            assert object_file is not None
            object_file.status = ObjectFileStatus.DELETE_PENDING.value
        else:
            raise AssertionError("Unknown synthetic snapshot mutation")

    with Session(m2_test_database) as session, session.begin():
        command = _verify_command(user_id=snapshot.user_id)
        candidate = resolve_registration_otp_candidate(
            session,
            command=command,
            settings=_settings(m2_test_database),
        )
        assert isinstance(candidate, ResolvedRegistrationOtpCandidate)
        result = recheck_registration_activation_snapshot(
            session,
            command=command,
            candidate=candidate,
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        stored = session.get(OtpChallenge, challenge_id)
        assert stored is not None
        stored_snapshot = (stored.status, stored.failed_attempts)

    assert result.outcome is expected
    assert result.candidate is candidate
    assert stored_snapshot == (OtpChallengeStatus.ACTIVE.value, 0)


@pytest.mark.integration
@pytest.mark.parametrize(
    "changed_part,phone,expected_action",
    [
        (
            "link",
            "+998900001359",
            "INVALIDATED_BY_LINK_CHANGE",
        ),
        (
            "offer",
            "+998900001360",
            "INVALIDATED_BY_REGISTRATION_STATE_CHANGE",
        ),
    ],
)
def test_snapshot_mismatch_invalidates_once_without_attempt_or_activation(
    m2_test_database: Engine,
    changed_part: str,
    phone: str,
    expected_action: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(session, phone=phone)
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id
        if changed_part == "link":
            link = session.get(TelegramLink, snapshot.telegram_link_id)
            assert link is not None
            link.linked_at = link.linked_at + timedelta(seconds=1)
        else:
            acceptance = session.get(
                OfferAcceptance,
                snapshot.registration_offer_acceptance_id,
            )
            assert acceptance is not None
            acceptance.content_hash = "f" * 64

    command = _verify_command(user_id=snapshot.user_id)
    with Session(m2_test_database) as session, session.begin():
        first = resolve_and_recheck_registration_otp_candidate(
            session,
            command=command,
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )

    with Session(m2_test_database) as session, session.begin():
        second = resolve_and_recheck_registration_otp_candidate(
            session,
            command=command,
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        session_count = session.scalar(select(func.count()).select_from(AuthSession))
        challenge_count = session.scalar(select(func.count()).select_from(OtpChallenge))
        assert stored is not None
        assert customer is not None
        stored_snapshot = (stored.status, stored.failed_attempts)
        customer_status = customer.onboarding_status

    assert first == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED
    )
    assert second == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )
    assert stored_snapshot == (
        OtpChallengeStatus.INVALIDATED.value,
        0,
    )
    assert actions == (expected_action,)
    assert customer_status == "draft"
    assert audit_count == session_count == 0
    assert challenge_count == 1


@pytest.mark.integration
def test_parallel_snapshot_mismatch_has_one_invalidation_event(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001361",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        assert link is not None
        link.linked_at = link.linked_at + timedelta(seconds=1)

    start = Barrier(2)

    def attempt() -> RegistrationOtpVerificationOutcome:
        with Session(m2_test_database) as session, session.begin():
            start.wait(timeout=5)
            result = resolve_and_recheck_registration_otp_candidate(
                session,
                command=_verify_command(user_id=snapshot.user_id),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            return result.outcome

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(attempt) for _ in range(2)]
        completed, pending = wait(futures, timeout=10)
        assert not pending
        outcomes = sorted(result.result().value for result in completed)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcomes == sorted(
        [
            RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED.value,
            RegistrationOtpVerificationOutcome.OTP_INVALID.value,
        ]
    )
    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        assert stored is not None
        stored_snapshot = (stored.status, stored.failed_attempts)
    assert stored_snapshot == (
        OtpChallengeStatus.INVALIDATED.value,
        0,
    )
    assert actions == ("INVALIDATED_BY_LINK_CHANGE",)


@pytest.mark.integration
def test_correct_code_is_ready_only_after_snapshot_recheck_and_has_no_mutation(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001362",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id

    with Session(m2_test_database) as session, session.begin():
        result = check_registration_otp_candidate_code(
            session,
            command=_verify_command(user_id=snapshot.user_id),
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        stored = session.get(OtpChallenge, challenge_id)
        event_count = session.scalar(
            select(func.count()).select_from(OtpChallengeEvent)
        )
        assert stored is not None
        stored_snapshot = (stored.status, stored.failed_attempts)

    assert isinstance(result, RegistrationSnapshotRecheckResult)
    assert result.outcome is RegistrationSnapshotRecheckOutcome.READY
    assert stored_snapshot == (OtpChallengeStatus.ACTIVE.value, 0)
    assert event_count == 0


@pytest.mark.integration
def test_five_wrong_codes_increment_safely_then_burn_once(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001363",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id

    outcomes: list[RegistrationOtpVerificationOutcome] = []
    dummy_calls: list[str] = []

    def record_dummy(_key: SecretStr, candidate: OtpCode | None) -> None:
        dummy_calls.append(
            candidate.as_internal_value() if candidate is not None else "missing"
        )

    for _attempt in range(5):
        with Session(m2_test_database) as session, session.begin():
            result = check_registration_otp_candidate_code(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    code="000000",
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
                dummy_work=record_dummy,
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            outcomes.append(result.outcome)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action)
                .where(OtpChallengeEvent.challenge_id == challenge_id)
                .order_by(OtpChallengeEvent.occurred_at, OtpChallengeEvent.id)
            )
        )
        customer = session.get(Customer, snapshot.customer_id)
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        session_count = session.scalar(select(func.count()).select_from(AuthSession))
        assert stored is not None
        assert customer is not None
        stored_snapshot = (stored.status, stored.failed_attempts)
        customer_status = customer.onboarding_status

    assert outcomes == [RegistrationOtpVerificationOutcome.OTP_INVALID] * 5
    assert dummy_calls == ["000000"] * 5
    assert stored_snapshot == (OtpChallengeStatus.BURNED.value, 5)
    assert len(actions) == 6
    assert actions.count(OtpChallengeEventAction.VERIFY_FAILED.value) == 5
    assert actions.count(OtpChallengeEventAction.BURNED.value) == 1
    assert customer_status == "draft"
    assert audit_count == session_count == 0


@pytest.mark.integration
def test_expiry_boundary_is_now_greater_or_equal_and_never_counts_attempt(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001364",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
            expires_at=_NOW,
        )
        challenge_id = challenge.id

    with Session(m2_test_database) as session, session.begin():
        result = check_registration_otp_candidate_code(
            session,
            command=_verify_command(user_id=snapshot.user_id, now=_NOW),
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        assert stored is not None
        stored_snapshot = (stored.status, stored.failed_attempts)

    assert result == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )
    assert stored_snapshot == (OtpChallengeStatus.EXPIRED.value, 0)
    assert actions == (OtpChallengeEventAction.EXPIRED.value,)


@pytest.mark.integration
def test_parallel_wrong_and_correct_code_serialize_deterministically(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001365",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id

    start = Barrier(2)

    def attempt(code: str) -> str:
        with Session(m2_test_database) as session, session.begin():
            start.wait(timeout=5)
            result = check_registration_otp_candidate_code(
                session,
                command=_verify_command(user_id=snapshot.user_id, code=code),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            if isinstance(result, RegistrationSnapshotRecheckResult):
                return result.outcome.value
            return result.outcome.value

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(attempt, code) for code in ("004271", "000000")]
        completed, pending = wait(futures, timeout=10)
        assert not pending
        outcomes = sorted(result.result() for result in completed)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcomes == sorted(
        [
            RegistrationSnapshotRecheckOutcome.READY.value,
            RegistrationOtpVerificationOutcome.OTP_INVALID.value,
        ]
    )
    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        assert stored is not None
        stored_snapshot = (stored.status, stored.failed_attempts)
    assert stored_snapshot == (OtpChallengeStatus.ACTIVE.value, 1)
    assert actions == (OtpChallengeEventAction.VERIFY_FAILED.value,)


@pytest.mark.integration
def test_correct_code_atomically_consumes_activates_and_audits_exact_payload(
    m2_test_database: Engine,
) -> None:
    assert Shop.__tablename__ == "shops"
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001366",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        other = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-other-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id
        current_raw_token = current.raw_token
        current_csrf = current.session.csrf_secret
        other_session_id = other.session.id
        other_token_hash = other.session.token_hash

    with Session(m2_test_database) as session, session.begin():
        prepared = verify_and_activate_registration_customer(
            session,
            command=_verify_command(
                user_id=snapshot.user_id,
                current_session_id=current_session_id,
            ),
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        assert isinstance(prepared, PreparedCustomerActivation)
    committed = mark_customer_activation_committed(prepared)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        events = tuple(
            session.scalars(
                select(OtpChallengeEvent).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        audits = tuple(session.scalars(select(AuditLog)))
        old_resolution = resolve_by_raw_token(session, current_raw_token, _NOW)
        new_resolution = resolve_by_raw_token(
            session,
            committed.release_cookie_token(),
            _NOW,
        )
        old_session = session.get(AuthSession, current_session_id)
        other_session = session.get(AuthSession, other_session_id)
        auth_session_count = session.scalar(
            select(func.count()).select_from(AuthSession)
        )
        assert stored is not None
        assert customer is not None
        stored_snapshot = (
            stored.status,
            stored.failed_attempts,
            stored.consumed_at,
        )
        customer_snapshot = (
            customer.onboarding_status,
            customer.activated_at,
            customer.updated_at,
        )

    assert stored_snapshot == (OtpChallengeStatus.CONSUMED.value, 0, _NOW)
    assert customer_snapshot == ("active", _NOW, _NOW)
    assert [(event.action, event.safe_code) for event in events] == [
        (OtpChallengeEventAction.CONSUMED.value, None)
    ]
    assert len(audits) == 1
    assert audits[0].event_type == "customer.activated"
    assert audits[0].object_type == "customer"
    assert audits[0].object_id == snapshot.customer_id
    assert audits[0].actor_user_id == snapshot.user_id
    assert audits[0].payload == {
        "from_status": "draft",
        "to_status": "active",
        "activation_method": "TELEGRAM_REGISTRATION_OTP",
    }
    assert auth_session_count == 3
    assert old_resolution is None
    assert old_session is not None and old_session.revoked_at == _NOW
    assert new_resolution is not None
    assert new_resolution.session.user_id == snapshot.user_id
    assert new_resolution.session.csrf_secret != current_csrf
    assert other_session is not None
    assert other_session.revoked_at is None
    assert other_session.token_hash == other_token_hash
    rendered = repr(prepared) + repr(committed)
    for forbidden in (
        str(snapshot.customer_id),
        str(snapshot.registration_offer_acceptance_id),
        str(snapshot.customer_document_id),
        "004271",
    ):
        assert forbidden not in rendered


@pytest.mark.integration
@pytest.mark.parametrize(
    "failure_stage,phone",
    [
        ("otp_event", "+998900001367"),
        ("central_audit", "+998900001368"),
    ],
)
def test_event_or_audit_failure_rolls_back_entire_activation(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    failure_stage: str,
    phone: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(session, phone=phone)
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic activation write failure")

    if failure_stage == "otp_event":
        monkeypatch.setattr(
            otp_repository_module,
            "append_challenge_event",
            fail_write,
        )
    else:
        monkeypatch.setattr(
            activation_service_module,
            "append_audit_event",
            fail_write,
        )

    with pytest.raises(RuntimeError, match="synthetic activation write failure"):
        with Session(m2_test_database) as session, session.begin():
            verify_and_activate_registration_customer(
                session,
                command=_verify_command(user_id=snapshot.user_id),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        event_count = session.scalar(
            select(func.count()).select_from(OtpChallengeEvent)
        )
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        assert stored is not None
        assert customer is not None
        stored_snapshot = (
            stored.status,
            stored.failed_attempts,
            stored.consumed_at,
        )
        customer_snapshot = (
            customer.onboarding_status,
            customer.activated_at,
        )

    assert stored_snapshot == (OtpChallengeStatus.ACTIVE.value, 0, None)
    assert customer_snapshot == ("draft", None)
    assert event_count == audit_count == 0


@pytest.mark.integration
def test_missing_current_session_rolls_back_activation_before_commit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001369",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        challenge_id = challenge.id

    with pytest.raises(CustomerActivationSessionUnavailable):
        with Session(m2_test_database) as session, session.begin():
            verify_and_activate_registration_customer(
                session,
                command=_verify_command(user_id=snapshot.user_id),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        event_count = session.scalar(
            select(func.count()).select_from(OtpChallengeEvent)
        )
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        auth_session_count = session.scalar(
            select(func.count()).select_from(AuthSession)
        )
        assert stored is not None
        assert customer is not None
        stored_snapshot = (stored.status, stored.consumed_at)
        customer_snapshot = (
            customer.onboarding_status,
            customer.activated_at,
        )

    assert stored_snapshot == (OtpChallengeStatus.ACTIVE.value, None)
    assert customer_snapshot == ("draft", None)
    assert event_count == audit_count == auth_session_count == 0


@pytest.mark.integration
def test_cookie_preparation_failure_before_commit_rolls_back_rotation_and_activation(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001370",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id
        current_token_hash = current.session.token_hash

    with pytest.raises(RuntimeError, match="synthetic cookie preparation failure"):
        with Session(m2_test_database) as session, session.begin():
            prepared = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(prepared, PreparedCustomerActivation)
            raise RuntimeError("synthetic cookie preparation failure")

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        current = session.get(AuthSession, current_session_id)
        event_count = session.scalar(
            select(func.count()).select_from(OtpChallengeEvent)
        )
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        auth_session_count = session.scalar(
            select(func.count()).select_from(AuthSession)
        )
        assert stored is not None
        assert customer is not None
        assert current is not None
        stored_snapshot = (stored.status, stored.consumed_at)
        customer_snapshot = (
            customer.onboarding_status,
            customer.activated_at,
        )
        current_snapshot = (current.revoked_at, current.token_hash)

    assert stored_snapshot == (OtpChallengeStatus.ACTIVE.value, None)
    assert customer_snapshot == ("draft", None)
    assert current_snapshot == (None, current_token_hash)
    assert event_count == audit_count == 0
    assert auth_session_count == 1


@pytest.mark.integration
def test_sequential_activation_replay_is_already_active_exact_noop(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001371",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id

    command = _verify_command(
        user_id=snapshot.user_id,
        current_session_id=current_session_id,
    )
    with Session(m2_test_database) as session, session.begin():
        first = verify_and_activate_registration_customer(
            session,
            command=command,
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        assert isinstance(first, PreparedCustomerActivation)

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        assert customer is not None
        first_state = (
            customer.onboarding_status,
            customer.activated_at,
            customer.updated_at,
        )
        first_counts = (
            session.scalar(select(func.count()).select_from(OtpChallengeEvent)),
            session.scalar(select(func.count()).select_from(AuditLog)),
            session.scalar(select(func.count()).select_from(AuthSession)),
        )

    with Session(m2_test_database) as session, session.begin():
        replay = verify_and_activate_registration_customer(
            session,
            command=command,
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        assert stored is not None
        assert customer is not None
        replay_state = (
            customer.onboarding_status,
            customer.activated_at,
            customer.updated_at,
        )
        replay_counts = (
            session.scalar(select(func.count()).select_from(OtpChallengeEvent)),
            session.scalar(select(func.count()).select_from(AuditLog)),
            session.scalar(select(func.count()).select_from(AuthSession)),
        )
        challenge_state = (stored.status, stored.consumed_at)

    assert replay == RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.ALREADY_ACTIVE
    )
    assert not hasattr(replay, "release_cookie_token")
    assert first_state == replay_state == ("active", _NOW, _NOW)
    assert first_counts == replay_counts == (1, 1, 2)
    assert challenge_state == (OtpChallengeStatus.CONSUMED.value, _NOW)


@pytest.mark.integration
def test_parallel_correct_verify_has_one_activation_and_idempotent_loser(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001372",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id

    start = Barrier(2)

    def attempt() -> str:
        with Session(m2_test_database) as session, session.begin():
            start.wait(timeout=5)
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            if isinstance(result, PreparedCustomerActivation):
                return RegistrationOtpVerificationOutcome.ACTIVATED.value
            return result.outcome.value

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(attempt) for _request in range(2)]
        completed, pending = wait(futures, timeout=10)
        assert not pending
        outcomes = sorted(result.result() for result in completed)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcomes == sorted(
        [
            RegistrationOtpVerificationOutcome.ACTIVATED.value,
            RegistrationOtpVerificationOutcome.ALREADY_ACTIVE.value,
        ]
    )
    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        old_session = session.get(AuthSession, current_session_id)
        events = tuple(session.scalars(select(OtpChallengeEvent)))
        audits = tuple(session.scalars(select(AuditLog)))
        active_sessions = tuple(
            session.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == snapshot.user_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > _NOW,
                )
            )
        )
        all_session_count = session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == snapshot.user_id)
        )
        assert stored is not None
        assert customer is not None
        assert old_session is not None
        stored_snapshot = (stored.status, stored.consumed_at)
        customer_snapshot = (
            customer.onboarding_status,
            customer.activated_at,
            customer.updated_at,
        )
        old_revoked_at = old_session.revoked_at
        event_snapshots = tuple((event.action, event.safe_code) for event in events)
        audit_types = tuple(audit.event_type for audit in audits)
        active_session_ids = tuple(active.id for active in active_sessions)

    assert stored_snapshot == (OtpChallengeStatus.CONSUMED.value, _NOW)
    assert customer_snapshot == ("active", _NOW, _NOW)
    assert event_snapshots == ((OtpChallengeEventAction.CONSUMED.value, None),)
    assert audit_types == ("customer.activated",)
    assert old_revoked_at == _NOW
    assert len(active_session_ids) == 1
    assert active_session_ids[0] != current_session_id
    assert all_session_count == 2


@pytest.mark.integration
def test_activation_locking_offer_first_then_switch_preserves_activation(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001373",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        actor, old_version_id, target_version_id = (
            _create_approved_registration_switch_target(
                session,
                snapshot=snapshot,
                admin_phone="+998900001473",
            )
        )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id

    activation_locked = Event()
    switch_attempted = Event()
    original_append = activation_service_module.append_audit_event

    def hold_after_activation_locks(*args: object, **kwargs: object) -> None:
        activation_locked.set()
        assert switch_attempted.wait(timeout=5)
        original_append(*args, **kwargs)

    monkeypatch.setattr(
        activation_service_module,
        "append_audit_event",
        hold_after_activation_locks,
    )

    def activate() -> str:
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, PreparedCustomerActivation)
            return RegistrationOtpVerificationOutcome.ACTIVATED.value

    def switch() -> bool:
        assert activation_locked.wait(timeout=5)
        switch_attempted.set()
        with Session(m2_test_database) as session, session.begin():
            result = make_offer_version_current(
                session,
                actor=actor,
                offer_version_id=target_version_id,
                expected_current_version_id=old_version_id,
                now=_NOW + timedelta(seconds=1),
            )
            return result.succeeded

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        activation_future = executor.submit(activate)
        switch_future = executor.submit(switch)
        completed, pending = wait(
            (activation_future, switch_future),
            timeout=10,
        )
        assert not pending
        assert len(completed) == 2
        assert activation_future.result() == "ACTIVATED"
        assert switch_future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        current_offer_id = session.scalar(
            select(OfferVersion.id).where(
                OfferVersion.purpose == OfferPurpose.REGISTRATION.value,
                OfferVersion.status == OfferStatus.CURRENT.value,
            )
        )
        activation_audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "customer.activated")
        )
        assert stored is not None
        assert customer is not None
        stored_status = stored.status
        customer_state = (customer.onboarding_status, customer.activated_at)

    assert stored_status == OtpChallengeStatus.CONSUMED.value
    assert customer_state == ("active", _NOW)
    assert current_offer_id == target_version_id
    assert activation_audits == 1


@pytest.mark.integration
def test_offer_switch_committing_first_invalidates_old_snapshot_without_activation(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001374",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        actor, old_version_id, target_version_id = (
            _create_approved_registration_switch_target(
                session,
                snapshot=snapshot,
                admin_phone="+998900001474",
            )
        )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id
        old_acceptance_id = snapshot.registration_offer_acceptance_id

    switch_committed = Event()

    def switch() -> bool:
        with Session(m2_test_database) as session, session.begin():
            result = make_offer_version_current(
                session,
                actor=actor,
                offer_version_id=target_version_id,
                expected_current_version_id=old_version_id,
                now=_NOW,
            )
        switch_committed.set()
        return result.succeeded

    def activate() -> RegistrationOtpVerificationOutcome:
        assert switch_committed.wait(timeout=5)
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            return result.outcome

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        switch_future = executor.submit(switch)
        activation_future = executor.submit(activate)
        completed, pending = wait(
            (switch_future, activation_future),
            timeout=10,
        )
        assert not pending
        assert len(completed) == 2
        assert switch_future.result()
        assert (
            activation_future.result()
            is RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        current_session = session.get(AuthSession, current_session_id)
        old_acceptance = session.get(OfferAcceptance, old_acceptance_id)
        current_offer_id = session.scalar(
            select(OfferVersion.id).where(
                OfferVersion.purpose == OfferPurpose.REGISTRATION.value,
                OfferVersion.status == OfferStatus.CURRENT.value,
            )
        )
        activation_audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "customer.activated")
        )
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        assert stored is not None
        assert customer is not None
        assert current_session is not None
        stored_state = (stored.status, stored.failed_attempts)
        customer_state = (customer.onboarding_status, customer.activated_at)
        current_session_revoked_at = current_session.revoked_at

    assert stored_state == (OtpChallengeStatus.INVALIDATED.value, 0)
    assert customer_state == ("draft", None)
    assert current_session_revoked_at is None
    assert old_acceptance is not None
    assert current_offer_id == target_version_id
    assert activation_audits == 0
    assert actions == (
        OtpChallengeEventAction.INVALIDATED_BY_REGISTRATION_STATE_CHANGE.value,
    )


class _HoldingAuditWriter:
    def __init__(
        self,
        session: Session,
        *,
        saved: Event,
        activation_attempted: Event,
    ) -> None:
        self._writer = SqlAlchemyAuditWriter(session)
        self._saved = saved
        self._activation_attempted = activation_attempted

    def append(self, *, event: AuditEvent) -> None:
        self._writer.append(event=event)
        self._saved.set()
        assert self._activation_attempted.wait(timeout=5)


class _NeverReadImageSource:
    async def seek(self, _offset: int) -> None:
        raise AssertionError("Active-customer document source must not be read")

    async def read(self, _size: int) -> bytes:
        raise AssertionError("Active-customer document source must not be read")


def _updated_identity_command(user_id: UUID) -> SaveCustomerIdentity:
    return SaveCustomerIdentity(
        actor=CustomerIdentityActor(user_id),
        expected_revision=1,
        first_name="Updated",
        last_name="Specimen",
        middle_name=None,
        jshshir="12345678901234",
        document_type="PASSPORT",
        document_number="AB 54321",
    )


def _document_upload_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=_RATE_HMAC_KEY,
        object_storage_endpoint_url="https://m11-storage.invalid",
        object_storage_region="region-1",
        object_storage_bucket="m11-private-documents",
        object_storage_access_key="m11-test-access",
        object_storage_secret_key="m11-test-secret",
        object_storage_use_ssl=True,
        object_storage_upload_rate_limit_user_attempts=5,
        object_storage_upload_rate_limit_ip_attempts=5,
    )


@pytest.mark.integration
def test_identity_update_holding_customer_lock_invalidates_waiting_activation(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001375",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        identity = session.get(CustomerIdentity, snapshot.customer_id)
        assert identity is not None
        identity.created_at = SEED_NOW - timedelta(days=1)
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id

    identity_saved = Event()
    activation_attempted = Event()

    def update_identity() -> int:
        with Session(m2_test_database) as session, session.begin():
            result = save_own_customer_identity(
                repository=SqlAlchemyCustomerIdentityRepository(session),
                audit_writer=_HoldingAuditWriter(
                    session,
                    saved=identity_saved,
                    activation_attempted=activation_attempted,
                ),
                crypto_config=synthetic_identity_crypto_config(),
                command=_updated_identity_command(snapshot.user_id),
                now=_NOW,
            )
            return result.revision.value

    def activate() -> RegistrationOtpVerificationOutcome:
        assert identity_saved.wait(timeout=5)
        activation_attempted.set()
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            return result.outcome

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        identity_future = executor.submit(update_identity)
        activation_future = executor.submit(activate)
        completed, pending = wait(
            (identity_future, activation_future),
            timeout=10,
        )
        assert not pending
        assert len(completed) == 2
        assert identity_future.result() == 2
        assert (
            activation_future.result()
            is RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        identity = session.get(CustomerIdentity, snapshot.customer_id)
        current_session = session.get(AuthSession, current_session_id)
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        audit_types = tuple(
            session.scalars(select(AuditLog.event_type).order_by(AuditLog.occurred_at))
        )
        assert stored is not None
        assert customer is not None
        assert identity is not None
        assert current_session is not None
        stored_state = (stored.status, stored.failed_attempts)
        customer_state = (customer.onboarding_status, customer.activated_at)

    assert stored_state == (OtpChallengeStatus.INVALIDATED.value, 0)
    assert customer_state == ("draft", None)
    assert identity.revision == 2
    assert current_session.revoked_at is None
    assert actions == (
        OtpChallengeEventAction.INVALIDATED_BY_REGISTRATION_STATE_CHANGE.value,
    )
    assert audit_types == ("customer.identity_saved",)


@pytest.mark.integration
def test_document_object_transition_holding_lock_invalidates_waiting_activation(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001376",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        document = session.get(CustomerDocument, snapshot.customer_document_id)
        assert document is not None
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id
        object_file_id = document.object_file_id

    object_changed = Event()
    activation_attempted = Event()

    def change_object_lifecycle() -> str:
        with Session(m2_test_database) as session, session.begin():
            object_file = session.scalar(
                select(ObjectFile)
                .where(ObjectFile.id == object_file_id)
                .with_for_update()
            )
            assert object_file is not None
            object_file.status = ObjectFileStatus.DELETE_PENDING.value
            object_file.updated_at = _NOW
            session.flush()
            object_changed.set()
            assert activation_attempted.wait(timeout=5)
            return object_file.status

    def activate() -> RegistrationOtpVerificationOutcome:
        assert object_changed.wait(timeout=5)
        activation_attempted.set()
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            return result.outcome

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        object_future = executor.submit(change_object_lifecycle)
        activation_future = executor.submit(activate)
        completed, pending = wait(
            (object_future, activation_future),
            timeout=10,
        )
        assert not pending
        assert len(completed) == 2
        assert object_future.result() == ObjectFileStatus.DELETE_PENDING.value
        assert (
            activation_future.result()
            is RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        current_session = session.get(AuthSession, current_session_id)
        object_file = session.get(ObjectFile, object_file_id)
        actions = tuple(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )
        assert stored is not None
        assert customer is not None
        assert current_session is not None
        assert object_file is not None

    assert (stored.status, stored.failed_attempts) == (
        OtpChallengeStatus.INVALIDATED.value,
        0,
    )
    assert (customer.onboarding_status, customer.activated_at) == ("draft", None)
    assert object_file.status == ObjectFileStatus.DELETE_PENDING.value
    assert current_session.revoked_at is None
    assert actions == (
        OtpChallengeEventAction.INVALIDATED_BY_REGISTRATION_STATE_CHANGE.value,
    )


@pytest.mark.integration
@pytest.mark.parametrize("failure_mode", ("tampered", "key_unavailable"))
def test_identity_crypto_failure_invalidates_activation_without_detail(
    failure_mode: str,
    m2_test_database: Engine,
) -> None:
    phone = {
        "tampered": "+998900001377",
        "key_unavailable": "+998900001378",
    }[failure_mode]
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(session, phone=phone)
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        identity = session.get(CustomerIdentity, snapshot.customer_id)
        assert identity is not None
        identity.created_at = SEED_NOW - timedelta(days=1)
        if failure_mode == "tampered":
            identity.ciphertext = identity.ciphertext[:-1] + bytes(
                [identity.ciphertext[-1] ^ 1]
            )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id

    crypto_config = synthetic_identity_crypto_config()
    if failure_mode == "key_unavailable":
        key_id = CustomerIdentityKeyId("identity-v2")
        crypto_config = CustomerIdentityCryptoConfig(
            active_key_id=key_id,
            encryption_keys={
                key_id: CustomerIdentityAesKey.from_bytes(bytes(range(32, 64)))
            },
            blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(
                bytes(reversed(range(32)))
            ),
        )

    with Session(m2_test_database) as session, session.begin():
        result = verify_and_activate_registration_customer(
            session,
            command=_verify_command(
                user_id=snapshot.user_id,
                current_session_id=current_session_id,
            ),
            settings=_settings(m2_test_database),
            identity_crypto_config=crypto_config,
        )
        assert isinstance(result, RegistrationOtpVerificationResult)
        safe_repr = repr(result)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        current_session = session.get(AuthSession, current_session_id)
        activation_audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "customer.activated")
        )
        assert stored is not None
        assert customer is not None
        assert current_session is not None

    assert (
        result.outcome is RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED
    )
    assert "CUSTOMER_ACTIVATION_CHANGED" in safe_repr
    assert all(
        marker not in safe_repr
        for marker in ("ciphertext", "blind_index", "key_id", "object_file")
    )
    assert stored.status == OtpChallengeStatus.INVALIDATED.value
    assert (customer.onboarding_status, customer.activated_at) == ("draft", None)
    assert current_session.revoked_at is None
    assert activation_audits == 0


@pytest.mark.integration
def test_activation_first_makes_later_identity_and_document_mutations_zero_write(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001379",
        )
        challenge, _dispatch = _create_active_registration_candidate(
            session,
            snapshot=snapshot,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        challenge_id = challenge.id
        current_session_id = current.session.id

    activation_locked = Event()
    mutations_attempted = Barrier(3)
    original_append = activation_service_module.append_audit_event

    def hold_active_customer_lock(*args: object, **kwargs: object) -> None:
        activation_locked.set()
        mutations_attempted.wait(timeout=5)
        original_append(*args, **kwargs)

    monkeypatch.setattr(
        activation_service_module,
        "append_audit_event",
        hold_active_customer_lock,
    )

    def activate() -> str:
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, PreparedCustomerActivation)
            return RegistrationOtpVerificationOutcome.ACTIVATED.value

    def update_identity() -> ErrorCode:
        assert activation_locked.wait(timeout=5)
        mutations_attempted.wait(timeout=5)
        with Session(m2_test_database) as session, session.begin():
            with pytest.raises(CustomerIdentityServiceError) as caught:
                save_own_customer_identity(
                    repository=SqlAlchemyCustomerIdentityRepository(session),
                    audit_writer=SqlAlchemyAuditWriter(session),
                    crypto_config=synthetic_identity_crypto_config(),
                    command=_updated_identity_command(snapshot.user_id),
                    now=_NOW + timedelta(seconds=1),
                )
            return caught.value.code

    def upload_document() -> tuple[ErrorCode, int]:
        assert activation_locked.wait(timeout=5)
        mutations_attempted.wait(timeout=5)
        storage = FakeObjectStorageService()
        command = UploadOwnCustomerDocument(
            actor=CustomerDocumentActor(snapshot.user_id),
            submission_id=CustomerDocumentSubmissionId(uuid4()),
            expected_current=ExpectedCurrentCustomerDocument(
                snapshot.customer_document_id
            ),
        )
        with pytest.raises(CustomerDocumentServiceError) as caught:
            asyncio.run(
                upload_and_attach_own_customer_document(
                    create_database_session_factory(m2_test_database),
                    command=command,
                    source=_NeverReadImageSource(),
                    client_ip=ResolvedClientIp("203.0.113.179"),
                    now=_NOW + timedelta(seconds=1),
                    settings=_document_upload_settings(m2_test_database),
                    storage=storage,
                )
            )
        return caught.value.code, len(storage.calls)

    executor = ThreadPoolExecutor(max_workers=3)
    try:
        activation_future = executor.submit(activate)
        identity_future = executor.submit(update_identity)
        document_future = executor.submit(upload_document)
        completed, pending = wait(
            (activation_future, identity_future, document_future),
            timeout=15,
        )
        assert not pending
        assert len(completed) == 3
        assert activation_future.result() == "ACTIVATED"
        assert identity_future.result() is ErrorCode.CUSTOMER_DRAFT_REQUIRED
        assert document_future.result() == (ErrorCode.CUSTOMER_DRAFT_REQUIRED, 0)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        customer = session.get(Customer, snapshot.customer_id)
        identity = session.get(CustomerIdentity, snapshot.customer_id)
        document_count = session.scalar(
            select(func.count())
            .select_from(CustomerDocument)
            .where(CustomerDocument.customer_id == snapshot.customer_id)
        )
        object_count = session.scalar(
            select(func.count())
            .select_from(ObjectFile)
            .where(ObjectFile.created_by_user_id == snapshot.user_id)
        )
        audit_types = tuple(
            session.scalars(select(AuditLog.event_type).order_by(AuditLog.occurred_at))
        )
        assert stored is not None
        assert customer is not None
        assert identity is not None

    assert stored.status == OtpChallengeStatus.CONSUMED.value
    assert (customer.onboarding_status, customer.activated_at) == ("active", _NOW)
    assert identity.revision == 1
    assert document_count == 1
    assert object_count == 1
    assert audit_types == ("customer.activated",)
