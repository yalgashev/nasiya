from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    CustomerActivatedAuditPayload,
)
from app.audit.repository import append_audit_event
from app.auth.deps import CurrentSessionContext, CurrentSessionStatus
from app.auth.models import User
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.customer.ports import (
    CustomerActivationTransitionOutcome,
    CustomerLifecycleStatus,
)
from app.customer.repository import (
    get_existing_own_customer_status,
    load_existing_own_customer,
    lock_existing_own_customer_for_update,
    transition_existing_own_customer_draft_to_active,
)
from app.customer_activation.contracts import (
    CurrentRegistrationAcceptanceSelection,
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    CustomerAlreadyActive,
    PreparedCustomerActivation,
    RegistrationOtpCandidate,
    RegistrationOtpCooldown,
    RegistrationOtpPendingDelivery,
    RegistrationOtpPrerequisiteFailed,
    RegistrationOtpRateLimited,
    RegistrationOtpRequestResult,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    RegistrationPrerequisiteError,
    RegistrationReadinessComponent,
    RegistrationReadinessComponentStatus,
    RegistrationReadinessComponentView,
    RegistrationReadinessSnapshot,
    RegistrationReadinessState,
    RegistrationReadinessView,
    VerifyRegistrationOtp,
    parse_registration_otp_candidate,
)
from app.customer_activation.rate_limit import RegistrationIssuanceRateLimitPolicy
from app.customer_activation.repository import (
    SqlAlchemyCurrentSessionRotation,
    SqlAlchemyCustomerDocumentReadiness,
    SqlAlchemyCustomerIdentityReadiness,
    SqlAlchemyRegistrationOfferReadiness,
)
from app.customer_document.service import CustomerDocumentCompletenessService
from app.customer_identity.crypto import CustomerIdentityCryptoConfig
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.service import CustomerIdentityCompletenessService
from app.offers.repository import SqlAlchemyHasAcceptedCurrentRegistrationOffer
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpPurpose,
)
from app.otp.crypto import derive_browser_binding_digest, verify_otp_code_mac
from app.otp.issuance import invalidate_otp_challenges_for_link_change
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.repository import (
    OtpChallengeInsertConflict,
    OtpChallengeLockSet,
    append_challenge_event,
    burn_challenge,
    consume_registration_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
    expire_challenge,
    invalidate_registration_challenge_for_state_change,
    lock_outstanding_challenge_set_by_user,
    lock_registration_candidate_set_by_browser,
    record_registration_failed_attempt,
    supersede_and_cancel_same_purpose_challenges,
)
from app.otp.web_presentation import OtpWebLanguage, get_otp_dispatch_locale
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink
from app.telegram.repository import (
    get_telegram_link_by_user_for_update,
    has_active_telegram_link,
)

_REGISTRATION_DUMMY_CHALLENGE_ID: Final = UUID("00000000-0000-4000-8000-000000000201")
_REGISTRATION_DUMMY_USER_ID: Final = UUID("00000000-0000-4000-8000-000000000202")
_REGISTRATION_DUMMY_CODE: Final = OtpCode("000000")
_REGISTRATION_DUMMY_STORED_MAC: Final = "0" * 64

