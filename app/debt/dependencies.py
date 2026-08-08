"""Detached, server-derived authority for M13 tenant debt mutations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import (
    CurrentSessionStatus,
    get_current_session_context,
    get_current_time,
    get_settings,
    validate_csrf,
)
from app.auth.models import User
from app.debt.customer_authority import (
    CustomerDebtAuthority,
    resolve_own_customer_debt_authority,
)
from app.debt.values import ShopId, UserId
from app.settings import Settings
from app.shop.context import resolve_current_shop
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import lock_actor_shop_staff_for_update, lock_shop_for_update

_DEBT_STAFF_ROLES = frozenset({ShopRole.OWNER, ShopRole.MANAGER, ShopRole.CASHIER})


@dataclass(frozen=True, slots=True, repr=False)
class DebtRequestContext:
    """Safe rendering/control-flow context; it contains no request secrets."""

    is_htmx: bool

    def __repr__(self) -> str:
        return f"DebtRequestContext(is_htmx={self.is_htmx!r})"


@dataclass(frozen=True, slots=True, repr=False)
class DetachedDebtActorAuthority:
    """IDs only, produced after the short auth/session/CSRF transaction closes."""

    status: CurrentSessionStatus
    actor_user_id: UUID | None
    current_shop_id: UUID | None
    request_context: DebtRequestContext

    @property
    def is_authenticated(self) -> bool:
        return (
            self.status is CurrentSessionStatus.AUTHENTICATED
            and self.actor_user_id is not None
            and self.current_shop_id is not None
        )

    def __repr__(self) -> str:
        return (
            "DetachedDebtActorAuthority("
            f"status={self.status!s}, actor_user_id=<redacted>, "
            "current_shop_id=<redacted>, request_context=<safe>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class LockedLiveDebtActor:
    actor_user_id: UUID
    current_shop_id: UUID
    role: ShopRole
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedLiveDebtActor(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DetachedCustomerDebtAuthority:
    status: CurrentSessionStatus
    authority: CustomerDebtAuthority | None = field(default=None, repr=False)

    @property
    def is_authenticated(self) -> bool:
        return self.status is CurrentSessionStatus.AUTHENTICATED

    def __repr__(self) -> str:
        return (
            "DetachedCustomerDebtAuthority("
            f"status={self.status!s}, authority=<redacted>)"
        )


async def get_detached_current_shop_debt_actor_authority(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedDebtActorAuthority:
    """Run TX-A and return no ORM row, Session, admin flag, or client identity."""

    session_factory = request.app.state.database_session_factory
    with session_factory.begin() as session:
        current = get_current_session_context(request, session, settings, now)
        await validate_csrf(request, current, now)
        auth_session = current.get_session_row()
        user = current.get_authenticated_user()
        shop_id: UUID | None = None
        if auth_session is not None and user is not None:
            current_shop = resolve_current_shop(
                session, auth_session=auth_session, user_id=UserId(user.id)
            )
            if current_shop.shop is not None:
                shop_id = current_shop.shop.id
        return DetachedDebtActorAuthority(
            status=current.status,
            actor_user_id=current.user_id,
            current_shop_id=shop_id,
            request_context=DebtRequestContext(
                is_htmx=request.headers.get("HX-Request") == "true"
            ),
        )


async def get_detached_customer_debt_authority(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedCustomerDebtAuthority:
    """Close customer auth/CSRF TX-A and transfer only immutable identifiers."""

    session_factory = request.app.state.database_session_factory
    with session_factory.begin() as session:
        current = get_current_session_context(request, session, settings, now)
        await validate_csrf(request, current, now)
        user = current.get_authenticated_user()
        if user is None:
            return DetachedCustomerDebtAuthority(status=current.status)
        return DetachedCustomerDebtAuthority(
            status=current.status,
            authority=resolve_own_customer_debt_authority(
                session,
                authenticated_user=user,
            ),
        )


def lock_live_debt_actor(
    session: Session, *, authority: DetachedDebtActorAuthority
) -> LockedLiveDebtActor | None:
    """TX-B live recheck. Platform-admin status never substitutes membership."""

    if (
        not isinstance(authority, DetachedDebtActorAuthority)
        or not authority.is_authenticated
    ):
        return None
    assert authority.current_shop_id is not None
    assert authority.actor_user_id is not None
    locked_shop = lock_shop_for_update(
        session, shop_id=ShopId(authority.current_shop_id)
    )
    if locked_shop is None or locked_shop.shop.status != ShopStatus.ACTIVE.value:
        return None
    locked_staff = lock_actor_shop_staff_for_update(
        session, locked_shop=locked_shop, actor_user_id=UserId(authority.actor_user_id)
    )
    if (
        locked_staff is None
        or ShopRole(locked_staff.staff.role) not in _DEBT_STAFF_ROLES
    ):
        return None
    actor = session.scalar(
        select(User).where(User.id == authority.actor_user_id).with_for_update()
    )
    if actor is None or not actor.is_active:
        return None
    return LockedLiveDebtActor(
        actor_user_id=actor.id,
        current_shop_id=locked_shop.shop.id,
        role=ShopRole(locked_staff.staff.role),
        _session=session,
    )
