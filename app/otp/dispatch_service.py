from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.auth.models import User
from app.otp.code import OtpCode, generate_otp_code
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDeliveryFailureCode,
    OtpDispatchStatus,
    OtpPurpose,
)
from app.otp.crypto import compute_otp_code_mac
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.provider import (
    OtpDeliverySendResult,
    OtpDeliverySendStatus,
    TelegramOtpTarget,
)
from app.otp.repository import (
    activate_challenge,
    append_challenge_event,
    cancel_dispatch,
    claim_next_pending_dispatch_for_update,
    expire_challenge,
    invalidate_challenge,
    load_challenge_by_id_for_update,
    load_stale_prepared_dispatches_for_update,
    mark_dispatch_failed,
    mark_dispatch_prepared,
    mark_dispatch_sent,
    mark_dispatch_unknown,
    mark_stale_prepared_dispatch_unknown,
)
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink


@dataclass(frozen=True, repr=False)
class PreparedOtpDispatch:
    dispatch_id: UUID
    challenge_id: UUID
    target: TelegramOtpTarget
    code: OtpCode
    locale: str
    ttl_seconds: int

    def __repr__(self) -> str:
        return (
            "PreparedOtpDispatch("
            "dispatch_id=<redacted>, challenge_id=<redacted>, "
            "target=<TelegramOtpTarget>, code=<OtpCode>, "
            f"locale={self.locale!r}, ttl_seconds={self.ttl_seconds!r})"
        )


def prepare_next_otp_dispatch(
    session: Session,
    *,
    otp_hmac_key: SecretStr,
    now: datetime,
    ttl_seconds: int,
    claim_stale_seconds: int,
    code_generator: Callable[[int], int] | None = None,
) -> PreparedOtpDispatch | None:
    current_time = _as_utc(now)
    stale_before = current_time - timedelta(seconds=claim_stale_seconds)
    dispatch = claim_next_pending_dispatch_for_update(
        session,
        now=current_time,
        claim_stale_before=stale_before,
    )
    if dispatch is None:
        return None

    challenge = load_challenge_by_id_for_update(
        session,
        challenge_id=dispatch.challenge_id,
    )
    if challenge is None:
        cancel_dispatch(session, dispatch=dispatch, now=current_time)
        append_challenge_event(
            session,
            challenge_id=dispatch.challenge_id,
            action=OtpChallengeEventAction.DISPATCH_RESULT,
            occurred_at=current_time,
            safe_code="OTP_CANCELLED",
        )
        return None

    target = _validate_challenge_target(
        session,
        challenge=challenge,
        dispatch=dispatch,
        now=current_time,
    )
    if target is None:
        return None

    code = generate_otp_code(number_generator=code_generator)
    code_mac = compute_otp_code_mac(
        otp_hmac_key=otp_hmac_key,
        challenge_id=challenge.id,
        user_id=challenge.user_id,
        purpose=OtpPurpose.LOGIN,
        code=code,
    )
    activate_challenge(
        session,
        challenge=challenge,
        code_mac=code_mac,
        activated_at=current_time,
        expires_at=current_time + timedelta(seconds=ttl_seconds),
    )
    mark_dispatch_prepared(
        session,
        dispatch=dispatch,
        challenge=challenge,
        now=current_time,
    )
    append_challenge_event(
        session,
        challenge_id=challenge.id,
        user_id=challenge.user_id,
        action=OtpChallengeEventAction.DISPATCH_PREPARED,
        occurred_at=current_time,
    )
    return PreparedOtpDispatch(
        dispatch_id=dispatch.id,
        challenge_id=challenge.id,
        target=target,
        code=code,
        locale=dispatch.locale,
        ttl_seconds=ttl_seconds,
    )


