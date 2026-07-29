from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.auth.models import User
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
    OtpPurpose,
)
from app.otp.crypto import OtpBrowserBindingDigest, verify_otp_code_mac
from app.otp.models import OtpChallenge
from app.otp.repository import (
    append_challenge_event,
    burn_challenge,
    cancel_dispatch,
    consume_challenge,
    expire_challenge,
    increment_challenge_failed_attempts,
    invalidate_challenge,
    load_dispatch_by_challenge_for_update,
    load_verification_candidate_by_browser_for_update,
)
from app.settings import OtpHmacKeySettingsError, Settings
from app.telegram.models import TelegramLink

_DUMMY_CHALLENGE_ID: Final = UUID("00000000-0000-4000-8000-000000000101")
_DUMMY_USER_ID: Final = UUID("00000000-0000-4000-8000-000000000102")
_DUMMY_CODE: Final = OtpCode("000000")
_DUMMY_STORED_MAC: Final = "0" * 64

OtpVerifyDummyWork = Callable[[SecretStr, OtpCode | None], None]
OtpVerifyMacVerifier = Callable[..., bool]


@dataclass(frozen=True, repr=False)
class OtpVerificationCandidateCheck:
    outcome: OtpInternalOutcome
    challenge: OtpChallenge | None = None
    code: OtpCode | None = None
    mac_matches: bool = False

    @property
    def accepted_for_consume(self) -> bool:
        return self.mac_matches and self.challenge is not None and self.code is not None

    def __repr__(self) -> str:
        return (
            "OtpVerificationCandidateCheck("
            f"outcome={self.outcome.value}, "
            "challenge=<OtpChallenge | None>, code=<OtpCode | None>, "
            f"mac_matches={self.mac_matches!r})"
        )


@dataclass(frozen=True, repr=False)
class OtpVerificationResult:
    outcome: OtpInternalOutcome
    user_id: UUID | None = None

    @property
    def consumed(self) -> bool:
        return self.outcome is OtpInternalOutcome.OTP_CONSUMED

    def __repr__(self) -> str:
        return (
            "OtpVerificationResult("
            f"outcome={self.outcome.value}, user_id=<UUID | None>)"
        )


def verify_login_otp(
    session: Session,
    settings: Settings,
    *,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    candidate_code_input: str,
    now: datetime,
    dummy_work: OtpVerifyDummyWork | None = None,
    mac_verifier: OtpVerifyMacVerifier = verify_otp_code_mac,
) -> OtpVerificationResult:
    current_time = _as_utc(now)
    candidate_check = check_login_otp_candidate(
        session,
        settings,
        browser_binding_digest=browser_binding_digest,
        candidate_code_input=candidate_code_input,
        now=current_time,
        dummy_work=dummy_work,
        mac_verifier=mac_verifier,
    )
    if candidate_check.outcome is OtpInternalOutcome.OTP_PENDING:
        return _consume_verified_candidate(
            session,
            challenge=candidate_check.challenge,
            now=current_time,
        )
    if _is_wrong_active_candidate(candidate_check):
        return _record_wrong_candidate_attempt(
            session,
            settings=settings,
            challenge=candidate_check.challenge,
            now=current_time,
        )
    return OtpVerificationResult(outcome=candidate_check.outcome)