RegistrationOtpVerifyDummyWork = Callable[[SecretStr, OtpCode | None], None]
type RegistrationOtpInputBoundaryResult = (
    RegistrationOtpCandidate | RegistrationOtpVerificationResult
)


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticatedActivationContext:
    actor: CustomerActivationActor = field(repr=False)
    browser: CustomerActivationBrowserContext = field(repr=False)
    trusted_client_ip: ResolvedClientIp = field(repr=False)
    _canonical_account_phone: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor, CustomerActivationActor):
            raise TypeError("Activation actor is invalid")
        if not isinstance(self.browser, CustomerActivationBrowserContext):
            raise TypeError("Activation browser context is invalid")
        if not isinstance(self.trusted_client_ip, ResolvedClientIp):
            raise TypeError("Activation client IP is invalid")
        try:
            canonical_phone = normalize_uzbekistan_phone(self._canonical_account_phone)
        except (PhoneNormalizationError, TypeError):
            raise ValueError("Activation account phone is invalid") from None
        if canonical_phone != self._canonical_account_phone:
            raise ValueError("Activation account phone is invalid")

    def canonical_account_phone_for_rate_limit(self) -> str:
        return self._canonical_account_phone

    def __repr__(self) -> str:
        return (
            "AuthenticatedActivationContext("
            "actor=<redacted>, browser=<redacted>, "
            "trusted_client_ip=<redacted>, account_phone=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedRegistrationOtpCandidate:
    challenge: OtpChallenge = field(repr=False)
    dispatch: OtpDispatch | None = field(repr=False)
    code: OtpCode = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.challenge, OtpChallenge):
            raise TypeError("Registration OTP challenge is invalid")
        if self.dispatch is not None and not isinstance(self.dispatch, OtpDispatch):
            raise TypeError("Registration OTP dispatch is invalid")
        if not isinstance(self.code, OtpCode):
            raise TypeError("Registration OTP code is invalid")

    def __repr__(self) -> str:
        return (
            "ResolvedRegistrationOtpCandidate("
            "challenge=<redacted>, dispatch=<redacted>, code=<redacted>)"
        )


type RegistrationOtpCandidateResolutionResult = (
    ResolvedRegistrationOtpCandidate | RegistrationOtpVerificationResult
)


class RegistrationSnapshotRecheckOutcome(StrEnum):
    READY = "READY"
    LINK_CHANGED = "LINK_CHANGED"
    REGISTRATION_STATE_CHANGED = "REGISTRATION_STATE_CHANGED"


@dataclass(frozen=True, slots=True, repr=False)
class RegistrationSnapshotRecheckResult:
    outcome: RegistrationSnapshotRecheckOutcome
    candidate: ResolvedRegistrationOtpCandidate = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RegistrationSnapshotRecheckOutcome):
            raise TypeError("Registration snapshot outcome is invalid")
        if not isinstance(self.candidate, ResolvedRegistrationOtpCandidate):
            raise TypeError("Registration snapshot candidate is invalid")

    def __repr__(self) -> str:
        return (
            "RegistrationSnapshotRecheckResult("
            f"outcome={self.outcome.value!r}, candidate=<redacted>)"
        )


def check_registration_otp_input_boundary(
    command: VerifyRegistrationOtp,
    *,
    otp_hmac_key: SecretStr,
    dummy_work: RegistrationOtpVerifyDummyWork | None = None,
) -> RegistrationOtpInputBoundaryResult:
    if not isinstance(command, VerifyRegistrationOtp):
        raise TypeError("Registration OTP verification command is invalid")
    if not isinstance(otp_hmac_key, SecretStr):
        raise TypeError("Registration OTP verification key is invalid")
    candidate = parse_registration_otp_candidate(command.candidate_code)
    if not candidate.requires_dummy_mac:
        return candidate
    _run_registration_verify_dummy_work(
        otp_hmac_key=otp_hmac_key,
        candidate_code=None,
        dummy_work=dummy_work,
    )
    return RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )


