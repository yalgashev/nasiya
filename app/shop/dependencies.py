from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.deps import (
    CurrentSessionContext,
    get_current_session_context,
    get_database_session,
    require_user,
)
from app.auth.error_codes import ErrorCode, get_error_http_status, get_public_error_body
from app.auth.models import User
from app.shop.context import CurrentShopContext, resolve_current_shop
from app.shop.enums import ShopRole
from app.shop.values import UserId

SHOP_SELECT_PATH = "/shop/select"


class ShopSelectionRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Shop selection required",
            headers={"Location": SHOP_SELECT_PATH},
        )


class ShopOwnerRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=get_error_http_status(ErrorCode.FORBIDDEN),
            detail=get_public_error_body(
                ErrorCode.FORBIDDEN,
                internal_detail="shop owner role required",
            ),
            headers={"X-Error-Code": ErrorCode.FORBIDDEN.value},
        )


def require_shop_staff(
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    current_user: Annotated[User, Depends(require_user)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> CurrentShopContext:
    auth_session = context.get_session_row()
    if auth_session is None:
        raise ShopSelectionRequired()

    shop_context = resolve_current_shop(
        db,
        auth_session=auth_session,
        user_id=UserId(current_user.id),
    )
    if not shop_context.is_selected:
        raise ShopSelectionRequired()
    return shop_context


def require_shop_owner(
    shop_context: Annotated[CurrentShopContext, Depends(require_shop_staff)],
) -> CurrentShopContext:
    if shop_context.role is not ShopRole.OWNER:
        raise ShopOwnerRequired()
    return shop_context
