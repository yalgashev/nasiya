from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.auth.rate_limit import AuthRateLimiter, RateLimitResult
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
    OtpPurpose,
)
from app.otp.crypto import (
    OtpBrowserBindingDigest,
    compute_otp_code_mac,
)
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.repository import (
    OtpChallengeInsertConflict,
    append_challenge_event,
    cancel_dispatch,
    create_pending_challenge,
    create_pending_dispatch,
    expire_challenge,
    invalidate_challenge,
    load_dispatch_by_challenge_for_update,
    load_outstanding_challenge_by_browser_for_update,
    load_outstanding_challenge_by_user_for_update,
    load_outstanding_challenges_by_user_for_update,
    supersede_challenge,
)
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink

OTP_LOGIN_ISSUE_PHONE_SCOPE: Final = "otp-login-issue:phone"
OTP_LOGIN_ISSUE_USER_SCOPE: Final = "otp-login-issue:user"
OTP_LOGIN_ISSUE_IP_SCOPE: Final = "otp-login-issue:ip"
OTP_LOGIN_NEW_CODE_USER_SCOPE: Final = "otp-login-new-code:user"
OTP_LOGIN_NEW_CODE_IP_SCOPE: Final = "otp-login-new-code:ip"
_DUMMY_CHALLENGE_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
_DUMMY_USER_ID: Final = UUID("00000000-0000-4000-8000-000000000002")
_DUMMY_CODE: Final = OtpCode("000000")


@dataclass(frozen=True, repr=False)
class OtpEligibleTarget:
    user: User
    telegram_link: TelegramLink
    canonical_phone: str

    def __repr__(self) -> str:
        return "OtpEligibleTarget(user=<User>, telegram_link=<TelegramLink>)"


@dataclass(frozen=True, repr=False)
class OtpEligibilityResult:
    outcome: OtpInternalOutcome
    target: OtpEligibleTarget | None = None

    @property
    def eligible(self) -> bool:
        return self.target is not None

    def __repr__(self) -> str:
        return (
            "OtpEligibilityResult("
            f"outcome={self.outcome.value}, target=<OtpEligibleTarget | None>)"
        )


@dataclass(frozen=True, repr=False)
class OtpIssueResult:
    outcome: OtpInternalOutcome
    challenge: OtpChallenge | None = None
    dispatch: OtpDispatch | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome is OtpInternalOutcome.OTP_PENDING

    def __repr__(self) -> str:
        return (
            "OtpIssueResult("
            f"outcome={self.outcome.value}, "
            "challenge=<OtpChallenge | None>, dispatch=<OtpDispatch | None>)"
        )


def lookup_login_otp_eligibility(
    session: Session,
    *,
    phone_input: str,
    lock_rows: bool = False,
) -> OtpEligibilityResult:
    try:
        canonical_phone = normalize_uzbekistan_phone(phone_input)
    except PhoneNormalizationError:
        return OtpEligibilityResult(outcome=OtpInternalOutcome.OTP_NOT_ELIGIBLE)

    user_statement = select(User).where(User.phone == canonical_phone)
    if lock_rows:
        user_statement = user_statement.with_for_update()
    user = session.scalar(user_statement)
    if user is None or not user.is_active:
        return OtpEligibilityResult(outcome=OtpInternalOutcome.OTP_NOT_ELIGIBLE)

    link_statement = select(TelegramLink).where(
        TelegramLink.user_id == user.id,
        TelegramLink.telegram_chat_id.is_not(None),
        TelegramLink.unlinked_at.is_(None),
    )
    if lock_rows:
        link_statement = link_statement.with_for_update()
    link = session.scalar(link_statement)
    if link is None:
        return OtpEligibilityResult(outcome=OtpInternalOutcome.OTP_NOT_ELIGIBLE)

    return OtpEligibilityResult(
        outcome=OtpInternalOutcome.OTP_PENDING,
        target=OtpEligibleTarget(
            user=user,
            telegram_link=link,
            canonical_phone=canonical_phone,
        ),
    )