def resolve_registration_otp_candidate(
    session: Session,
    *,
    command: VerifyRegistrationOtp,
    settings: Settings,
    dummy_work: RegistrationOtpVerifyDummyWork | None = None,
) -> RegistrationOtpCandidateResolutionResult:
    if not isinstance(session, Session):
        raise TypeError("Registration OTP verification session is invalid")
    if not isinstance(settings, Settings):
        raise TypeError("Registration OTP verification settings are invalid")
    otp_hmac_key = settings.require_otp_hmac_key()
    input_boundary = check_registration_otp_input_boundary(
        command,
        otp_hmac_key=otp_hmac_key,
        dummy_work=dummy_work,
    )
    if isinstance(input_boundary, RegistrationOtpVerificationResult):
        return input_boundary
    candidate_code = input_boundary.code
    if candidate_code is None:
        raise RuntimeError("Registration OTP candidate boundary is inconsistent")

    lock_set = lock_registration_candidate_set_by_browser(
        session,
        browser_binding_digest=command.browser.browser_binding_digest,
    )
    if len(lock_set.challenges) != 1:
        customer = lock_existing_own_customer_for_update(
            session,
            actor_user_id=command.actor.user_id,
        )
        if (
            customer is not None
            and customer.onboarding_status == CustomerLifecycleStatus.ACTIVE.value
        ):
            return RegistrationOtpVerificationResult(
                RegistrationOtpVerificationOutcome.ALREADY_ACTIVE
            )
        return _invalid_registration_candidate(
            otp_hmac_key=otp_hmac_key,
            candidate_code=candidate_code,
            dummy_work=dummy_work,
        )

    challenge = lock_set.challenges[0]
    config = settings.require_registration_otp_config()
    if not _is_active_registration_candidate(
        challenge,
        actor=command.actor,
        browser=command.browser,
    ):
        return _invalid_registration_candidate(
            otp_hmac_key=otp_hmac_key,
            candidate_code=candidate_code,
            dummy_work=dummy_work,
        )
    dispatch = next(
        (
            candidate
            for candidate in lock_set.dispatches
            if candidate.challenge_id == challenge.id
        ),
        None,
    )
    if challenge.expires_at is None or command.now >= challenge.expires_at:
        expire_challenge(session, challenge=challenge, now=command.now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            action=OtpChallengeEventAction.EXPIRED,
            occurred_at=command.now,
            safe_code="OTP_EXPIRED",
        )
        return _invalid_registration_candidate(
            otp_hmac_key=otp_hmac_key,
            candidate_code=candidate_code,
            dummy_work=dummy_work,
        )
    if challenge.failed_attempts >= config.max_verify_attempts:
        burn_challenge(session, challenge=challenge, now=command.now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            action=OtpChallengeEventAction.BURNED,
            occurred_at=command.now,
            safe_code="OTP_BURNED",
        )
        return _invalid_registration_candidate(
            otp_hmac_key=otp_hmac_key,
            candidate_code=candidate_code,
            dummy_work=dummy_work,
        )
    return ResolvedRegistrationOtpCandidate(
        challenge=challenge,
        dispatch=dispatch,
        code=candidate_code,
    )


def recheck_registration_activation_snapshot(
    session: Session,
    *,
    command: VerifyRegistrationOtp,
    candidate: ResolvedRegistrationOtpCandidate,
    identity_crypto_config: CustomerIdentityCryptoConfig,
) -> RegistrationSnapshotRecheckResult:
    if not isinstance(session, Session):
        raise TypeError("Registration snapshot session is invalid")
    if not isinstance(command, VerifyRegistrationOtp):
        raise TypeError("Registration snapshot command is invalid")
    if not isinstance(candidate, ResolvedRegistrationOtpCandidate):
        raise TypeError("Registration snapshot candidate is invalid")
    if not isinstance(identity_crypto_config, CustomerIdentityCryptoConfig):
        raise TypeError("Registration identity crypto config is invalid")

    challenge = candidate.challenge
    if challenge.user_id is None:
        return _registration_state_changed(candidate)
    user = session.get(User, challenge.user_id, with_for_update=True)
    if (
        user is None
        or not user.is_active
        or user.id != command.actor.user_id
        or challenge.browser_binding_digest
        != command.browser.browser_binding_digest.as_stored_value()
        or challenge.purpose != OtpPurpose.REGISTRATION.value
    ):
        return _registration_state_changed(candidate)

    if challenge.telegram_link_id is None:
        return _link_changed(candidate)
    link = session.get(
        TelegramLink,
        challenge.telegram_link_id,
        with_for_update=True,
    )
    if (
        link is None
        or link.user_id != user.id
        or link.telegram_chat_id is None
        or link.unlinked_at is not None
        or link.linked_at != challenge.telegram_linked_at
    ):
        return _link_changed(candidate)

    customer = lock_existing_own_customer_for_update(
        session,
        actor_user_id=user.id,
    )
    if (
        customer is None
        or customer.id != challenge.customer_id
        or customer.onboarding_status != CustomerLifecycleStatus.DRAFT.value
    ):
        return _registration_state_changed(candidate)

    acceptance = select_current_registration_acceptance(
        session,
        actor=command.actor,
    )
    if (
        not acceptance.succeeded
        or acceptance.acceptance_id_for_snapshot()
        != challenge.registration_offer_acceptance_id
    ):
        return _registration_state_changed(candidate)

    identity_revision = SqlAlchemyCustomerIdentityReadiness(
        session,
        crypto_config=identity_crypto_config,
    ).lock_complete_identity_revision(customer_id=customer.id)
    if (
        identity_revision is None
        or identity_revision.value != challenge.customer_identity_revision
    ):
        return _registration_state_changed(candidate)

    document_id = SqlAlchemyCustomerDocumentReadiness(
        session
    ).lock_current_available_document(customer_id=customer.id)
    if document_id is None or document_id != challenge.customer_document_id:
        return _registration_state_changed(candidate)
    return RegistrationSnapshotRecheckResult(
        outcome=RegistrationSnapshotRecheckOutcome.READY,
        candidate=candidate,
    )


