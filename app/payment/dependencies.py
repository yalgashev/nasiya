"""Detached TX-A actor context for the future M14 payment mutation route."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated
from uuid import UUID

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
from app.debt.presentation import DebtWebLanguage
from app.debt.web_presentation import resolve_debt_web_language
from app.settings import Settings
from app.shop.context import resolve_current_shop
from app.shop.dependencies import ShopSelectionRequired
from app.shop.enums import ShopRole
from app.shop.values import UserId

__all__ = (
    "DetachedPaymentActorContext",
    "DetachedPaymentReadActorContext",
    "DetachedPaymentVoidFormContext",
    "get_detached_current_shop_payment_actor_context",
    "get_detached_current_shop_payment_read_actor_context",
    "get_detached_current_shop_payment_void_read_actor_context",
)


@dataclass(frozen=True, slots=True, repr=False)
class DetachedPaymentActorContext:
    """Server-derived scalar handoff after TX-A has committed and closed."""

    actor_user_id: UUID = field(repr=False)
    current_shop_id: UUID = field(repr=False)
    role_hint: ShopRole
    language: DebtWebLanguage

    def __post_init__(self) -> None:
        if not isinstance(self.actor_user_id, UUID):
            raise ValueError("Payment actor user ID is invalid")
        if not isinstance(self.current_shop_id, UUID):
            raise ValueError("Payment current shop ID is invalid")
        if not isinstance(self.role_hint, ShopRole):
            raise ValueError("Payment actor role hint is invalid")
        if not isinstance(self.language, DebtWebLanguage):
            raise ValueError("Payment actor language is invalid")

    def __repr__(self) -> str:
        return (
            "DetachedPaymentActorContext("
            "actor_user_id=<redacted>, current_shop_id=<redacted>, "
            f"role_hint={self.role_hint.value!r}, language={self.language.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class DetachedPaymentReadActorContext:
    """TX-A read handoff: scalar identity only, with no CSRF/mutation capability."""

    actor_user_id: UUID = field(repr=False)
    current_shop_id: UUID = field(repr=False)
    role_hint: ShopRole
    language: DebtWebLanguage

    def __post_init__(self) -> None:
        if not isinstance(self.actor_user_id, UUID) or not isinstance(
            self.current_shop_id, UUID
        ):
            raise ValueError("Payment read identity is invalid")
        if not isinstance(self.role_hint, ShopRole) or not isinstance(
            self.language, DebtWebLanguage
        ):
            raise ValueError("Payment read context is invalid")

    def __repr__(self) -> str:
        return "DetachedPaymentReadActorContext(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DetachedPaymentVoidFormContext:
    """Detached GET-form facts; CSRF is rendered but never a mutation grant."""

    actor: DetachedPaymentActorContext = field(repr=False)
    csrf_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor, DetachedPaymentActorContext):
            raise ValueError("Payment void form actor is invalid")
        if not isinstance(self.csrf_token, str) or not self.csrf_token:
            raise ValueError("Payment void form CSRF token is invalid")

    def __repr__(self) -> str:
        return "DetachedPaymentVoidFormContext(<redacted>)"


class PaymentActorLoginRequired(HTTPException):
    """Keep unauthenticated payment mutation entrypoints error-code stable."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Payment login required",
            headers={
                "Location": "/auth/login",
                "X-Error-Code": ErrorCode.UNAUTHORIZED.value,
            },
        )


async def get_detached_current_shop_payment_actor_context(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedPaymentActorContext:
    """Commit/close TX-A before a later route opens its own payment TX-B."""

    session_factory = request.app.state.database_session_factory
    detached: DetachedPaymentActorContext | None = None
    with session_factory.begin() as session:
        current = get_current_session_context(request, session, settings, now)
        await validate_csrf(request, current, now)
        if (
            current.status is CurrentSessionStatus.AUTHENTICATED
            and current.user_id is not None
            and current.get_session_row() is not None
        ):
            current_shop = resolve_current_shop(
                session,
                auth_session=current.get_session_row(),
                user_id=UserId(current.user_id),
            )
            if current_shop.shop is not None and current_shop.role is not None:
                detached = DetachedPaymentActorContext(
                    actor_user_id=current.user_id,
                    current_shop_id=current_shop.shop.id,
                    role_hint=current_shop.role,
                    language=resolve_debt_web_language(
                        request.headers.get("accept-language")
                    ),
                )

    if current.status is not CurrentSessionStatus.AUTHENTICATED:
        raise PaymentActorLoginRequired()
    if detached is None:
        raise ShopSelectionRequired()
    return detached


async def get_detached_current_shop_payment_read_actor_context(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedPaymentReadActorContext:
    """Resolve current-shop identity in short TX-A before a read-only TX-B.

    This intentionally does not validate CSRF: no payment mutation is exposed
    through the read context.
    """

    session_factory = request.app.state.database_session_factory
    detached: DetachedPaymentReadActorContext | None = None
    with session_factory.begin() as session:
        current = get_current_session_context(request, session, settings, now)
        if (
            current.status is CurrentSessionStatus.AUTHENTICATED
            and current.user_id is not None
            and current.get_session_row() is not None
        ):
            current_shop = resolve_current_shop(
                session,
                auth_session=current.get_session_row(),
                user_id=UserId(current.user_id),
            )
            if current_shop.shop is not None and current_shop.role is not None:
                detached = DetachedPaymentReadActorContext(
                    actor_user_id=current.user_id,
                    current_shop_id=current_shop.shop.id,
                    role_hint=current_shop.role,
                    language=resolve_debt_web_language(
                        request.headers.get("accept-language")
                    ),
                )
    if current.status is not CurrentSessionStatus.AUTHENTICATED:
        raise PaymentActorLoginRequired()
    if detached is None:
        raise ShopSelectionRequired()
    return detached


async def get_detached_current_shop_payment_void_read_actor_context(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedPaymentVoidFormContext:
    """Resolve scalar GET-form context; mutation authority remains POST-only."""

    session_factory = request.app.state.database_session_factory
    detached: DetachedPaymentVoidFormContext | None = None
    with session_factory.begin() as session:
        current = get_current_session_context(request, session, settings, now)
        if (
            current.status is CurrentSessionStatus.AUTHENTICATED
            and current.user_id is not None
            and current.get_session_row() is not None
        ):
            current_shop = resolve_current_shop(
                session,
                auth_session=current.get_session_row(),
                user_id=UserId(current.user_id),
            )
            if current_shop.shop is not None and current_shop.role is not None:
                detached = DetachedPaymentVoidFormContext(
                    actor=DetachedPaymentActorContext(
                        actor_user_id=current.user_id,
                        current_shop_id=current_shop.shop.id,
                        role_hint=current_shop.role,
                        language=resolve_debt_web_language(
                            request.headers.get("accept-language")
                        ),
                    ),
                    csrf_token=get_csrf_token(
                        current.get_session_row()
                    ).as_form_value(),
                )
    if current.status is not CurrentSessionStatus.AUTHENTICATED:
        raise PaymentActorLoginRequired()
    if detached is None:
        raise ShopSelectionRequired()
    return detached