def record_login_otp_issue_limits(
    session: Session,
    settings: Settings,
    *,
    canonical_phone: str | None,
    client_ip: ResolvedClientIp,
    now: datetime,
    user_id: UUID | None = None,
    new_code: bool = False,
) -> RateLimitResult:
    current_time = _as_utc(now)
    limiter = AuthRateLimiter(db=session, settings=settings)
    results: list[RateLimitResult] = []

    if canonical_phone is not None:
        results.append(
            limiter.record_failure(
                OTP_LOGIN_ISSUE_PHONE_SCOPE,
                f"otp-login-issue:phone:{canonical_phone}",
                current_time,
                settings.otp_login_rate_limit_phone_attempts,
                settings.otp_login_rate_limit_window_seconds,
            )
        )

    ip_scope = OTP_LOGIN_NEW_CODE_IP_SCOPE if new_code else OTP_LOGIN_ISSUE_IP_SCOPE
    ip_key_prefix = "otp-login-new-code:ip" if new_code else "otp-login-issue:ip"
    results.append(
        limiter.record_failure(
            ip_scope,
            f"{ip_key_prefix}:{client_ip.as_hmac_input()}",
            current_time,
            settings.otp_login_rate_limit_ip_attempts,
            settings.otp_login_rate_limit_window_seconds,
        )
    )

    if user_id is not None:
        user_scope = (
            OTP_LOGIN_NEW_CODE_USER_SCOPE if new_code else OTP_LOGIN_ISSUE_USER_SCOPE
        )
        user_key_prefix = (
            "otp-login-new-code:user" if new_code else "otp-login-issue:user"
        )
        results.append(
            limiter.record_failure(
                user_scope,
                f"{user_key_prefix}:{user_id}",
                current_time,
                settings.otp_login_rate_limit_user_attempts,
                settings.otp_login_rate_limit_window_seconds,
            )
        )

    blocked = next((result for result in results if not result.allowed), None)
    if blocked is not None:
        return blocked
    return results[-1]


def record_login_otp_user_issue_limit(
    session: Session,
    settings: Settings,
    *,
    user_id: UUID,
    now: datetime,
    new_code: bool = False,
) -> RateLimitResult:
    current_time = _as_utc(now)
    limiter = AuthRateLimiter(db=session, settings=settings)
    user_scope = (
        OTP_LOGIN_NEW_CODE_USER_SCOPE if new_code else OTP_LOGIN_ISSUE_USER_SCOPE
    )
    user_key_prefix = "otp-login-new-code:user" if new_code else "otp-login-issue:user"
    return limiter.record_failure(
        user_scope,
        f"{user_key_prefix}:{user_id}",
        current_time,
        settings.otp_login_rate_limit_user_attempts,
        settings.otp_login_rate_limit_window_seconds,
    )


def perform_neutral_otp_request_work(
    otp_hmac_key: SecretStr,
) -> None:
    compute_otp_code_mac(
        otp_hmac_key=otp_hmac_key,
        challenge_id=_DUMMY_CHALLENGE_ID,
        user_id=_DUMMY_USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=_DUMMY_CODE,
    )


def request_login_otp(
    session: Session,
    settings: Settings,
    *,
    phone_input: str,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    client_ip: ResolvedClientIp,
    locale: str,
    now: datetime,
    dummy_work: Callable[[SecretStr], None] | None = None,
) -> OtpIssueResult:
    current_time = _as_utc(now)
    try:
        otp_hmac_key = settings.require_otp_hmac_key()
    except Exception:
        return OtpIssueResult(outcome=OtpInternalOutcome.OTP_CONFIGURATION_UNAVAILABLE)

    canonical_phone = _normalize_phone_or_none(phone_input)
    limit_result = record_login_otp_issue_limits(
        session,
        settings,
        canonical_phone=canonical_phone,
        client_ip=client_ip,
        now=current_time,
    )
    if not limit_result.allowed:
        return OtpIssueResult(outcome=OtpInternalOutcome.RATE_LIMITED)

    _run_dummy_work(otp_hmac_key, dummy_work)
    eligibility = lookup_login_otp_eligibility(
        session,
        phone_input=phone_input,
        lock_rows=True,
    )
    if eligibility.target is None:
        return OtpIssueResult(outcome=OtpInternalOutcome.OTP_NOT_ELIGIBLE)

    user_limit_result = record_login_otp_user_issue_limit(
        session,
        settings,
        now=current_time,
        user_id=eligibility.target.user.id,
    )
    if not user_limit_result.allowed:
        return OtpIssueResult(outcome=OtpInternalOutcome.RATE_LIMITED)

    return _issue_challenge_for_target(
        session,
        settings=settings,
        target=eligibility.target,
        browser_binding_digest=browser_binding_digest,
        locale=locale,
        now=current_time,
    )


