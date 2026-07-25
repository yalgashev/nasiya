from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.telegram.models import TelegramLinkEvent

TelegramLinkEventAction = Literal["linked", "unlinked", "relinked"]
APPROVED_TELEGRAM_LINK_EVENT_ACTIONS: Final = frozenset(
    {"linked", "unlinked", "relinked"}
)


def append_telegram_link_event(
    session: Session,
    user_id: UUID,
    action: TelegramLinkEventAction,
    occurred_at: datetime,
) -> TelegramLinkEvent:
    if action not in APPROVED_TELEGRAM_LINK_EVENT_ACTIONS:
        raise ValueError("Telegram link event action is not approved")

    event = TelegramLinkEvent(
        user_id=user_id,
        action=action,
        occurred_at=_as_utc(occurred_at),
    )
    session.add(event)
    session.flush()
    return event


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Telegram link event timestamp must be timezone-aware")
    return value.astimezone(UTC)
