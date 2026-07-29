import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import delete as sqlalchemy_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDeliveryFailureCode,
    OtpDispatchStatus,
    OtpPurpose,
    parse_challenge_status,
    parse_dispatch_status,
    parse_event_action,
    parse_otp_purpose,
)
from app.otp.crypto import OtpBrowserBindingDigest, OtpCodeMac
from app.otp.models import (
    OtpChallenge,
    OtpChallengeEvent,
    OtpDispatch,
    OtpDispatcherState,
)

OTP_TERMINAL_RETENTION_DAYS: Final = 30
OTP_EVENT_RETENTION_DAYS: Final = 90
OTP_PURGE_MAX_BATCH_SIZE: Final = 5000
_OUTSTANDING_CHALLENGE_STATUSES: Final = (
    OtpChallengeStatus.PENDING_DISPATCH.value,
    OtpChallengeStatus.ACTIVE.value,
)
_TERMINAL_CHALLENGE_STATUSES: Final = (
    OtpChallengeStatus.CONSUMED.value,
    OtpChallengeStatus.SUPERSEDED.value,
    OtpChallengeStatus.EXPIRED.value,
    OtpChallengeStatus.BURNED.value,
    OtpChallengeStatus.INVALIDATED.value,
)
_TERMINAL_DISPATCH_STATUSES: Final = (
    OtpDispatchStatus.SENT.value,
    OtpDispatchStatus.FAILED.value,
    OtpDispatchStatus.UNKNOWN.value,
    OtpDispatchStatus.CANCELLED.value,
)
_EXPECTED_CHALLENGE_INSERT_CONSTRAINTS: Final = frozenset(
    {
        "uq_otp_challenges_one_outstanding_per_user_purpose",
        "uq_otp_challenges_one_outstanding_per_browser_purpose",
    }
)
_EXPECTED_DISPATCH_INSERT_CONSTRAINTS: Final = frozenset(
    {"uq_otp_dispatches_challenge_id"}
)
_SAFE_CODE_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_EVENT_CODES: Final = frozenset(
    {
        *(failure_code.value for failure_code in OtpDeliveryFailureCode),
        "OTP_SENT",
        "OTP_INVALID",
        "OTP_EXPIRED",
        "OTP_SUPERSEDED",
        "OTP_BURNED",
        "OTP_LINK_CHANGED",
        "OTP_RATE_LIMITED",
        "OTP_DISPATCH_STALE_PREPARED",
        "OTP_CANCELLED",
        "OTP_UNKNOWN",
    }
)
_LOCALES: Final = frozenset({"uz-Latn", "ru"})


class OtpChallengeInsertConflict(RuntimeError):
    pass


class OtpDispatchInsertConflict(RuntimeError):
    pass


class OtpRepositoryStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class OtpPurgeResult:
    dispatches_deleted: int
    challenges_deleted: int
    events_deleted: int


@dataclass(frozen=True)
class OtpDispatcherHealth:
    heartbeat_at: datetime | None
    ready_at: datetime | None


class OtpDispatcherStateMissingError(RuntimeError):
    pass


def load_outstanding_challenge_by_user_for_update(
    session: Session,
    *,
    user_id: UUID,
    purpose: OtpPurpose | str,
) -> OtpChallenge | None:
    statement = (
        select(OtpChallenge)
        .where(
            OtpChallenge.user_id == _validate_uuid(user_id, "OTP user id"),
            OtpChallenge.purpose == _purpose_value(purpose),
            OtpChallenge.status.in_(_OUTSTANDING_CHALLENGE_STATUSES),
        )
        .with_for_update()
    )
    return session.scalar(statement)


def load_outstanding_challenge_by_browser_for_update(
    session: Session,
    *,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    purpose: OtpPurpose | str,
) -> OtpChallenge | None:
    statement = (
        select(OtpChallenge)
        .where(
            OtpChallenge.browser_binding_digest
            == _browser_binding_value(browser_binding_digest),
            OtpChallenge.purpose == _purpose_value(purpose),
            OtpChallenge.status.in_(_OUTSTANDING_CHALLENGE_STATUSES),
        )
        .with_for_update()
    )
    return session.scalar(statement)


