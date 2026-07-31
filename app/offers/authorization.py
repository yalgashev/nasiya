from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
)
from app.audit.repository import append_audit_event
from app.auth.deps import require_user
from app.auth.error_codes import ErrorCode, get_error_http_status, get_public_error_body
from app.auth.models import User

_PLATFORM_ADMIN_ACTOR_TOKEN = object()


@dataclass(frozen=True, slots=True, repr=False, init=False)
class PlatformAdminActor:
    user_id: UUID

    def __init__(self, user_id: UUID, token: object) -> None:
        if token is not _PLATFORM_ADMIN_ACTOR_TOKEN:
            raise ValueError("Platform-admin actor cannot be constructed directly")
        if not isinstance(user_id, UUID):
            raise ValueError("Platform-admin actor identity is invalid")
        object.__setattr__(self, "user_id", user_id)

    def __repr__(self) -> str:
        return "PlatformAdminActor(user_id=<redacted>)"


class PlatformAdminAuthorizationError(PermissionError):
    pass


class PlatformAdminRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=get_error_http_status(ErrorCode.FORBIDDEN),
            detail=get_public_error_body(
                ErrorCode.FORBIDDEN,
                internal_detail="platform-admin authority required",
            ),
            headers={"X-Error-Code": ErrorCode.FORBIDDEN.value},
        )


class PlatformAdminBootstrapStatus(StrEnum):
    BOOTSTRAPPED = "BOOTSTRAPPED"
    ADMIN_ALREADY_EXISTS = "ADMIN_ALREADY_EXISTS"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    USER_INACTIVE = "USER_INACTIVE"


def require_platform_admin_actor(
    current_user: Annotated[User, Depends(require_user)],
) -> PlatformAdminActor:
    if not current_user.is_active or not current_user.is_platform_admin:
        raise PlatformAdminRequired()
    return PlatformAdminActor(current_user.id, _PLATFORM_ADMIN_ACTOR_TOKEN)


def assert_platform_admin_actor(
    session: Session,
    actor: PlatformAdminActor,
) -> None:
    if not isinstance(actor, PlatformAdminActor):
        raise PlatformAdminAuthorizationError("Platform-admin authorization failed")
    statement = (
        select(User.id)
        .where(
            User.id == actor.user_id,
            User.is_active.is_(True),
            User.is_platform_admin.is_(True),
        )
        .with_for_update()
    )
    if session.scalar(statement) is None:
        raise PlatformAdminAuthorizationError("Platform-admin authorization failed")


def bootstrap_first_platform_admin(
    session: Session,
    *,
    target_user_id: UUID,
    occurred_at: datetime,
) -> PlatformAdminBootstrapStatus:
    if not isinstance(target_user_id, UUID):
        raise ValueError("Bootstrap target identity is invalid")
    current_time = _as_utc(occurred_at)
    users = tuple(session.scalars(select(User).order_by(User.id).with_for_update()))
    if any(user.is_platform_admin for user in users):
        return PlatformAdminBootstrapStatus.ADMIN_ALREADY_EXISTS

    target = next((user for user in users if user.id == target_user_id), None)
    if target is None:
        return PlatformAdminBootstrapStatus.USER_NOT_FOUND
    if not target.is_active:
        return PlatformAdminBootstrapStatus.USER_INACTIVE

    target.is_platform_admin = True
    session.flush()
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.USER,
            object_id=target.id,
            occurred_at=current_time,
            candidate_metadata={"bootstrap_method": "operator_cli"},
        ),
    )
    return PlatformAdminBootstrapStatus.BOOTSTRAPPED


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Bootstrap time must be timezone-aware")
    return value.astimezone(UTC)
