"""Detached current-Shop authority and clock dependencies for disclosure SSR."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.csrf import get_csrf_token
from app.auth.deps import (
    CurrentSessionStatus,
    get_current_session_context,
    get_current_time,
    get_settings,
    validate_csrf,
)
from app.auth.error_codes import ErrorCode
from app.rating.targeting import DetachedDisclosureActorContext
from app.settings import Settings
from app.shop.context import resolve_current_shop
from app.shop.values import ShopId, UserId

__all__ = (
    "DisclosureClock",
    "DetachedDisclosureReadContext",
    "get_detached_current_shop_disclosure_actor_context",
    "get_detached_current_shop_disclosure_read_actor_context",
    "get_risk_band_disclosure_clock",
)

type DisclosureClock = Callable[[], datetime]


class DisclosureActorLoginRequired(HTTPException):
    """Keep disclosure entrypoints on the established current-Shop login path."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Disclosure login required",
            headers={
                "Location": "/auth/login",
                "X-Error-Code": ErrorCode.UNAUTHORIZED.value,
            },
        )


class DisclosureActorUnavailable(HTTPException):
    """Generic current-Shop denial with no target-dependent signal."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Disclosure unavailable",
            headers={"Location": "/shop/customers?risk_error=unavailable"},
        )


@dataclass(frozen=True, slots=True, repr=False)
class DetachedDisclosureReadContext:
    actor: DetachedDisclosureActorContext = field(repr=False)
    csrf_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor, DetachedDisclosureActorContext):
            raise ValueError("Disclosure read actor is invalid")
        if not isinstance(self.csrf_token, str) or not self.csrf_token:
            raise ValueError("Disclosure read CSRF token is invalid")

    def __repr__(self) -> str:
        return "DetachedDisclosureReadContext(<redacted>)"


async def get_detached_current_shop_disclosure_actor_context(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedDisclosureActorContext:
    """Close TX-A after auth/current-Shop resolution and CSRF verification."""

    session_factory = request.app.state.database_session_factory
    detached: DetachedDisclosureActorContext | None = None
    with session_factory.begin() as session:
        current = get_current_session_context(request, session, settings, now)
        await validate_csrf(request, current, now)
        detached = _current_shop_disclosure_actor(session, current)
    if current.status is not CurrentSessionStatus.AUTHENTICATED:
        raise DisclosureActorLoginRequired()
    if detached is None:
        raise DisclosureActorUnavailable()
    return detached


async def get_detached_current_shop_disclosure_read_actor_context(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedDisclosureReadContext:
    """Close TX-A for historical read authority without a CSRF capability."""

    session_factory = request.app.state.database_session_factory
    with session_factory.begin() as session:
        current = get_current_session_context(request, session, settings, now)
        detached = _current_shop_disclosure_actor(session, current)
        auth_session = current.get_session_row()
        csrf_token = (
            None
            if auth_session is None
            else get_csrf_token(auth_session).as_form_value()
        )
    if current.status is not CurrentSessionStatus.AUTHENTICATED:
        raise DisclosureActorLoginRequired()
    if detached is None:
        raise DisclosureActorUnavailable()
    if csrf_token is None:
        raise DisclosureActorLoginRequired()
    return DetachedDisclosureReadContext(actor=detached, csrf_token=csrf_token)


def get_risk_band_disclosure_clock(request: Request) -> DisclosureClock:
    """Expose the application-composed trusted instant supplier to the router."""

    clock = getattr(request.app.state, "risk_band_disclosure_clock", None)
    if not callable(clock):
        raise RuntimeError("Risk-band disclosure clock is unavailable")
    return clock


def _current_shop_disclosure_actor(
    session,
    current,
) -> DetachedDisclosureActorContext | None:
    if (
        current.status is not CurrentSessionStatus.AUTHENTICATED
        or current.user_id is None
        or current.get_session_row() is None
    ):
        return None
    current_shop = resolve_current_shop(
        session,
        auth_session=current.get_session_row(),
        user_id=UserId(current.user_id),
    )
    if current_shop.shop is None or current_shop.role is None:
        return None
    return DetachedDisclosureActorContext(
        actor_user_id=UserId(current.user_id),
        current_shop_id=ShopId(current_shop.shop.id),
        role_hint=current_shop.role,
    )
