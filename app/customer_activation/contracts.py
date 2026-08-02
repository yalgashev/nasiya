from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.auth.sessions import RawSessionToken
from app.auth.user_agent import truncate_user_agent
from app.customer_identity.contracts import IdentityRevision
from app.otp.code import OtpCode
from app.otp.contracts import OtpPurpose
from app.otp.crypto import OtpBrowserBindingDigest
from app.telegram.client_ip import ResolvedClientIp


@dataclass(frozen=True, slots=True, repr=False)
class CustomerActivationActor:
    user_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.user_id, field_name="actor_user_id")

    def __repr__(self) -> str:
        return "CustomerActivationActor(user_id=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerActivationBrowserContext:
    current_session_id: UUID = field(repr=False)
    browser_binding_digest: OtpBrowserBindingDigest = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.current_session_id, field_name="current_session_id")
        if not isinstance(self.browser_binding_digest, OtpBrowserBindingDigest):
            raise ValueError("OTP browser binding is invalid")

    def __repr__(self) -> str:
        return (
            "CustomerActivationBrowserContext("
            "current_session_id=<redacted>, browser_binding_digest=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RequestRegistrationOtp:
    actor: CustomerActivationActor
    browser: CustomerActivationBrowserContext = field(repr=False)
    trusted_client_ip: ResolvedClientIp = field(repr=False)
    now: datetime

    def __post_init__(self) -> None:
        _validate_request_context(
            actor=self.actor,
            browser=self.browser,
            trusted_client_ip=self.trusted_client_ip,
            now=self.now,
        )
        object.__setattr__(self, "now", _as_utc(self.now))

    def __repr__(self) -> str:
        return (
            "RequestRegistrationOtp("
            "actor=<redacted>, browser=<redacted>, "
            "trusted_client_ip=<redacted>, now=<injected>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RequestNewRegistrationOtpCode:
    actor: CustomerActivationActor
    browser: CustomerActivationBrowserContext = field(repr=False)
    trusted_client_ip: ResolvedClientIp = field(repr=False)
    now: datetime

    def __post_init__(self) -> None:
        _validate_request_context(
            actor=self.actor,
            browser=self.browser,
            trusted_client_ip=self.trusted_client_ip,
            now=self.now,
        )
        object.__setattr__(self, "now", _as_utc(self.now))

    def __repr__(self) -> str:
        return (
            "RequestNewRegistrationOtpCode("
            "actor=<redacted>, browser=<redacted>, "
            "trusted_client_ip=<redacted>, now=<injected>)"
        )


class RegistrationPrerequisiteError(StrEnum):
    CUSTOMER_DRAFT_REQUIRED = "CUSTOMER_DRAFT_REQUIRED"
    TELEGRAM_NOT_LINKED = "TELEGRAM_NOT_LINKED"
    OFFER_UNAVAILABLE = "OFFER_UNAVAILABLE"
    REGISTRATION_OFFER_NOT_ACCEPTED = "REGISTRATION_OFFER_NOT_ACCEPTED"
    CUSTOMER_IDENTITY_UNAVAILABLE = "CUSTOMER_IDENTITY_UNAVAILABLE"
    CUSTOMER_DOCUMENT_UNAVAILABLE = "CUSTOMER_DOCUMENT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RegistrationOtpPendingDelivery:
    pass


@dataclass(frozen=True, slots=True)
class CustomerAlreadyActive:
    pass


@dataclass(frozen=True, slots=True)
class RegistrationOtpPrerequisiteFailed:
    error: RegistrationPrerequisiteError

    def __post_init__(self) -> None:
        if not isinstance(self.error, RegistrationPrerequisiteError):
            raise TypeError("Registration prerequisite error is invalid")


@dataclass(frozen=True, slots=True)
class RegistrationOtpCooldown:
    pass


@dataclass(frozen=True, slots=True)
class RegistrationOtpRateLimited:
    pass


type RegistrationOtpRequestResult = (
    RegistrationOtpPendingDelivery
    | CustomerAlreadyActive
    | RegistrationOtpPrerequisiteFailed
    | RegistrationOtpCooldown
    | RegistrationOtpRateLimited
)


@dataclass(frozen=True, slots=True, repr=False)
class VerifyRegistrationOtp:
    actor: CustomerActivationActor
    browser: CustomerActivationBrowserContext = field(repr=False)
    candidate_code: str = field(repr=False)
    now: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.actor, CustomerActivationActor):
            raise TypeError("Customer activation actor is invalid")
        if not isinstance(self.browser, CustomerActivationBrowserContext):
            raise TypeError("Customer activation browser context is invalid")
        if not isinstance(self.candidate_code, str):
            raise TypeError("Registration OTP candidate is invalid")
        object.__setattr__(self, "now", _as_utc(self.now))

    def __repr__(self) -> str:
        return (
            "VerifyRegistrationOtp("
            "actor=<redacted>, browser=<redacted>, "
            "candidate_code=<redacted>, now=<injected>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RegistrationOtpCandidate:
    code: OtpCode | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.code is not None and not isinstance(self.code, OtpCode):
            raise TypeError("Registration OTP candidate is invalid")

    @property
    def requires_dummy_mac(self) -> bool:
        return self.code is None

    def __repr__(self) -> str:
        return (
            "RegistrationOtpCandidate("
            f"code=<{'malformed' if self.code is None else 'redacted'}>)"
        )


def parse_registration_otp_candidate(value: str) -> RegistrationOtpCandidate:
    if not isinstance(value, str):
        raise TypeError("Registration OTP candidate is invalid")
    try:
        code = OtpCode.from_user_input(value)
    except ValueError:
        return RegistrationOtpCandidate(code=None)
    return RegistrationOtpCandidate(code=code)


@dataclass(frozen=True, slots=True, repr=False, init=False)
class RegistrationOtpCandidateLookupKey:
    browser_binding_digest: OtpBrowserBindingDigest = field(repr=False)
    purpose: OtpPurpose = field(default=OtpPurpose.REGISTRATION, init=False)

    def __init__(self, browser_binding_digest: OtpBrowserBindingDigest) -> None:
        if not isinstance(browser_binding_digest, OtpBrowserBindingDigest):
            raise TypeError("Registration OTP lookup key is invalid")
        object.__setattr__(self, "browser_binding_digest", browser_binding_digest)
        object.__setattr__(self, "purpose", OtpPurpose.REGISTRATION)

    def __repr__(self) -> str:
        return (
            "RegistrationOtpCandidateLookupKey("
            "browser_binding_digest=<redacted>, purpose='REGISTRATION')"
        )


class RegistrationOtpVerificationOutcome(StrEnum):
    ACTIVATED = "ACTIVATED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    OTP_INVALID = "OTP_INVALID"
    CUSTOMER_ACTIVATION_CHANGED = "CUSTOMER_ACTIVATION_CHANGED"
    RATE_LIMITED = "RATE_LIMITED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    PREREQUISITE_FAILED = "PREREQUISITE_FAILED"


@dataclass(frozen=True, slots=True)
class RegistrationOtpVerificationResult:
    outcome: RegistrationOtpVerificationOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RegistrationOtpVerificationOutcome):
            raise TypeError("Registration OTP verification outcome is invalid")


class CustomerLifecycleStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class CustomerLifecycleState:
    status: CustomerLifecycleStatus
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CustomerLifecycleStatus):
            raise TypeError("Customer lifecycle status is invalid")
        created_at = _as_utc(self.created_at)
        updated_at = _as_utc(self.updated_at)
        activated_at = None if self.activated_at is None else _as_utc(self.activated_at)
        if updated_at < created_at:
            raise ValueError("Customer lifecycle timestamps are invalid")
        if self.status is CustomerLifecycleStatus.DRAFT and activated_at is not None:
            raise ValueError("Draft customer cannot have activation time")
        if self.status is CustomerLifecycleStatus.ACTIVE and activated_at is None:
            raise ValueError("Active customer requires activation time")
        if activated_at is not None and (
            activated_at < created_at or updated_at < activated_at
        ):
            raise ValueError("Customer activation timestamps are invalid")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "activated_at", activated_at)