def record_otp_delivery_result(
    session: Session,
    *,
    dispatch_id: UUID,
    result: OtpDeliverySendResult,
    now: datetime,
) -> bool:
    current_time = _as_utc(now)
    dispatch = session.get(OtpDispatch, dispatch_id, with_for_update=True)
    if dispatch is None or dispatch.status != OtpDispatchStatus.PREPARED.value:
        return False

    if result.status is OtpDeliverySendStatus.SENT:
        mark_dispatch_sent(session, dispatch=dispatch, now=current_time)
        safe_code = "OTP_SENT"
    elif result.status is OtpDeliverySendStatus.UNKNOWN:
        failure_code = _safe_result_code(result.failure_code, default="OTP_UNKNOWN")
        mark_dispatch_unknown(
            session,
            dispatch=dispatch,
            failure_code=failure_code,
            now=current_time,
        )
        safe_code = failure_code
    else:
        failure_code = _safe_result_code(
            result.failure_code,
            default=OtpDeliveryFailureCode.TELEGRAM_UNKNOWN,
        )
        mark_dispatch_failed(
            session,
            dispatch=dispatch,
            failure_code=failure_code,
            now=current_time,
        )
        safe_code = failure_code

    append_challenge_event(
        session,
        challenge_id=dispatch.challenge_id,
        action=OtpChallengeEventAction.DISPATCH_RESULT,
        occurred_at=current_time,
        user_id=_dispatch_challenge_user_id(session, dispatch),
        safe_code=safe_code,
    )
    return True


def recover_stale_prepared_dispatches(
    session: Session,
    *,
    now: datetime,
    stale_seconds: int,
    limit: int,
) -> int:
    current_time = _as_utc(now)
    stale_before = current_time - timedelta(seconds=stale_seconds)
    dispatches = load_stale_prepared_dispatches_for_update(
        session,
        stale_before=stale_before,
        limit=limit,
    )
    for dispatch in dispatches:
        mark_stale_prepared_dispatch_unknown(
            session,
            dispatch=dispatch,
            now=current_time,
        )
        append_challenge_event(
            session,
            challenge_id=dispatch.challenge_id,
            action=OtpChallengeEventAction.DISPATCH_RESULT,
            occurred_at=current_time,
            safe_code="OTP_DISPATCH_STALE_PREPARED",
        )
    return len(dispatches)


def _validate_challenge_target(
    session: Session,
    *,
    challenge: OtpChallenge,
    dispatch: OtpDispatch,
    now: datetime,
) -> TelegramOtpTarget | None:
    if challenge.status != OtpChallengeStatus.PENDING_DISPATCH.value:
        cancel_dispatch(session, dispatch=dispatch, now=now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            action=OtpChallengeEventAction.DISPATCH_RESULT,
            occurred_at=now,
            safe_code="OTP_CANCELLED",
        )
        return None
    if (
        challenge.user_id is None
        or challenge.telegram_link_id is None
        or challenge.telegram_linked_at is None
    ):
        expire_challenge(session, challenge=challenge, now=now)
        cancel_dispatch(session, dispatch=dispatch, now=now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            action=OtpChallengeEventAction.EXPIRED,
            occurred_at=now,
            safe_code="OTP_EXPIRED",
        )
        return None

    user = session.get(User, challenge.user_id, with_for_update=True)
    link = session.get(TelegramLink, challenge.telegram_link_id, with_for_update=True)
    if (
        user is None
        or not user.is_active
        or link is None
        or link.telegram_chat_id is None
        or link.unlinked_at is not None
        or link.linked_at != challenge.telegram_linked_at
    ):
        invalidate_challenge(session, challenge=challenge, now=now)
        cancel_dispatch(session, dispatch=dispatch, now=now)
        append_challenge_event(
            session,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            action=OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE,
            occurred_at=now,
            safe_code="OTP_LINK_CHANGED",
        )
        return None

    return TelegramOtpTarget(
        chat_identity=VerifiedPrivateTelegramChatIdentity(link.telegram_chat_id)
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OTP dispatch timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _safe_result_code(
    failure_code: OtpDeliveryFailureCode | str | None,
    *,
    default: OtpDeliveryFailureCode | str,
) -> str:
    if isinstance(failure_code, OtpDeliveryFailureCode):
        return failure_code.value
    if failure_code is None:
        if isinstance(default, OtpDeliveryFailureCode):
            return default.value
        return default
    return failure_code


def _dispatch_challenge_user_id(
    session: Session,
    dispatch: OtpDispatch,
) -> UUID | None:
    challenge = session.get(OtpChallenge, dispatch.challenge_id)
    if challenge is None:
        return None
    return challenge.user_id
