import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkToken

_TOKEN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_TOKEN_INSERT_CONSTRAINTS = frozenset(
    {
        "uq_telegram_link_tokens_token_hash",
        "uq_telegram_link_tokens_one_outstanding_per_user",
    }
)
TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS: Final = 30


class TelegramLinkTokenInsertConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramLinkTokenStatus:
    total_count: int
    outstanding_count: int
    consumed_count: int
    invalidated_count: int
    expired_outstanding_count: int


def has_active_telegram_link(session: Session, current_user: User) -> bool:
    statement = (
        select(TelegramLink.id)
        .where(
            TelegramLink.user_id == current_user.id,
            TelegramLink.telegram_chat_id.is_not(None),
            TelegramLink.unlinked_at.is_(None),
        )
        .limit(1)
    )
    return session.scalar(statement) is not None


def get_telegram_link_by_user(
    session: Session,
    current_user: User,
) -> TelegramLink | None:
    statement = select(TelegramLink).where(TelegramLink.user_id == current_user.id)
    return session.scalar(statement)


def get_telegram_link_by_user_for_update(
    session: Session,
    current_user: User,
) -> TelegramLink | None:
    statement = (
        select(TelegramLink)
        .where(TelegramLink.user_id == current_user.id)
        .with_for_update()
    )
    return session.scalar(statement)


def get_other_active_telegram_link_by_chat_identity_for_update(
    session: Session,
    current_user: User,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
) -> TelegramLink | None:
    statement = (
        select(TelegramLink)
        .where(
            TelegramLink.telegram_chat_id == chat_identity.as_bigint(),
            TelegramLink.unlinked_at.is_(None),
            TelegramLink.user_id != current_user.id,
        )
        .with_for_update()
    )
    return session.scalar(statement)


def lock_telegram_link_change_set(
    session: Session,
    current_user: User,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
) -> tuple[TelegramLink, ...]:
    statement = (
        select(TelegramLink)
        .where(
            or_(
                TelegramLink.user_id == current_user.id,
                (
                    (TelegramLink.telegram_chat_id == chat_identity.as_bigint())
                    & TelegramLink.unlinked_at.is_(None)
                ),
            )
        )
        .order_by(TelegramLink.id.asc())
        .with_for_update()
    )
    return tuple(session.scalars(statement).all())


def link_verified_private_chat(
    session: Session,
    current_user: User,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    now: datetime,
) -> TelegramLink | None:
    current_time = _as_utc(now)
    existing_link = get_telegram_link_by_user_for_update(session, current_user)
    if existing_link is None:
        link = TelegramLink(
            user_id=current_user.id,
            telegram_chat_id=chat_identity.as_bigint(),
            linked_at=current_time,
            updated_at=current_time,
        )
        session.add(link)
        session.flush()
        return link

    if existing_link.telegram_chat_id is not None and existing_link.unlinked_at is None:
        return None

    existing_link.telegram_chat_id = chat_identity.as_bigint()
    existing_link.linked_at = current_time
    existing_link.unlinked_at = None
    existing_link.updated_at = current_time
    session.add(existing_link)
    session.flush()
    return existing_link


def relink_verified_private_chat(
    session: Session,
    current_user: User,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    now: datetime,
) -> TelegramLink | None:
    current_time = _as_utc(now)
    existing_link = get_telegram_link_by_user_for_update(session, current_user)
    if (
        existing_link is None
        or existing_link.telegram_chat_id is None
        or existing_link.unlinked_at is not None
    ):
        return None

    existing_link.telegram_chat_id = chat_identity.as_bigint()
    existing_link.linked_at = current_time
    existing_link.updated_at = current_time
    session.add(existing_link)
    session.flush()
    return existing_link


def unlink_verified_private_chat(
    session: Session,
    current_user: User,
    now: datetime,
) -> TelegramLink | None:
    current_time = _as_utc(now)
    existing_link = get_telegram_link_by_user_for_update(session, current_user)
    if (
        existing_link is None
        or existing_link.telegram_chat_id is None
        or existing_link.unlinked_at is not None
    ):
        return None

    existing_link.telegram_chat_id = None
    existing_link.unlinked_at = current_time
    existing_link.updated_at = current_time
    session.add(existing_link)
    session.flush()
    return existing_link


def get_telegram_link_status(
    session: Session,
    current_user: User,
) -> TelegramLink | None:
    return get_telegram_link_by_user(session, current_user)


def get_outstanding_telegram_link_token_for_update(
    session: Session,
    current_user: User,
) -> TelegramLinkToken | None:
    statement = (
        select(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == current_user.id,
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
        )
        .with_for_update()
    )
    return session.scalar(statement)


def get_telegram_link_token_by_hash_for_update(
    session: Session,
    token_hash: str,
) -> TelegramLinkToken | None:
    normalized_hash = _validate_token_hash(token_hash)
    statement = (
        select(TelegramLinkToken)
        .where(TelegramLinkToken.token_hash == normalized_hash)
        .with_for_update()
    )
    return session.scalar(statement)


def get_telegram_link_token_by_id_for_user(
    session: Session,
    current_user: User,
    token_id: UUID,
) -> TelegramLinkToken | None:
    statement = select(TelegramLinkToken).where(
        TelegramLinkToken.id == token_id,
        TelegramLinkToken.user_id == current_user.id,
    )
    return session.scalar(statement)