type RegistrationOtpCandidateStateResult = (
    RegistrationSnapshotRecheckResult | RegistrationOtpVerificationResult
)


class CustomerActivationSessionUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Current activation session is unavailable")


type RegistrationOtpActivationAttemptResult = (
    PreparedCustomerActivation | RegistrationOtpVerificationResult
)


def resolve_and_recheck_registration_otp_candidate(
    session: Session,
    *,
    command: VerifyRegistrationOtp,
    settings: Settings,
    identity_crypto_config: CustomerIdentityCryptoConfig,
    dummy_work: RegistrationOtpVerifyDummyWork | None = None,
) -> RegistrationOtpCandidateStateResult:
    resolved = resolve_registration_otp_candidate(
        session,
        command=command,
        settings=settings,
        dummy_work=dummy_work,
    )
    if isinstance(resolved, RegistrationOtpVerificationResult):
        return resolved
    rechecked = recheck_registration_activation_snapshot(
        session,
        command=command,
        candidate=resolved,
        identity_crypto_config=identity_crypto_config,
    )
    if rechecked.outcome is RegistrationSnapshotRecheckOutcome.READY:
        return rechecked
    _invalidate_registration_snapshot_mismatch(
        session,
        rechecked=rechecked,
        now=command.now,
    )
    return RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED
    )


def check_registration_otp_candidate_code(
    session: Session,
    *,
    command: VerifyRegistrationOtp,
    settings: Settings,
    identity_crypto_config: CustomerIdentityCryptoConfig,
    dummy_work: RegistrationOtpVerifyDummyWork | None = None,
) -> RegistrationOtpCandidateStateResult:
    candidate_state = resolve_and_recheck_registration_otp_candidate(
        session,
        command=command,
        settings=settings,
        identity_crypto_config=identity_crypto_config,
        dummy_work=dummy_work,
    )
    if isinstance(candidate_state, RegistrationOtpVerificationResult):
        return candidate_state
    candidate = candidate_state.candidate
    challenge = candidate.challenge
    otp_hmac_key = settings.require_otp_hmac_key()
    mac_matches = verify_otp_code_mac(
        otp_hmac_key=otp_hmac_key,
        challenge_id=challenge.id,
        user_id=challenge.user_id,
        purpose=OtpPurpose.REGISTRATION,
        code=candidate.code,
        stored_mac=challenge.code_mac,
    )
    if mac_matches:
        return candidate_state
    _run_registration_verify_dummy_work(
        otp_hmac_key=otp_hmac_key,
        candidate_code=candidate.code,
        dummy_work=dummy_work,
    )
    record_registration_failed_attempt(
        session,
        challenge=challenge,
        now=command.now,
        max_attempts=(settings.require_registration_otp_config().max_verify_attempts),
    )
    return RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )


