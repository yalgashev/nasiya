"""Closed TX-A authority adapter for bounded ShopCustomer mutations."""

from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.deps import (
    CurrentSessionStatus,
    get_current_session_context,
    get_current_time,
    get_settings,
    validate_csrf,
)
from app.auth.error_codes import ErrorCode
from app.settings import Settings
from app.shop.context import resolve_current_shop
from app.shop.dependencies import ShopSelectionRequired
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import DetachedShopCustomerAuthority


class ShopCustomerLoginRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Login required",
            headers={
                "Location": "/auth/login",
                "X-Error-Code": ErrorCode.UNAUTHORIZED.value,
            },
        )


async def get_detached_shop_customer_authority(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> DetachedShopCustomerAuthority:
    """Resolve trusted actor/current-shop IDs and close TX-A before returning."""

    session_factory = request.app.state.database_session_factory
    authority: DetachedShopCustomerAuthority | None = None
    authenticated = False
    with session_factory.begin() as db:
        context = get_current_session_context(request, db, settings, now)
        await validate_csrf(request, context, now)
        authenticated = (
            context.status is CurrentSessionStatus.AUTHENTICATED
            and context.user_id is not None
        )
        auth_session = context.get_session_row()
        if authenticated and auth_session is not None:
            user_id = UserId(context.user_id)
            current_shop = resolve_current_shop(
                db,
                auth_session=auth_session,
                user_id=user_id,
            )
            if current_shop.shop is not None:
                authority = DetachedShopCustomerAuthority(
                    actor_user_id=user_id,
                    current_shop_id=ShopId(current_shop.shop.id),
                )

    if not authenticated:
        raise ShopCustomerLoginRequired()
    if authority is None:
        raise ShopSelectionRequired()
    return authority