def check_login_otp_candidate(
    session: Session,
    settings: Settings,
    *,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    candidate_code_input: str,
    now: datetime,
    dummy_work: OtpVerifyDummyWork | None = None,
    mac_verifier: OtpVerifyMacVerifier = verify_otp_code_mac,
) -> OtpVerificationCandidateCheck:
    current_time = _as_utc(now)
    try:
        otp_hmac_key = settings.require_otp_hmac_key()
    except OtpHmacKeySettingsError:
        return OtpVerificationCandidateCheck(
            outcome=OtpInternalOutcome.OTP_CONFIGURATION_UNAVAILABLE
        )

    candidate_code = _parse_candidate_or_none(candidate_code_input)
    if candidate_code is None:
        _run_dummy_work(otp_hmac_key, None, dummy_work)
        return OtpVerificationCandidateCheck(outcome=OtpInternalOutcome.OTP_INVALID)

    challenge = load_verification_candidate_by_browser_for_update(
        session,
        browser_binding_digest=browser_binding_digest,
        purpose=OtpPurpose.LOGIN,
    )
    if challenge is None:
        _run_dummy_work(otp_hmac_key, candidate_code, dummy_work)
        return OtpVerificationCandidateCheck(outcome=OtpInternalOutcome.OTP_INVALID)

    if not _is_active_verification_candidate(challenge):
        _run_dummy_work(otp_hmac_key, candidate_code, dummy_work)
        return OtpVerificationCandidateCheck(
            outcome=_inactive_candidate_outcome(challenge, now=current_time),
            challenge=challenge,
            code=candidate_code,
        )
    terminal_outcome = _terminalize_unverifiable_active_candidate(
        session,
        settings=settings,
        challenge=challenge,
        now=current_time,
    )
    if terminal_outcome is not None:
        _run_dummy_work(otp_hmac_key, candidate_code, dummy_work)
        return OtpVerificationCandidateCheck(
            outcome=terminal_outcome,
            challenge=challenge,
            code=candidate_code,
        )
    if not _revalidate_current_login_target(session, challenge=challenge):
        _invalidate_verification_candidate(
            session, challenge=challenge, now=current_time
        )
        _run_dummy_work(otp_hmac_key, candidate_code, dummy_work)
        return OtpVerificationCandidateCheck(
            outcome=OtpInternalOutcome.OTP_LINK_CHANGED,
            challenge=challenge,
            code=candidate_code,
        )

    mac_matches = bool(
        mac_verifier(
            otp_hmac_key=otp_hmac_key,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            purpose=OtpPurpose.LOGIN,
            code=candidate_code,
            stored_mac=challenge.code_mac,
        )
    )
    if not mac_matches:
        _run_dummy_work(otp_hmac_key, candidate_code, dummy_work)
        return OtpVerificationCandidateCheck(
            outcome=OtpInternalOutcome.OTP_INVALID,
            challenge=challenge,
            code=candidate_code,
        )
    return OtpVerificationCandidateCheck(
        outcome=OtpInternalOutcome.OTP_PENDING,
        challenge=challenge,
        code=candidate_code,
        mac_matches=True,
    )


def _record_wrong_candidate_attempt(
    session: Session,
    *,
    settings: Settings,
    challenge: OtpChallenge | None,
    now: datetime,
) -> OtpVerificationResult:
    if challenge is None:
        return OtpVerificationResult(outcome=OtpInternalOutcome.OTP_INVALID)
    increment_challenge_failed_attempts(
        session,
        challenge=challenge,
        now=now,
        max_attempts=settings.otp_login_max_verify_attempts,
    )
    append_challenge_event(
        session,
        challenge_id=challenge.id,
        user_id=challenge.user_id,
        action=OtpChallengeEventAction.VERIFY_FAILED,
        occurred_at=now,
        safe_code="OTP_INVALID",
    )
    if challenge.status != OtpChallengeStatus.BURNED.value:
        return OtpVerificationResult(outcome=OtpInternalOutcome.OTP_INVALID)
    append_challenge_event(
        session,
        challenge_id=challenge.id,
        user_id=challenge.user_id,
        action=OtpChallengeEventAction.BURNED,
        occurred_at=now,
        safe_code="OTP_BURNED",
    )
    return OtpVerificationResult(outcome=OtpInternalOutcome.OTP_BURNED)


def _consume_verified_candidate(
    session: Session,
    *,
    challenge: OtpChallenge | None,
    now: datetime,
) -> OtpVerificationResult:
    if challenge is None or challenge.user_id is None:
        return OtpVerificationResult(outcome=OtpInternalOutcome.OTP_INVALID)
    consume_challenge(session, challenge=challenge, now=now)
    append_challenge_event(
        session,
        challenge_id=challenge.id,
        user_id=challenge.user_id,
        action=OtpChallengeEventAction.CONSUMED,
        occurred_at=now,
    )
    return OtpVerificationResult(
        outcome=OtpInternalOutcome.OTP_CONSUMED,
        user_id=challenge.user_id,
    )


def perform_neutral_otp_verify_work(
    otp_hmac_key: SecretStr,
    candidate_code: OtpCode | None = None,
) -> None:
    verify_otp_code_mac(
        otp_hmac_key=otp_hmac_key,
        challenge_id=_DUMMY_CHALLENGE_ID,
        user_id=_DUMMY_USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=candidate_code or _DUMMY_CODE,
        stored_mac=_DUMMY_STORED_MAC,
    )