def verify_and_activate_registration_customer(
    session: Session,
    *,
    command: VerifyRegistrationOtp,
    settings: Settings,
    identity_crypto_config: CustomerIdentityCryptoConfig,
    dummy_work: RegistrationOtpVerifyDummyWork | None = None,
) -> RegistrationOtpActivationAttemptResult:
    candidate_state = check_registration_otp_candidate_code(
        session,
        command=command,
        settings=settings,
        identity_crypto_config=identity_crypto_config,
        dummy_work=dummy_work,
    )
    if isinstance(candidate_state, RegistrationOtpVerificationResult):
        return candidate_state
    candidate = candidate_state.candidate
    challenge = candidate.challenge
    consume_registration_challenge(
        session,
        challenge=challenge,
        now=command.now,
    )
    transition = transition_existing_own_customer_draft_to_active(
        session,
        actor_user_id=command.actor.user_id,
        expected_status=CustomerLifecycleStatus.DRAFT,
        now=command.now,
    )
    if transition.outcome is not CustomerActivationTransitionOutcome.ACTIVATED:
        raise RuntimeError("Customer activation state changed after verification")
    if challenge.customer_id is None:
        raise RuntimeError("Customer activation snapshot is unavailable")
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.CUSTOMER_ACTIVATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=command.actor.user_id,
            object_type=AuditObjectType.CUSTOMER,
            object_id=challenge.customer_id,
            occurred_at=command.now,
            candidate_metadata=(
                CustomerActivatedAuditPayload().as_candidate_metadata()
            ),
        ),
    )
    prepared = SqlAlchemyCurrentSessionRotation(
        session,
        settings=settings,
    ).replace_current_authenticated_session(
        actor_user_id=command.actor.user_id,
        current_session_id=command.browser.current_session_id,
        now=command.now,
    )
    if prepared is None:
        raise CustomerActivationSessionUnavailable()
    return prepared


def select_current_registration_acceptance(
    session: Session,
    *,
    actor: CustomerActivationActor,
) -> CurrentRegistrationAcceptanceSelection:
    if not isinstance(session, Session):
        raise TypeError("Registration acceptance session is invalid")
    if not isinstance(actor, CustomerActivationActor):
        raise TypeError("Registration acceptance actor is invalid")
    return SqlAlchemyRegistrationOfferReadiness(
        session
    ).select_earliest_exact_current_acceptance(actor_user_id=actor.user_id)


def issue_registration_otp(
    session: Session,
    *,
    context: AuthenticatedActivationContext,
    identity_crypto_config: CustomerIdentityCryptoConfig,
    language: OtpWebLanguage,
    now: datetime,
) -> RegistrationOtpRequestResult:
    return _issue_registration_otp(
        session,
        context=context,
        identity_crypto_config=identity_crypto_config,
        language=language,
        now=now,
        resend_cooldown_seconds=None,
    )


def request_new_registration_otp(
    session_factory: sessionmaker[Session],
    *,
    context: AuthenticatedActivationContext,
    settings: Settings,
    identity_crypto_config: CustomerIdentityCryptoConfig,
    language: OtpWebLanguage,
    now: datetime,
) -> RegistrationOtpRequestResult:
    if not isinstance(context, AuthenticatedActivationContext):
        raise TypeError("Activation context is invalid")
    if not isinstance(settings, Settings):
        raise TypeError("Registration settings are invalid")
    current_time = _as_utc(now)
    config = settings.require_registration_otp_config()
    with session_factory.begin() as rate_session:
        current_user = rate_session.get(User, context.actor.user_id)
        if (
            current_user is None
            or not current_user.is_active
            or current_user.phone != context.canonical_account_phone_for_rate_limit()
        ):
            return _prerequisite_failed(
                RegistrationPrerequisiteError.CUSTOMER_DRAFT_REQUIRED
            )
        rate_result = RegistrationIssuanceRateLimitPolicy(
            session=rate_session,
            settings=settings,
        ).check_and_record(
            current_user=current_user,
            client_ip=context.trusted_client_ip,
            now=current_time,
        )
    if not rate_result.allowed:
        return RegistrationOtpRateLimited()
    with session_factory.begin() as domain_session:
        return _issue_registration_otp(
            domain_session,
            context=context,
            identity_crypto_config=identity_crypto_config,
            language=language,
            now=current_time,
            resend_cooldown_seconds=config.resend_cooldown_seconds,
        )