def request_new_login_code(
    session: Session,
    settings: Settings,
    *,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    client_ip: ResolvedClientIp,
    locale: str,
    now: datetime,
    dummy_work: Callable[[SecretStr], None] | None = None,
) -> OtpIssueResult:
    current_time = _as_utc(now)
    try:
        otp_hmac_key = settings.require_otp_hmac_key()
    except Exception:
        return OtpIssueResult(outcome=OtpInternalOutcome.OTP_CONFIGURATION_UNAVAILABLE)

    _run_dummy_work(otp_hmac_key, dummy_work)
    current_challenge = load_outstanding_challenge_by_browser_for_update(
        session,
        browser_binding_digest=browser_binding_digest,
        purpose=OtpPurpose.LOGIN,
    )
    if current_challenge is None or current_challenge.user_id is None:
        return OtpIssueResult(outcome=OtpInternalOutcome.OTP_NOT_ELIGIBLE)
    if (
        current_challenge.created_at
        + timedelta(seconds=settings.otp_login_resend_cooldown_seconds)
        > current_time
    ):
        return OtpIssueResult(outcome=OtpInternalOutcome.RATE_LIMITED)

    limit_result = record_login_otp_issue_limits(
        session,
        settings,
        canonical_phone=None,
        client_ip=client_ip,
        now=current_time,
        user_id=current_challenge.user_id,
        new_code=True,
    )
    if not limit_result.allowed:
        return OtpIssueResult(outcome=OtpInternalOutcome.RATE_LIMITED)

    target = _target_from_challenge_snapshot(
        session,
        current_challenge=current_challenge,
    )
    if target is None:
        return OtpIssueResult(outcome=OtpInternalOutcome.OTP_NOT_ELIGIBLE)

    return _issue_challenge_for_target(
        session,
        settings=settings,
        target=target,
        browser_binding_digest=browser_binding_digest,
        locale=locale,
        now=current_time,
    )


def invalidate_login_otp_challenges_for_link_change(
    session: Session,
    *,
    user_id: UUID,
    now: datetime,
) -> int:
    current_time = _as_utc(now)
    challenges = load_outstanding_challenges_by_user_for_update(
        session,
        user_id=user_id,
        purpose=OtpPurpose.LOGIN,
    )
    for challenge in challenges:
        invalidate_challenge(session, challenge=challenge, now=current_time)
        _cancel_dispatch_if_open(session, challenge_id=challenge.id, now=current_time)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=user_id,
            action=OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE,
            occurred_at=current_time,
        )
    return len(challenges)


def _issue_challenge_for_target(
    session: Session,
    *,
    settings: Settings,
    target: OtpEligibleTarget,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    locale: str,
    now: datetime,
) -> OtpIssueResult:
    current_time = _as_utc(now)
    _terminalize_existing_outstanding(
        session,
        settings=settings,
        target=target,
        browser_binding_digest=browser_binding_digest,
        now=current_time,
    )
    try:
        challenge = create_pending_challenge(
            session,
            user_id=target.user.id,
            telegram_link_id=target.telegram_link.id,
            telegram_linked_at=target.telegram_link.linked_at,
            browser_binding_digest=browser_binding_digest,
            now=current_time,
        )
    except OtpChallengeInsertConflict:
        return OtpIssueResult(outcome=OtpInternalOutcome.OTP_PENDING)

    dispatch = create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale=locale,
        now=current_time,
    )
    append_challenge_event(
        session,
        challenge_id=challenge.id,
        user_id=target.user.id,
        action=OtpChallengeEventAction.ISSUED,
        occurred_at=current_time,
    )
    return OtpIssueResult(
        outcome=OtpInternalOutcome.OTP_PENDING,
        challenge=challenge,
        dispatch=dispatch,
    )