def load_verification_candidate_by_browser_for_update(
    session: Session,
    *,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    purpose: OtpPurpose | str,
) -> OtpChallenge | None:
    return load_outstanding_challenge_by_browser_for_update(
        session,
        browser_binding_digest=browser_binding_digest,
        purpose=purpose,
    )


def load_challenge_by_id_for_update(
    session: Session,
    *,
    challenge_id: UUID,
) -> OtpChallenge | None:
    return session.get(
        OtpChallenge,
        _validate_uuid(challenge_id, "OTP challenge id"),
        with_for_update=True,
    )


def load_outstanding_challenges_by_user_for_update(
    session: Session,
    *,
    user_id: UUID,
    purpose: OtpPurpose | str,
) -> list[OtpChallenge]:
    statement = (
        select(OtpChallenge)
        .where(
            OtpChallenge.user_id == _validate_uuid(user_id, "OTP user id"),
            OtpChallenge.purpose == _purpose_value(purpose),
            OtpChallenge.status.in_(_OUTSTANDING_CHALLENGE_STATUSES),
        )
        .order_by(OtpChallenge.created_at.asc(), OtpChallenge.id.asc())
        .with_for_update()
    )
    return list(session.scalars(statement).all())


def create_pending_challenge(
    session: Session,
    *,
    browser_binding_digest: OtpBrowserBindingDigest | str,
    now: datetime,
    purpose: OtpPurpose | str = OtpPurpose.LOGIN,
    user_id: UUID | None = None,
    telegram_link_id: UUID | None = None,
    telegram_linked_at: datetime | None = None,
) -> OtpChallenge:
    current_time = _as_utc(now)
    normalized_user_id, normalized_link_id, normalized_linked_at = (
        _validate_identity_snapshot(
            user_id=user_id,
            telegram_link_id=telegram_link_id,
            telegram_linked_at=telegram_linked_at,
        )
    )
    challenge = OtpChallenge(
        user_id=normalized_user_id,
        purpose=_purpose_value(purpose),
        telegram_link_id=normalized_link_id,
        telegram_linked_at=normalized_linked_at,
        browser_binding_digest=_browser_binding_value(browser_binding_digest),
        status=OtpChallengeStatus.PENDING_DISPATCH.value,
        failed_attempts=0,
        created_at=current_time,
        updated_at=current_time,
    )
    try:
        with session.begin_nested():
            session.add(challenge)
            session.flush()
    except IntegrityError as exc:
        if _is_expected_constraint(exc, _EXPECTED_CHALLENGE_INSERT_CONSTRAINTS):
            raise OtpChallengeInsertConflict("OTP challenge insert conflict") from None
        raise
    return challenge


def activate_challenge(
    session: Session,
    *,
    challenge: OtpChallenge,
    code_mac: OtpCodeMac | str,
    activated_at: datetime,
    expires_at: datetime,
) -> OtpChallenge:
    _require_challenge_status(challenge, OtpChallengeStatus.PENDING_DISPATCH)
    activation_time = _as_utc(activated_at)
    expiry_time = _as_utc(expires_at)
    if expiry_time <= activation_time:
        raise ValueError("OTP challenge expiry must be after activation")
    if (
        challenge.user_id is None
        or challenge.telegram_link_id is None
        or challenge.telegram_linked_at is None
    ):
        raise OtpRepositoryStateError("OTP challenge identity snapshot is required")

    challenge.status = OtpChallengeStatus.ACTIVE.value
    challenge.code_mac = _code_mac_value(code_mac)
    challenge.activated_at = activation_time
    challenge.expires_at = expiry_time
    challenge.updated_at = activation_time
    session.add(challenge)
    session.flush()
    return challenge


def increment_challenge_failed_attempts(
    session: Session,
    *,
    challenge: OtpChallenge,
    now: datetime,
    max_attempts: int,
) -> OtpChallenge:
    _require_challenge_status(challenge, OtpChallengeStatus.ACTIVE)
    if max_attempts < 1 or max_attempts > 10:
        raise ValueError("OTP max attempts must be between 1 and 10")
    current_time = _as_utc(now)
    challenge.failed_attempts += 1
    if challenge.failed_attempts >= max_attempts:
        challenge.status = OtpChallengeStatus.BURNED.value
        challenge.terminal_at = current_time
    challenge.updated_at = current_time
    session.add(challenge)
    session.flush()
    return challenge