class CustomerActivationTransitionOutcome(StrEnum):
    ACTIVATED = "ACTIVATED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class CustomerActivationTransitionResult:
    outcome: CustomerActivationTransitionOutcome
    state: CustomerLifecycleState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, CustomerActivationTransitionOutcome):
            raise TypeError("Customer activation transition outcome is invalid")
        if self.state is not None and not isinstance(
            self.state, CustomerLifecycleState
        ):
            raise TypeError("Customer lifecycle state is invalid")
        if self.outcome is CustomerActivationTransitionOutcome.MISSING:
            if self.state is not None:
                raise ValueError("Missing customer cannot have lifecycle state")
            return
        if (
            self.state is None
            or self.state.status is not CustomerLifecycleStatus.ACTIVE
        ):
            raise ValueError("Activation result requires active customer state")


def transition_customer_to_active(
    state: CustomerLifecycleState | None,
    *,
    now: datetime,
) -> CustomerActivationTransitionResult:
    current_time = _as_utc(now)
    if state is None:
        return CustomerActivationTransitionResult(
            outcome=CustomerActivationTransitionOutcome.MISSING
        )
    if not isinstance(state, CustomerLifecycleState):
        raise TypeError("Customer lifecycle state is invalid")
    if state.status is CustomerLifecycleStatus.ACTIVE:
        return CustomerActivationTransitionResult(
            outcome=CustomerActivationTransitionOutcome.ALREADY_ACTIVE,
            state=state,
        )
    if current_time < state.updated_at:
        raise ValueError("Customer activation time is invalid")
    active_state = CustomerLifecycleState(
        status=CustomerLifecycleStatus.ACTIVE,
        created_at=state.created_at,
        updated_at=current_time,
        activated_at=current_time,
    )
    return CustomerActivationTransitionResult(
        outcome=CustomerActivationTransitionOutcome.ACTIVATED,
        state=active_state,
    )