def _terminalize_existing_outstanding(
    session: Session,
    *,
    settings: Settings,
    target: OtpEligibleTarget,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    now: datetime,
) -> None:
    candidates = [
        load_outstanding_challenge_by_user_for_update(
            session,
            user_id=target.user.id,
            purpose=OtpPurpose.LOGIN,
        ),
        load_outstanding_challenge_by_browser_for_update(
            session,
            browser_binding_digest=browser_binding_digest,
            purpose=OtpPurpose.LOGIN,
        ),
    ]
    unique_challenges = {
        challenge.id: challenge for challenge in candidates if challenge is not None
    }
    for challenge in unique_challenges.values():
        if _is_stale_challenge(
            challenge,
            now=now,
            ttl_seconds=settings.otp_login_ttl_seconds,
        ):
            expire_challenge(session, challenge=challenge, now=now)
            event_action = OtpChallengeEventAction.EXPIRED
        else:
            supersede_challenge(session, challenge=challenge, now=now)
            event_action = OtpChallengeEventAction.SUPERSEDED
        _cancel_dispatch_if_open(session, challenge_id=challenge.id, now=now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            action=event_action,
            occurred_at=now,
        )


def _cancel_dispatch_if_open(
    session: Session,
    *,
    challenge_id: UUID,
    now: datetime,
) -> None:
    dispatch = load_dispatch_by_challenge_for_update(session, challenge_id=challenge_id)
    if dispatch is None or dispatch.status not in {
        OtpDispatchStatus.PENDING.value,
        OtpDispatchStatus.PREPARED.value,
    }:
        return
    cancel_dispatch(session, dispatch=dispatch, now=now)


def _target_from_challenge_snapshot(
    session: Session,
    *,
    current_challenge: OtpChallenge,
) -> OtpEligibleTarget | None:
    if (
        current_challenge.user_id is None
        or current_challenge.telegram_link_id is None
        or current_challenge.telegram_linked_at is None
    ):
        return None

    user = session.get(User, current_challenge.user_id, with_for_update=True)
    if user is None or not user.is_active:
        return None
    link = session.get(
        TelegramLink,
        current_challenge.telegram_link_id,
        with_for_update=True,
    )
    if (
        link is None
        or link.telegram_chat_id is None
        or link.unlinked_at is not None
        or link.linked_at != current_challenge.telegram_linked_at
    ):
        return None
    return OtpEligibleTarget(
        user=user,
        telegram_link=link,
        canonical_phone=user.phone,
    )


def _is_stale_challenge(
    challenge: OtpChallenge,
    *,
    now: datetime,
    ttl_seconds: int,
) -> bool:
    if challenge.status == OtpChallengeStatus.ACTIVE.value:
        return challenge.expires_at is not None and challenge.expires_at <= now
    return challenge.created_at + timedelta(seconds=ttl_seconds) <= now


def _run_dummy_work(
    otp_hmac_key: SecretStr,
    dummy_work: Callable[[SecretStr], None] | None,
) -> None:
    if dummy_work is None:
        perform_neutral_otp_request_work(otp_hmac_key)
        return
    dummy_work(otp_hmac_key)


def _normalize_phone_or_none(phone_input: str) -> str | None:
    try:
        return normalize_uzbekistan_phone(phone_input)
    except PhoneNormalizationError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OTP issuance timestamps must be timezone-aware")
    return value.astimezone(UTC)