def _parse_candidate_or_none(candidate_code_input: str) -> OtpCode | None:
    try:
        return OtpCode.from_user_input(candidate_code_input)
    except ValueError:
        return None


def _run_dummy_work(
    otp_hmac_key: SecretStr,
    candidate_code: OtpCode | None,
    dummy_work: OtpVerifyDummyWork | None,
) -> None:
    if dummy_work is None:
        perform_neutral_otp_verify_work(otp_hmac_key, candidate_code)
        return
    dummy_work(otp_hmac_key, candidate_code)


def _terminalize_unverifiable_active_candidate(
    session: Session,
    *,
    settings: Settings,
    challenge: OtpChallenge,
    now: datetime,
) -> OtpInternalOutcome | None:
    if challenge.expires_at is not None and challenge.expires_at <= now:
        expire_challenge(session, challenge=challenge, now=now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            action=OtpChallengeEventAction.EXPIRED,
            occurred_at=now,
            safe_code="OTP_EXPIRED",
        )
        return OtpInternalOutcome.OTP_EXPIRED
    if challenge.failed_attempts >= settings.otp_login_max_verify_attempts:
        burn_challenge(session, challenge=challenge, now=now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            action=OtpChallengeEventAction.BURNED,
            occurred_at=now,
            safe_code="OTP_BURNED",
        )
        return OtpInternalOutcome.OTP_BURNED
    return None


def _is_active_verification_candidate(
    challenge: OtpChallenge,
) -> bool:
    return (
        challenge.status == OtpChallengeStatus.ACTIVE.value
        and challenge.user_id is not None
        and challenge.code_mac is not None
        and challenge.activated_at is not None
        and challenge.expires_at is not None
    )


def _revalidate_current_login_target(
    session: Session,
    *,
    challenge: OtpChallenge,
) -> bool:
    if challenge.user_id is None or challenge.telegram_link_id is None:
        return False
    user = session.get(User, challenge.user_id, with_for_update=True)
    link = session.get(TelegramLink, challenge.telegram_link_id, with_for_update=True)
    return (
        user is not None
        and user.is_active
        and link is not None
        and link.telegram_chat_id is not None
        and link.unlinked_at is None
        and link.linked_at == challenge.telegram_linked_at
    )


def _invalidate_verification_candidate(
    session: Session,
    *,
    challenge: OtpChallenge,
    now: datetime,
) -> None:
    invalidate_challenge(session, challenge=challenge, now=now)
    dispatch = load_dispatch_by_challenge_for_update(
        session,
        challenge_id=challenge.id,
    )
    if dispatch is not None and dispatch.status in {
        OtpDispatchStatus.PENDING.value,
        OtpDispatchStatus.PREPARED.value,
    }:
        cancel_dispatch(session, dispatch=dispatch, now=now)
    append_challenge_event(
        session,
        challenge_id=challenge.id,
        user_id=challenge.user_id,
        action=OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE,
        occurred_at=now,
        safe_code="OTP_LINK_CHANGED",
    )


def _is_wrong_active_candidate(candidate_check: OtpVerificationCandidateCheck) -> bool:
    return (
        candidate_check.outcome is OtpInternalOutcome.OTP_INVALID
        and candidate_check.challenge is not None
        and candidate_check.code is not None
        and not candidate_check.mac_matches
        and candidate_check.challenge.status == OtpChallengeStatus.ACTIVE.value
    )


def _inactive_candidate_outcome(
    challenge: OtpChallenge,
    *,
    now: datetime,
) -> OtpInternalOutcome:
    if challenge.status == OtpChallengeStatus.EXPIRED.value:
        return OtpInternalOutcome.OTP_EXPIRED
    if challenge.status == OtpChallengeStatus.SUPERSEDED.value:
        return OtpInternalOutcome.OTP_SUPERSEDED
    if challenge.status == OtpChallengeStatus.BURNED.value:
        return OtpInternalOutcome.OTP_BURNED
    if challenge.status == OtpChallengeStatus.INVALIDATED.value:
        return OtpInternalOutcome.OTP_LINK_CHANGED
    if challenge.expires_at is not None and challenge.expires_at <= now:
        return OtpInternalOutcome.OTP_EXPIRED
    return OtpInternalOutcome.OTP_INVALID


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OTP verification timestamps must be timezone-aware")
    return value.astimezone(UTC)