class ActivationAtomicMutation(StrEnum):
    CHALLENGE_CONSUMED = "CHALLENGE_CONSUMED"
    OTP_CONSUMED_EVENT_APPENDED = "OTP_CONSUMED_EVENT_APPENDED"
    CUSTOMER_ACTIVATED = "CUSTOMER_ACTIVATED"
    CENTRAL_AUDIT_APPENDED = "CENTRAL_AUDIT_APPENDED"
    CURRENT_SESSION_REPLACED = "CURRENT_SESSION_REPLACED"


ACTIVATION_ATOMIC_MUTATION_ORDER = tuple(ActivationAtomicMutation)


class ActivationSessionRotationScope(StrEnum):
    CURRENT_SESSION_ONLY = "CURRENT_SESSION_ONLY"


@dataclass(frozen=True, slots=True, repr=False)
class ActivationCsrfSecret:
    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._value, str) or not self._value:
            raise ValueError("Activation CSRF secret is invalid")

    def as_persistence_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "ActivationCsrfSecret(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-activation-csrf-secret>"


@dataclass(frozen=True, slots=True, repr=False)
class ActivationSessionSecrets:
    token: RawSessionToken = field(repr=False)
    csrf_secret: ActivationCsrfSecret = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.token, RawSessionToken):
            raise TypeError("Activation session token is invalid")
        if not isinstance(self.csrf_secret, ActivationCsrfSecret):
            raise TypeError("Activation CSRF secret is invalid")

    def __repr__(self) -> str:
        return "ActivationSessionSecrets(token=<redacted>, csrf_secret=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ActivationSafeDeviceMetadata:
    user_agent: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.user_agent is not None and not isinstance(self.user_agent, str):
            raise TypeError("Activation device metadata is invalid")
        object.__setattr__(self, "user_agent", truncate_user_agent(self.user_agent))

    def __repr__(self) -> str:
        return "ActivationSafeDeviceMetadata(user_agent=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ActivationSessionRotation:
    previous_session_id: UUID = field(repr=False)
    replacement_session_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    active_shop_id: UUID | None = field(repr=False)
    safe_device_metadata: ActivationSafeDeviceMetadata = field(repr=False)
    _previous_secrets: ActivationSessionSecrets = field(repr=False)
    _replacement_secrets: ActivationSessionSecrets = field(repr=False)
    scope: ActivationSessionRotationScope = field(
        default=ActivationSessionRotationScope.CURRENT_SESSION_ONLY,
        init=False,
    )

    def __post_init__(self) -> None:
        _require_uuid(self.previous_session_id, field_name="previous_session_id")
        _require_uuid(self.replacement_session_id, field_name="replacement_session_id")
        _require_uuid(self.user_id, field_name="rotation_user_id")
        if self.previous_session_id == self.replacement_session_id:
            raise ValueError("Activation replacement session must be fresh")
        if self.active_shop_id is not None:
            _require_uuid(self.active_shop_id, field_name="active_shop_id")
        if not isinstance(self.safe_device_metadata, ActivationSafeDeviceMetadata):
            raise TypeError("Activation device metadata is invalid")
        if not isinstance(self._previous_secrets, ActivationSessionSecrets):
            raise TypeError("Previous activation session secrets are invalid")
        if not isinstance(self._replacement_secrets, ActivationSessionSecrets):
            raise TypeError("Replacement activation session secrets are invalid")

    def replacement_csrf_secret_for_persistence(self) -> ActivationCsrfSecret:
        return self._replacement_secrets.csrf_secret

    def __repr__(self) -> str:
        return (
            "ActivationSessionRotation("
            "previous_session_id=<redacted>, replacement_session_id=<redacted>, "
            "user_id=<redacted>, active_shop_id=<redacted>, "
            "safe_device_metadata=<redacted>, previous_secrets=<redacted>, "
            "replacement_secrets=<redacted>, scope='CURRENT_SESSION_ONLY')"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PreparedCustomerActivation:
    _rotation: ActivationSessionRotation = field(repr=False)
    mutations: tuple[ActivationAtomicMutation, ...] = field(
        default=ACTIVATION_ATOMIC_MUTATION_ORDER,
        init=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self._rotation, ActivationSessionRotation):
            raise TypeError("Activation session rotation is invalid")

    def __repr__(self) -> str:
        return (
            "PreparedCustomerActivation("
            "rotation=<redacted>, mutations=<atomic-activation-order>)"
        )


@dataclass(frozen=True, slots=True)
class CustomerActivationAlreadyActive:
    pass


@dataclass(frozen=True, slots=True, repr=False)
class CommittedCustomerActivation:
    _replacement_token: RawSessionToken = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._replacement_token, RawSessionToken):
            raise TypeError("Committed activation session token is invalid")

    def release_cookie_token(self) -> RawSessionToken:
        return self._replacement_token

    def __repr__(self) -> str:
        return "CommittedCustomerActivation(replacement_token=<redacted>)"