def _issue_registration_otp(
    session: Session,
    *,
    context: AuthenticatedActivationContext,
    identity_crypto_config: CustomerIdentityCryptoConfig,
    language: OtpWebLanguage,
    now: datetime,
    resend_cooldown_seconds: int | None,
) -> RegistrationOtpRequestResult:
    if not isinstance(session, Session):
        raise TypeError("Registration issue session is invalid")
    if not isinstance(context, AuthenticatedActivationContext):
        raise TypeError("Activation context is invalid")
    if not isinstance(identity_crypto_config, CustomerIdentityCryptoConfig):
        raise TypeError("Customer identity crypto config is invalid")
    if not isinstance(language, OtpWebLanguage):
        raise TypeError("Registration dispatch language is invalid")
    if resend_cooldown_seconds is not None and (
        not isinstance(resend_cooldown_seconds, int)
        or isinstance(resend_cooldown_seconds, bool)
        or resend_cooldown_seconds <= 0
    ):
        raise ValueError("Registration resend cooldown is invalid")
    current_time = _as_utc(now)

    locked_otp = lock_outstanding_challenge_set_by_user(
        session,
        user_id=context.actor.user_id,
        purpose=OtpPurpose.REGISTRATION,
    )
    user = session.get(User, context.actor.user_id, with_for_update=True)
    if user is None or not user.is_active:
        return _prerequisite_failed(
            RegistrationPrerequisiteError.CUSTOMER_DRAFT_REQUIRED
        )
    link = get_telegram_link_by_user_for_update(session, user)
    if link is None or link.telegram_chat_id is None or link.unlinked_at is not None:
        return _prerequisite_failed(RegistrationPrerequisiteError.TELEGRAM_NOT_LINKED)
    customer = lock_existing_own_customer_for_update(
        session,
        actor_user_id=context.actor.user_id,
    )
    if customer is None:
        return _prerequisite_failed(
            RegistrationPrerequisiteError.CUSTOMER_DRAFT_REQUIRED
        )
    try:
        customer_status = CustomerLifecycleStatus(customer.onboarding_status)
    except ValueError:
        return _prerequisite_failed(
            RegistrationPrerequisiteError.CUSTOMER_DRAFT_REQUIRED
        )
    if customer_status is CustomerLifecycleStatus.ACTIVE:
        return CustomerAlreadyActive()
    if customer_status is not CustomerLifecycleStatus.DRAFT:
        return _prerequisite_failed(
            RegistrationPrerequisiteError.CUSTOMER_DRAFT_REQUIRED
        )
    if resend_cooldown_seconds is not None and any(
        challenge.created_at + timedelta(seconds=resend_cooldown_seconds) > current_time
        for challenge in locked_otp.challenges
    ):
        return RegistrationOtpCooldown()

    acceptance = select_current_registration_acceptance(
        session,
        actor=context.actor,
    )
    if not acceptance.succeeded:
        if acceptance.error is None:
            raise RuntimeError("Registration acceptance failure is unavailable")
        return _prerequisite_failed(acceptance.error)
    identity_revision = SqlAlchemyCustomerIdentityReadiness(
        session,
        crypto_config=identity_crypto_config,
    ).lock_complete_identity_revision(customer_id=customer.id)
    if identity_revision is None:
        return _prerequisite_failed(
            RegistrationPrerequisiteError.CUSTOMER_IDENTITY_UNAVAILABLE
        )
    document_id = SqlAlchemyCustomerDocumentReadiness(
        session
    ).lock_current_available_document(customer_id=customer.id)
    if document_id is None:
        return _prerequisite_failed(
            RegistrationPrerequisiteError.CUSTOMER_DOCUMENT_UNAVAILABLE
        )

    snapshot = RegistrationReadinessSnapshot(
        user_id=user.id,
        customer_id=customer.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        registration_offer_acceptance_id=(acceptance.acceptance_id_for_snapshot()),
        customer_identity_revision=identity_revision,
        customer_document_id=document_id,
        browser_binding_digest=context.browser.browser_binding_digest,
    )
    supersede_and_cancel_same_purpose_challenges(
        session,
        locked=locked_otp,
        purpose=OtpPurpose.REGISTRATION,
        now=current_time,
    )
    try:
        challenge = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=current_time,
        )
    except OtpChallengeInsertConflict:
        return RegistrationOtpPendingDelivery()
    create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale=get_otp_dispatch_locale(language),
        now=current_time,
    )
    append_challenge_event(
        session,
        challenge_id=challenge.id,
        user_id=user.id,
        action=OtpChallengeEventAction.ISSUED,
        occurred_at=current_time,
    )
    return RegistrationOtpPendingDelivery()