def get_valid_telegram_link_token_for_consume_by_hash_for_update(
    session: Session,
    token_hash: str,
    now: datetime,
) -> TelegramLinkToken | None:
    current_time = _as_utc(now)
    token = get_telegram_link_token_by_hash_for_update(session, token_hash)
    if (
        token is None
        or token.consumed_at is not None
        or token.invalidated_at is not None
        or token.expires_at <= current_time
    ):
        return None
    return token


def invalidate_outstanding_telegram_link_tokens(
    session: Session,
    current_user: User,
    now: datetime,
) -> int:
    current_time = _as_utc(now)
    statement = (
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == current_user.id,
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=current_time)
    )
    result = session.execute(statement)
    return result.rowcount or 0


def insert_telegram_link_token(
    session: Session,
    current_user: User,
    token_hash: str,
    created_at: datetime,
    expires_at: datetime,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=current_user.id,
        token_hash=_validate_token_hash(token_hash),
        created_at=_as_utc(created_at),
        expires_at=_as_utc(expires_at),
    )
    session.add(token)
    session.flush()
    return token


def invalidate_and_insert_telegram_link_token(
    session: Session,
    current_user: User,
    token_hash: str,
    now: datetime,
    expires_at: datetime,
) -> TelegramLinkToken:
    current_time = _as_utc(now)
    expires_time = _as_utc(expires_at)
    normalized_hash = _validate_token_hash(token_hash)
    existing_token = get_outstanding_telegram_link_token_for_update(
        session,
        current_user,
    )

    try:
        with session.begin_nested():
            if existing_token is not None:
                existing_token.invalidated_at = current_time
                session.add(existing_token)
                session.flush()

            token = TelegramLinkToken(
                user_id=current_user.id,
                token_hash=normalized_hash,
                created_at=current_time,
                expires_at=expires_time,
            )
            session.add(token)
            session.flush()
    except IntegrityError as exc:
        if _is_expected_token_insert_conflict(exc):
            raise TelegramLinkTokenInsertConflict(
                "Telegram link token insert conflict"
            ) from None
        raise
    return token


def get_telegram_link_token_status(
    session: Session,
    current_user: User,
    now: datetime,
) -> TelegramLinkTokenStatus:
    current_time = _as_utc(now)
    statement = select(
        func.count(TelegramLinkToken.id).label("total_count"),
        func.count(TelegramLinkToken.id)
        .filter(
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
        )
        .label("outstanding_count"),
        func.count(TelegramLinkToken.id)
        .filter(TelegramLinkToken.consumed_at.is_not(None))
        .label("consumed_count"),
        func.count(TelegramLinkToken.id)
        .filter(TelegramLinkToken.invalidated_at.is_not(None))
        .label("invalidated_count"),
        func.count(TelegramLinkToken.id)
        .filter(
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
            TelegramLinkToken.expires_at <= current_time,
        )
        .label("expired_outstanding_count"),
    ).where(TelegramLinkToken.user_id == current_user.id)
    row = session.execute(statement).one()
    return TelegramLinkTokenStatus(
        total_count=row.total_count,
        outstanding_count=row.outstanding_count,
        consumed_count=row.consumed_count,
        invalidated_count=row.invalidated_count,
        expired_outstanding_count=row.expired_outstanding_count,
    )


def get_telegram_link_tokens_eligible_for_purge(
    session: Session,
    now: datetime,
    *,
    limit: int,
) -> list[TelegramLinkToken]:
    current_time = _as_utc(now)
    if limit < 1:
        raise ValueError("Telegram link token purge batch limit must be positive")
    cutoff = current_time - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    statement = (
        select(TelegramLinkToken)
        .where(_terminal_link_token_purge_filter(cutoff))
        .order_by(TelegramLinkToken.created_at.asc(), TelegramLinkToken.id.asc())
        .limit(limit)
    )
    return list(session.scalars(statement).all())


def delete_telegram_link_tokens_eligible_for_purge(
    session: Session,
    now: datetime,
    *,
    limit: int,
) -> int:
    current_time = _as_utc(now)
    if limit < 1:
        raise ValueError("Telegram link token purge batch limit must be positive")
    cutoff = current_time - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    eligible_ids_statement = (
        select(TelegramLinkToken.id)
        .where(_terminal_link_token_purge_filter(cutoff))
        .order_by(TelegramLinkToken.created_at.asc(), TelegramLinkToken.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    eligible_ids = list(session.scalars(eligible_ids_statement).all())
    if not eligible_ids:
        return 0
    statement = delete(TelegramLinkToken).where(TelegramLinkToken.id.in_(eligible_ids))
    result = session.execute(statement)
    return result.rowcount or 0


def _validate_token_hash(token_hash: str) -> str:
    if (
        not isinstance(token_hash, str)
        or _TOKEN_HASH_PATTERN.fullmatch(token_hash) is None
    ):
        raise ValueError("Telegram link token hash must be lowercase SHA-256 hex")
    return token_hash


def _is_expected_token_insert_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name in _EXPECTED_TOKEN_INSERT_CONSTRAINTS


def _terminal_link_token_purge_filter(cutoff: datetime):
    return (
        (
            TelegramLinkToken.consumed_at.is_not(None)
            & (TelegramLinkToken.consumed_at <= cutoff)
        )
        | (
            TelegramLinkToken.invalidated_at.is_not(None)
            & (TelegramLinkToken.invalidated_at <= cutoff)
        )
        | (
            TelegramLinkToken.consumed_at.is_(None)
            & TelegramLinkToken.invalidated_at.is_(None)
            & (TelegramLinkToken.expires_at <= cutoff)
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Telegram link token timestamps must be timezone-aware")
    return value.astimezone(UTC)