def mark_customer_activation_committed(
    prepared: PreparedCustomerActivation,
) -> CommittedCustomerActivation:
    """Called only by the outer transaction owner after a successful commit."""
    if not isinstance(prepared, PreparedCustomerActivation):
        raise TypeError("Prepared customer activation result is invalid")
    return CommittedCustomerActivation(
        _replacement_token=prepared._rotation._replacement_secrets.token,
    )


type CustomerActivationAtomicResult = (
    PreparedCustomerActivation | CustomerActivationAlreadyActive
)


TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER = "TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER"


class OrdinaryTelegramUnlinkOutcome(StrEnum):
    INHERITED_UNLINK_ALLOWED = "INHERITED_UNLINK_ALLOWED"
    ACTIVE_CUSTOMER_DENIED = "ACTIVE_CUSTOMER_DENIED"


@dataclass(frozen=True, slots=True)
class OrdinaryTelegramUnlinkDecision:
    outcome: OrdinaryTelegramUnlinkOutcome

    @property
    def mutation_allowed(self) -> bool:
        return self.outcome is OrdinaryTelegramUnlinkOutcome.INHERITED_UNLINK_ALLOWED

    @property
    def error_code(self) -> str | None:
        if self.outcome is OrdinaryTelegramUnlinkOutcome.ACTIVE_CUSTOMER_DENIED:
            return TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER
        return None


def decide_ordinary_telegram_unlink(
    customer_status: CustomerLifecycleStatus | None,
) -> OrdinaryTelegramUnlinkDecision:
    if customer_status is not None and not isinstance(
        customer_status, CustomerLifecycleStatus
    ):
        raise TypeError("Customer lifecycle status is invalid")
    outcome = (
        OrdinaryTelegramUnlinkOutcome.ACTIVE_CUSTOMER_DENIED
        if customer_status is CustomerLifecycleStatus.ACTIVE
        else OrdinaryTelegramUnlinkOutcome.INHERITED_UNLINK_ALLOWED
    )
    return OrdinaryTelegramUnlinkDecision(outcome=outcome)


class ProtectedTelegramRelinkOutcome(StrEnum):
    RELINKED = "RELINKED"
    CHAT_COLLISION = "CHAT_COLLISION"


class ProtectedTelegramRelinkLinkDisposition(StrEnum):
    GENERATION_REPLACED = "GENERATION_REPLACED"
    CURRENT_LINK_PRESERVED = "CURRENT_LINK_PRESERVED"


@dataclass(frozen=True, slots=True)
class ProtectedActiveTelegramRelinkResult:
    outcome: ProtectedTelegramRelinkOutcome
    link_disposition: ProtectedTelegramRelinkLinkDisposition = field(init=False)
    invalidated_otp_purposes: tuple[OtpPurpose, ...] = field(init=False)
    customer_status: CustomerLifecycleStatus = field(
        default=CustomerLifecycleStatus.ACTIVE,
        init=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProtectedTelegramRelinkOutcome):
            raise TypeError("Protected Telegram relink outcome is invalid")
        if self.outcome is ProtectedTelegramRelinkOutcome.RELINKED:
            disposition = ProtectedTelegramRelinkLinkDisposition.GENERATION_REPLACED
            purposes = (OtpPurpose.LOGIN, OtpPurpose.REGISTRATION)
        else:
            disposition = ProtectedTelegramRelinkLinkDisposition.CURRENT_LINK_PRESERVED
            purposes = ()
        object.__setattr__(self, "link_disposition", disposition)
        object.__setattr__(self, "invalidated_otp_purposes", purposes)