def get_registration_readiness(
    session: Session,
    *,
    context: AuthenticatedActivationContext,
    identity_crypto_config: CustomerIdentityCryptoConfig,
) -> RegistrationReadinessView:
    if not isinstance(session, Session):
        raise TypeError("Registration readiness session is invalid")
    if not isinstance(context, AuthenticatedActivationContext):
        raise TypeError("Activation context is invalid")
    if not isinstance(identity_crypto_config, CustomerIdentityCryptoConfig):
        raise TypeError("Customer identity crypto config is invalid")

    user = session.get(User, context.actor.user_id)
    customer = load_existing_own_customer(
        session,
        actor_user_id=context.actor.user_id,
    )
    customer_status = get_existing_own_customer_status(
        session,
        actor_user_id=context.actor.user_id,
    )
    active_user = user is not None and user.is_active
    customer_id = None if customer is None else customer.id
    component_statuses = {
        RegistrationReadinessComponent.TELEGRAM_LINK: (
            active_user and user is not None and has_active_telegram_link(session, user)
        ),
        RegistrationReadinessComponent.OFFER_ACCEPTANCE: (
            active_user
            and SqlAlchemyHasAcceptedCurrentRegistrationOffer(session)(
                user_id=context.actor.user_id
            )
        ),
        RegistrationReadinessComponent.CUSTOMER_IDENTITY: (
            active_user
            and customer_id is not None
            and CustomerIdentityCompletenessService(
                repository=SqlAlchemyCustomerIdentityRepository(session),
                crypto_config=identity_crypto_config,
            )(customer_id=customer_id)
        ),
        RegistrationReadinessComponent.CUSTOMER_DOCUMENT: (
            active_user
            and customer_id is not None
            and CustomerDocumentCompletenessService(session=session)(
                customer_id=customer_id
            )
        ),
    }
    components = tuple(
        RegistrationReadinessComponentView(
            component=component,
            status=(
                RegistrationReadinessComponentStatus.COMPLETE
                if component_statuses[component]
                else RegistrationReadinessComponentStatus.INCOMPLETE
            ),
        )
        for component in RegistrationReadinessComponent
    )
    all_complete = all(
        component.status is RegistrationReadinessComponentStatus.COMPLETE
        for component in components
    )
    if customer_status is CustomerLifecycleStatus.ACTIVE:
        state = RegistrationReadinessState.ACTIVE
    elif customer_status is CustomerLifecycleStatus.DRAFT and all_complete:
        state = RegistrationReadinessState.READY_FOR_OTP
    else:
        state = RegistrationReadinessState.INCOMPLETE
    return RegistrationReadinessView(state=state, components=components)


def _prerequisite_failed(
    error: RegistrationPrerequisiteError,
) -> RegistrationOtpPrerequisiteFailed:
    return RegistrationOtpPrerequisiteFailed(error=error)


def _run_registration_verify_dummy_work(
    *,
    otp_hmac_key: SecretStr,
    candidate_code: OtpCode | None,
    dummy_work: RegistrationOtpVerifyDummyWork | None,
) -> None:
    if dummy_work is not None:
        dummy_work(otp_hmac_key, candidate_code)
        return
    verify_otp_code_mac(
        otp_hmac_key=otp_hmac_key,
        challenge_id=_REGISTRATION_DUMMY_CHALLENGE_ID,
        user_id=_REGISTRATION_DUMMY_USER_ID,
        purpose=OtpPurpose.REGISTRATION,
        code=candidate_code or _REGISTRATION_DUMMY_CODE,
        stored_mac=_REGISTRATION_DUMMY_STORED_MAC,
    )


def _invalid_registration_candidate(
    *,
    otp_hmac_key: SecretStr,
    candidate_code: OtpCode,
    dummy_work: RegistrationOtpVerifyDummyWork | None,
) -> RegistrationOtpVerificationResult:
    _run_registration_verify_dummy_work(
        otp_hmac_key=otp_hmac_key,
        candidate_code=candidate_code,
        dummy_work=dummy_work,
    )
    return RegistrationOtpVerificationResult(
        RegistrationOtpVerificationOutcome.OTP_INVALID
    )