def consume_challenge(
    session: Session,
    *,
    challenge: OtpChallenge,
    now: datetime,
) -> OtpChallenge:
    _require_challenge_status(challenge, OtpChallengeStatus.ACTIVE)
    current_time = _as_utc(now)
    challenge.status = OtpChallengeStatus.CONSUMED.value
    challenge.consumed_at = current_time
    challenge.terminal_at = current_time
    challenge.updated_at = current_time
    session.add(challenge)
    session.flush()
    return challenge


def supersede_challenge(
    session: Session,
    *,
    challenge: OtpChallenge,
    now: datetime,
) -> OtpChallenge:
    return _terminalize_outstanding_challenge(
        session,
        challenge=challenge,
        status=OtpChallengeStatus.SUPERSEDED,
        now=now,
    )


def expire_challenge(
    session: Session,
    *,
    challenge: OtpChallenge,
    now: datetime,
) -> OtpChallenge:
    return _terminalize_outstanding_challenge(
        session,
        challenge=challenge,
        status=OtpChallengeStatus.EXPIRED,
        now=now,
    )


def invalidate_challenge(
    session: Session,
    *,
    challenge: OtpChallenge,
    now: datetime,
) -> OtpChallenge:
    return _terminalize_outstanding_challenge(
        session,
        challenge=challenge,
        status=OtpChallengeStatus.INVALIDATED,
        now=now,
    )


def burn_challenge(
    session: Session,
    *,
    challenge: OtpChallenge,
    now: datetime,
) -> OtpChallenge:
    _require_challenge_status(challenge, OtpChallengeStatus.ACTIVE)
    return _terminalize_outstanding_challenge(
        session,
        challenge=challenge,
        status=OtpChallengeStatus.BURNED,
        now=now,
    )


def create_pending_dispatch(
    session: Session,
    *,
    challenge_id: UUID,
    locale: str,
    now: datetime,
) -> OtpDispatch:
    current_time = _as_utc(now)
    dispatch = OtpDispatch(
        challenge_id=_validate_uuid(challenge_id, "OTP challenge id"),
        status=OtpDispatchStatus.PENDING.value,
        locale=_validate_locale(locale),
        created_at=current_time,
        updated_at=current_time,
    )
    try:
        with session.begin_nested():
            session.add(dispatch)
            session.flush()
    except IntegrityError as exc:
        if _is_expected_constraint(exc, _EXPECTED_DISPATCH_INSERT_CONSTRAINTS):
            raise OtpDispatchInsertConflict("OTP dispatch insert conflict") from None
        raise
    return dispatch


def load_dispatch_by_challenge_for_update(
    session: Session,
    *,
    challenge_id: UUID,
) -> OtpDispatch | None:
    statement = (
        select(OtpDispatch)
        .where(
            OtpDispatch.challenge_id == _validate_uuid(challenge_id, "OTP challenge id")
        )
        .with_for_update()
    )
    return session.scalar(statement)