class RegistrationReadinessComponent(StrEnum):
    TELEGRAM_LINK = "TELEGRAM_LINK"
    OFFER_ACCEPTANCE = "OFFER_ACCEPTANCE"
    CUSTOMER_IDENTITY = "CUSTOMER_IDENTITY"
    CUSTOMER_DOCUMENT = "CUSTOMER_DOCUMENT"


class RegistrationReadinessComponentStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class RegistrationReadinessState(StrEnum):
    READY_FOR_OTP = "READY_FOR_OTP"
    INCOMPLETE = "INCOMPLETE"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True, slots=True, repr=False)
class RegistrationReadinessSnapshot:
    user_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    telegram_link_id: UUID = field(repr=False)
    telegram_linked_at: datetime = field(repr=False)
    registration_offer_acceptance_id: UUID = field(repr=False)
    customer_identity_revision: IdentityRevision
    customer_document_id: UUID = field(repr=False)
    browser_binding_digest: OtpBrowserBindingDigest = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.user_id, field_name="user_id")
        _require_uuid(self.customer_id, field_name="customer_id")
        _require_uuid(self.telegram_link_id, field_name="telegram_link_id")
        object.__setattr__(
            self,
            "telegram_linked_at",
            _as_utc(self.telegram_linked_at),
        )
        _require_uuid(
            self.registration_offer_acceptance_id,
            field_name="registration_offer_acceptance_id",
        )
        if not isinstance(self.customer_identity_revision, IdentityRevision):
            raise ValueError("Customer identity revision is invalid")
        _require_uuid(
            self.customer_document_id,
            field_name="customer_document_id",
        )
        if not isinstance(self.browser_binding_digest, OtpBrowserBindingDigest):
            raise ValueError("OTP browser binding is invalid")

    def __repr__(self) -> str:
        return (
            "RegistrationReadinessSnapshot("
            "user_id=<redacted>, customer_id=<redacted>, "
            "telegram_link_id=<redacted>, telegram_linked_at=<redacted>, "
            "registration_offer_acceptance_id=<redacted>, "
            f"customer_identity_revision={self.customer_identity_revision.value!r}, "
            "customer_document_id=<redacted>, "
            "browser_binding_digest=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class RegistrationReadinessComponentView:
    component: RegistrationReadinessComponent
    status: RegistrationReadinessComponentStatus

    def __post_init__(self) -> None:
        if not isinstance(self.component, RegistrationReadinessComponent):
            raise TypeError("Registration readiness component is invalid")
        if not isinstance(self.status, RegistrationReadinessComponentStatus):
            raise TypeError("Registration readiness status is invalid")


@dataclass(frozen=True, slots=True)
class RegistrationReadinessView:
    state: RegistrationReadinessState
    components: tuple[RegistrationReadinessComponentView, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, RegistrationReadinessState):
            raise TypeError("Registration readiness state is invalid")
        if not isinstance(self.components, tuple) or any(
            not isinstance(component, RegistrationReadinessComponentView)
            for component in self.components
        ):
            raise TypeError("Registration readiness components are invalid")
        expected = tuple(RegistrationReadinessComponent)
        actual = tuple(component.component for component in self.components)
        if actual != expected:
            raise ValueError("Registration readiness component set is invalid")
        all_complete = all(
            component.status is RegistrationReadinessComponentStatus.COMPLETE
            for component in self.components
        )
        if self.state is RegistrationReadinessState.READY_FOR_OTP and not all_complete:
            raise ValueError("Ready registration state requires complete components")
        if self.state is RegistrationReadinessState.INCOMPLETE and all_complete:
            raise ValueError(
                "Incomplete registration state requires an incomplete item"
            )


def _require_uuid(value: object, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"Registration snapshot {field_name} is invalid")


def _validate_request_context(
    *,
    actor: CustomerActivationActor,
    browser: CustomerActivationBrowserContext,
    trusted_client_ip: ResolvedClientIp,
    now: datetime,
) -> None:
    if not isinstance(actor, CustomerActivationActor):
        raise TypeError("Customer activation actor is invalid")
    if not isinstance(browser, CustomerActivationBrowserContext):
        raise TypeError("Customer activation browser context is invalid")
    if not isinstance(trusted_client_ip, ResolvedClientIp):
        raise TypeError("Trusted client IP is invalid")
    _as_utc(now)


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Registration snapshot timestamp is invalid")
    return value.astimezone(UTC)
