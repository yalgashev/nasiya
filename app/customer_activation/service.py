from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from app.auth.deps import CurrentSessionContext, CurrentSessionStatus
from app.auth.models import User
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.customer.ports import CustomerLifecycleStatus
from app.customer.repository import (
    get_existing_own_customer_status,
    load_existing_own_customer,
    lock_existing_own_customer_for_update,
)
from app.customer_activation.contracts import (
    CurrentRegistrationAcceptanceSelection,
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    CustomerAlreadyActive,
    RegistrationOtpCooldown,
    RegistrationOtpPendingDelivery,
    RegistrationOtpPrerequisiteFailed,
    RegistrationOtpRateLimited,
    RegistrationOtpRequestResult,
    RegistrationPrerequisiteError,
    RegistrationReadinessComponent,
    RegistrationReadinessComponentStatus,
    RegistrationReadinessComponentView,
    RegistrationReadinessSnapshot,
    RegistrationReadinessState,
    RegistrationReadinessView,
)
from app.customer_activation.rate_limit import RegistrationIssuanceRateLimitPolicy
from app.customer_activation.repository import (
    SqlAlchemyCustomerDocumentReadiness,
    SqlAlchemyCustomerIdentityReadiness,
    SqlAlchemyRegistrationOfferReadiness,
)
from app.customer_document.service import CustomerDocumentCompletenessService
from app.customer_identity.crypto import CustomerIdentityCryptoConfig
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.service import CustomerIdentityCompletenessService
from app.offers.repository import SqlAlchemyHasAcceptedCurrentRegistrationOffer
from app.otp.contracts import OtpChallengeEventAction, OtpPurpose
from app.otp.crypto import derive_browser_binding_digest
from app.otp.repository import (
    OtpChallengeInsertConflict,
    append_challenge_event,
    create_pending_dispatch,
    create_pending_registration_challenge,
    lock_outstanding_challenge_set_by_user,
    supersede_and_cancel_same_purpose_challenges,
)
from app.otp.web_presentation import OtpWebLanguage, get_otp_dispatch_locale
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.repository import (
    get_telegram_link_by_user_for_update,
    has_active_telegram_link,
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