def claim_next_pending_dispatch_for_update(
    session: Session,
    *,
    now: datetime,
    claim_stale_before: datetime | None = None,
) -> OtpDispatch | None:
    current_time = _as_utc(now)
    statement = (
        select(OtpDispatch)
        .where(OtpDispatch.status == OtpDispatchStatus.PENDING.value)
        .order_by(OtpDispatch.created_at.asc(), OtpDispatch.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if claim_stale_before is not None:
        stale_before = _as_utc(claim_stale_before)
        statement = statement.where(
            (OtpDispatch.claimed_at.is_(None))
            | (OtpDispatch.claimed_at <= stale_before)
        )
    dispatch = session.scalar(statement)
    if dispatch is None:
        return None
    dispatch.claimed_at = current_time
    dispatch.updated_at = current_time
    session.add(dispatch)
    session.flush()
    return dispatch


def mark_dispatch_prepared(
    session: Session,
    *,
    dispatch: OtpDispatch,
    challenge: OtpChallenge,
    now: datetime,
) -> OtpDispatch:
    _require_dispatch_status(dispatch, OtpDispatchStatus.PENDING)
    _require_challenge_status(challenge, OtpChallengeStatus.ACTIVE)
    if dispatch.challenge_id != challenge.id:
        raise OtpRepositoryStateError("OTP dispatch challenge mismatch")
    if dispatch.claimed_at is None:
        raise OtpRepositoryStateError("OTP dispatch must be claimed before prepare")

    current_time = _as_utc(now)
    dispatch.status = OtpDispatchStatus.PREPARED.value
    dispatch.prepared_at = current_time
    dispatch.updated_at = current_time
    session.add(dispatch)
    session.flush()
    return dispatch


def mark_dispatch_sent(
    session: Session,
    *,
    dispatch: OtpDispatch,
    now: datetime,
) -> OtpDispatch:
    _require_dispatch_status(dispatch, OtpDispatchStatus.PREPARED)
    current_time = _as_utc(now)
    dispatch.status = OtpDispatchStatus.SENT.value
    dispatch.sent_at = current_time
    dispatch.terminal_at = current_time
    dispatch.updated_at = current_time
    session.add(dispatch)
    session.flush()
    return dispatch


def mark_dispatch_failed(
    session: Session,
    *,
    dispatch: OtpDispatch,
    failure_code: OtpDeliveryFailureCode | str,
    now: datetime,
) -> OtpDispatch:
    _require_dispatch_status(dispatch, OtpDispatchStatus.PREPARED)
    current_time = _as_utc(now)
    dispatch.status = OtpDispatchStatus.FAILED.value
    dispatch.failure_code = _delivery_failure_code_value(failure_code)
    dispatch.terminal_at = current_time
    dispatch.updated_at = current_time
    session.add(dispatch)
    session.flush()
    return dispatch


def mark_dispatch_unknown(
    session: Session,
    *,
    dispatch: OtpDispatch,
    failure_code: str = "OTP_UNKNOWN",
    now: datetime,
) -> OtpDispatch:
    _require_dispatch_status(dispatch, OtpDispatchStatus.PREPARED)
    current_time = _as_utc(now)
    dispatch.status = OtpDispatchStatus.UNKNOWN.value
    dispatch.failure_code = _safe_code_value(failure_code)
    dispatch.terminal_at = current_time
    dispatch.updated_at = current_time
    session.add(dispatch)
    session.flush()
    return dispatch


def cancel_dispatch(
    session: Session,
    *,
    dispatch: OtpDispatch,
    now: datetime,
) -> OtpDispatch:
    _require_dispatch_statuses(
        dispatch,
        OtpDispatchStatus.PENDING,
        OtpDispatchStatus.PREPARED,
    )
    current_time = _as_utc(now)
    dispatch.status = OtpDispatchStatus.CANCELLED.value
    dispatch.terminal_at = current_time
    dispatch.updated_at = current_time
    session.add(dispatch)
    session.flush()
    return dispatch


def load_stale_prepared_dispatches_for_update(
    session: Session,
    *,
    stale_before: datetime,
    limit: int,
) -> list[OtpDispatch]:
    batch_limit = _validate_batch_size(limit)
    cutoff = _as_utc(stale_before)
    statement = (
        select(OtpDispatch)
        .where(
            OtpDispatch.status == OtpDispatchStatus.PREPARED.value,
            OtpDispatch.prepared_at <= cutoff,
        )
        .order_by(OtpDispatch.prepared_at.asc(), OtpDispatch.id.asc())
        .limit(batch_limit)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(statement).all())


def mark_stale_prepared_dispatch_unknown(
    session: Session,
    *,
    dispatch: OtpDispatch,
    now: datetime,
) -> OtpDispatch:
    return mark_dispatch_unknown(
        session,
        dispatch=dispatch,
        failure_code="OTP_DISPATCH_STALE_PREPARED",
        now=now,
    )


def get_or_create_dispatcher_state_for_update(
    session: Session,
    *,
    now: datetime,
) -> OtpDispatcherState:
    state = session.get(OtpDispatcherState, 1, with_for_update=True)
    if state is not None:
        return state

    current_time = _as_utc(now)
    state = OtpDispatcherState(id=1, updated_at=current_time)
    try:
        with session.begin_nested():
            session.add(state)
            session.flush()
    except IntegrityError:
        state = session.get(OtpDispatcherState, 1, with_for_update=True)
        if state is None:
            raise
    return state


def mark_dispatcher_heartbeat(
    session: Session,
    *,
    now: datetime,
    ready: bool | None = False,
) -> OtpDispatcherState:
    current_time = _as_utc(now)
    state = get_or_create_dispatcher_state_for_update(session, now=current_time)
    state.heartbeat_at = current_time
    if ready is True:
        state.ready_at = state.ready_at or current_time
    elif ready is False:
        state.ready_at = None
    state.updated_at = current_time
    session.add(state)
    session.flush()
    return state


def read_dispatcher_health(session: Session) -> OtpDispatcherHealth:
    state = session.get(OtpDispatcherState, 1)
    if state is None:
        raise OtpDispatcherStateMissingError("OTP dispatcher state is missing")
    return OtpDispatcherHealth(
        heartbeat_at=state.heartbeat_at,
        ready_at=state.ready_at,
    )


def append_challenge_event(
    session: Session,
    *,
    challenge_id: UUID,
    action: OtpChallengeEventAction | str,
    occurred_at: datetime,
    user_id: UUID | None = None,
    safe_code: str | None = None,
) -> OtpChallengeEvent:
    event = OtpChallengeEvent(
        challenge_id=_validate_uuid(challenge_id, "OTP challenge id"),
        user_id=None if user_id is None else _validate_uuid(user_id, "OTP user id"),
        action=_event_action_value(action),
        occurred_at=_as_utc(occurred_at),
        safe_code=None if safe_code is None else _safe_code_value(safe_code),
    )
    session.add(event)
    session.flush()
    return event


def purge_terminal_otp_records(
    session: Session,
    *,
    now: datetime,
    batch_size: int,
    terminal_retention_days: int = OTP_TERMINAL_RETENTION_DAYS,
    event_retention_days: int = OTP_EVENT_RETENTION_DAYS,
) -> OtpPurgeResult:
    current_time = _as_utc(now)
    limit = _validate_batch_size(batch_size)
    terminal_cutoff = current_time - timedelta(
        days=_validate_retention_days(terminal_retention_days)
    )
    event_cutoff = current_time - timedelta(
        days=_validate_retention_days(event_retention_days)
    )

    dispatch_ids = list(
        session.scalars(
            select(OtpDispatch.id)
            .where(
                OtpDispatch.status.in_(_TERMINAL_DISPATCH_STATUSES),
                OtpDispatch.terminal_at <= terminal_cutoff,
            )
            .order_by(OtpDispatch.terminal_at.asc(), OtpDispatch.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    dispatches_deleted = 0
    if dispatch_ids:
        dispatches_deleted = (
            session.execute(
                sqlalchemy_delete(OtpDispatch).where(OtpDispatch.id.in_(dispatch_ids))
            ).rowcount
            or 0
        )

    blocking_dispatch = select(OtpDispatch.id).where(
        OtpDispatch.challenge_id == OtpChallenge.id
    )
    challenge_ids = list(
        session.scalars(
            select(OtpChallenge.id)
            .where(
                OtpChallenge.status.in_(_TERMINAL_CHALLENGE_STATUSES),
                OtpChallenge.terminal_at <= terminal_cutoff,
                ~blocking_dispatch.exists(),
            )
            .order_by(OtpChallenge.terminal_at.asc(), OtpChallenge.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    challenges_deleted = 0
    if challenge_ids:
        challenges_deleted = (
            session.execute(
                sqlalchemy_delete(OtpChallenge).where(
                    OtpChallenge.id.in_(challenge_ids)
                )
            ).rowcount
            or 0
        )

    event_ids = list(
        session.scalars(
            select(OtpChallengeEvent.id)
            .where(OtpChallengeEvent.occurred_at <= event_cutoff)
            .order_by(OtpChallengeEvent.occurred_at.asc(), OtpChallengeEvent.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    events_deleted = 0
    if event_ids:
        events_deleted = (
            session.execute(
                sqlalchemy_delete(OtpChallengeEvent).where(
                    OtpChallengeEvent.id.in_(event_ids)
                )
            ).rowcount
            or 0
        )

    return OtpPurgeResult(
        dispatches_deleted=dispatches_deleted,
        challenges_deleted=challenges_deleted,
        events_deleted=events_deleted,
    )


def _terminalize_outstanding_challenge(
    session: Session,
    *,
    challenge: OtpChallenge,
    status: OtpChallengeStatus,
    now: datetime,
) -> OtpChallenge:
    _require_challenge_statuses(
        challenge,
        OtpChallengeStatus.PENDING_DISPATCH,
        OtpChallengeStatus.ACTIVE,
    )
    current_time = _as_utc(now)
    challenge.status = status.value
    challenge.terminal_at = current_time
    challenge.updated_at = current_time
    session.add(challenge)
    session.flush()
    return challenge


def _require_challenge_status(
    challenge: OtpChallenge,
    status: OtpChallengeStatus,
) -> None:
    _require_challenge_statuses(challenge, status)


def _require_challenge_statuses(
    challenge: OtpChallenge,
    *statuses: OtpChallengeStatus,
) -> None:
    current_status = parse_challenge_status(challenge.status)
    if current_status not in statuses:
        raise OtpRepositoryStateError("OTP challenge state transition rejected")


def _require_dispatch_status(
    dispatch: OtpDispatch,
    status: OtpDispatchStatus,
) -> None:
    _require_dispatch_statuses(dispatch, status)


def _require_dispatch_statuses(
    dispatch: OtpDispatch,
    *statuses: OtpDispatchStatus,
) -> None:
    current_status = parse_dispatch_status(dispatch.status)
    if current_status not in statuses:
        raise OtpRepositoryStateError("OTP dispatch state transition rejected")


def _validate_identity_snapshot(
    *,
    user_id: UUID | None,
    telegram_link_id: UUID | None,
    telegram_linked_at: datetime | None,
) -> tuple[UUID | None, UUID | None, datetime | None]:
    values = (user_id, telegram_link_id, telegram_linked_at)
    if all(value is None for value in values):
        return None, None, None
    if any(value is None for value in values):
        raise ValueError("OTP challenge identity snapshot must be complete")
    return (
        _validate_uuid(user_id, "OTP user id"),
        _validate_uuid(telegram_link_id, "OTP Telegram link id"),
        _as_utc(telegram_linked_at),
    )


def _purpose_value(purpose: OtpPurpose | str) -> str:
    if isinstance(purpose, OtpPurpose):
        return purpose.value
    return parse_otp_purpose(purpose).value


def _event_action_value(action: OtpChallengeEventAction | str) -> str:
    if isinstance(action, OtpChallengeEventAction):
        return action.value
    return parse_event_action(action).value


def _delivery_failure_code_value(failure_code: OtpDeliveryFailureCode | str) -> str:
    if isinstance(failure_code, OtpDeliveryFailureCode):
        return failure_code.value
    if failure_code in {code.value for code in OtpDeliveryFailureCode}:
        return failure_code
    raise ValueError("Unknown OTP delivery failure code")


def _safe_code_value(safe_code: str) -> str:
    if (
        not isinstance(safe_code, str)
        or _SAFE_CODE_PATTERN.fullmatch(safe_code) is None
    ):
        raise ValueError("OTP safe event code must be an allowed safe code")
    if safe_code not in _SAFE_EVENT_CODES:
        raise ValueError("OTP safe event code must be an allowed safe code")
    return safe_code


def _code_mac_value(code_mac: OtpCodeMac | str) -> str:
    if isinstance(code_mac, OtpCodeMac):
        return code_mac.as_stored_value()
    return OtpCodeMac(code_mac).as_stored_value()


def _browser_binding_value(
    browser_binding_digest: OtpBrowserBindingDigest | str,
) -> str:
    if isinstance(browser_binding_digest, OtpBrowserBindingDigest):
        return browser_binding_digest.as_stored_value()
    return OtpBrowserBindingDigest(browser_binding_digest).as_stored_value()


def _validate_locale(locale: str) -> str:
    if locale not in _LOCALES:
        raise ValueError("OTP dispatch locale must be supported")
    return locale


def _validate_uuid(value: UUID | None, label: str) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(f"{label} must be a UUID")
    return value


def _validate_batch_size(batch_size: int) -> int:
    if batch_size < 1 or batch_size > OTP_PURGE_MAX_BATCH_SIZE:
        raise ValueError("OTP batch size must be between 1 and 5000")
    return batch_size


def _validate_retention_days(retention_days: int) -> int:
    if retention_days < 1:
        raise ValueError("OTP retention days must be positive")
    return retention_days


def _is_expected_constraint(
    exc: IntegrityError,
    expected_constraints: frozenset[str],
) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name in expected_constraints


def _as_utc(value: datetime | None) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OTP timestamps must be timezone-aware")
    return value.astimezone(UTC)