def _registration_state_changed(
    candidate: ResolvedRegistrationOtpCandidate,
) -> RegistrationSnapshotRecheckResult:
    return RegistrationSnapshotRecheckResult(
        outcome=RegistrationSnapshotRecheckOutcome.REGISTRATION_STATE_CHANGED,
        candidate=candidate,
    )


def _link_changed(
    candidate: ResolvedRegistrationOtpCandidate,
) -> RegistrationSnapshotRecheckResult:
    return RegistrationSnapshotRecheckResult(
        outcome=RegistrationSnapshotRecheckOutcome.LINK_CHANGED,
        candidate=candidate,
    )


def _invalidate_registration_snapshot_mismatch(
    session: Session,
    *,
    rechecked: RegistrationSnapshotRecheckResult,
    now: datetime,
) -> None:
    candidate = rechecked.candidate
    if rechecked.outcome is RegistrationSnapshotRecheckOutcome.LINK_CHANGED:
        invalidate_otp_challenges_for_link_change(
            session,
            user_id=candidate.challenge.user_id,
            purposes=(OtpPurpose.REGISTRATION,),
            now=now,
            locked=OtpChallengeLockSet(
                dispatches=(
                    () if candidate.dispatch is None else (candidate.dispatch,)
                ),
                challenges=(candidate.challenge,),
            ),
        )
        return
    if (
        rechecked.outcome
        is RegistrationSnapshotRecheckOutcome.REGISTRATION_STATE_CHANGED
    ):
        invalidate_registration_challenge_for_state_change(
            session,
            challenge=candidate.challenge,
            dispatch=candidate.dispatch,
            now=now,
        )
        return
    raise ValueError("Ready registration snapshot cannot be invalidated")


def _is_active_registration_candidate(
    challenge: OtpChallenge,
    *,
    actor: CustomerActivationActor,
    browser: CustomerActivationBrowserContext,
) -> bool:
    return (
        challenge.user_id == actor.user_id
        and challenge.purpose == OtpPurpose.REGISTRATION.value
        and challenge.browser_binding_digest
        == browser.browser_binding_digest.as_stored_value()
        and challenge.status == OtpChallengeStatus.ACTIVE.value
        and challenge.code_mac is not None
        and challenge.activated_at is not None
        and challenge.expires_at is not None
    )


def derive_authenticated_activation_context(
    *,
    current_context: CurrentSessionContext,
    trusted_client_ip: ResolvedClientIp,
    otp_hmac_key: SecretStr,
    now: datetime,
) -> AuthenticatedActivationContext | None:
    if not isinstance(current_context, CurrentSessionContext):
        raise TypeError("Current session context is invalid")
    if not isinstance(trusted_client_ip, ResolvedClientIp):
        raise TypeError("Resolved client IP is invalid")
    if not isinstance(otp_hmac_key, SecretStr):
        raise TypeError("OTP HMAC key is invalid")
    current_time = _as_utc(now)
    if current_context.status is not CurrentSessionStatus.AUTHENTICATED:
        return None
    session = current_context.get_session_row()
    user = current_context.get_authenticated_user()
    if (
        session is None
        or user is None
        or current_context.session_id != session.id
        or current_context.user_id != user.id
        or session.user_id != user.id
        or not user.is_active
        or session.revoked_at is not None
        or _as_utc(session.expires_at) <= current_time
    ):
        return None
    try:
        canonical_phone = normalize_uzbekistan_phone(user.phone)
        if canonical_phone != user.phone:
            return None
        browser_binding = derive_browser_binding_digest(
            otp_hmac_key=otp_hmac_key,
            session_id=session.id,
            csrf_secret=session.csrf_secret,
        )
    except (PhoneNormalizationError, TypeError, ValueError):
        return None
    return AuthenticatedActivationContext(
        actor=CustomerActivationActor(user_id=user.id),
        browser=CustomerActivationBrowserContext(
            current_session_id=session.id,
            browser_binding_digest=browser_binding,
        ),
        trusted_client_ip=trusted_client_ip,
        _canonical_account_phone=canonical_phone,
    )


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Activation context time must be timezone-aware")
    return value.astimezone(UTC)
