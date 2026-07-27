from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.cookies import delete_session_cookie
from app.auth.deps import (
    CurrentSessionContext,
    CurrentSessionStatus,
    LoginRequired,
    get_current_session_context,
    get_current_time,
    get_database_session,
    get_settings,
    require_user,
    validate_csrf,
)
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.auth.sessions import set_session_active_shop_id
from app.auth.template_context import with_csrf_context
from app.security_headers import mark_auth_response_no_store
from app.settings import Settings
from app.shop.context import CurrentShopContext
from app.shop.dependencies import (
    ShopOwnerRequired,
    ShopSelectionRequired,
    require_shop_owner,
    require_shop_staff,
)
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import list_active_shop_staff, list_user_active_staff
from app.shop.service import (
    RevokeStaffOutcome,
    add_staff,
    change_staff_role,
    revoke_staff,
)
from app.shop.values import ShopId, ShopStaffId, UserId

router = APIRouter(prefix="/shop")
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
LOGIN_PATH = "/auth/login"
SHOP_SELECT_PATH = "/shop/select"

ROLE_LABELS = {
    ShopRole.OWNER: "egasi",
    ShopRole.MANAGER: "menejer",
    ShopRole.CASHIER: "kassir",
}
STATUS_LABELS = {
    ShopStatus.ACTIVE: "faol",
    ShopStatus.SUSPENDED: "to'xtatilgan",
}
STAFF_ERROR_MESSAGES = {
    "add_failed": "Xodimni qo'shib bo'lmadi.",
    "staff_action_failed": "Xodim bo'yicha amal bajarilmadi.",
    "last_owner": "Oxirgi egani olib tashlab yoki rolini pasaytirib bo'lmaydi.",
    "shop_suspended": "Do'kon to'xtatilgan. O'zgartirish kiritib bo'lmaydi.",
}
STAFF_NOTICE_MESSAGES = {
    "staff_added": "Xodim saqlandi.",
    "role_updated": "Xodim roli saqlandi.",
    "staff_revoked": "Xodim huquqi yopildi.",
}
STAFF_ROLE_OPTIONS = (
    ShopRole.OWNER,
    ShopRole.MANAGER,
    ShopRole.CASHIER,
)


@router.get("", response_class=HTMLResponse, response_model=None)
def workspace_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> Response:
    try:
        current_user = require_user(session_context)
    except LoginRequired:
        return _redirect_auth_login(session_context, settings)

    try:
        shop_context = require_shop_staff(db, current_user, session_context)
    except ShopSelectionRequired:
        return _redirect_shop_select()

    response = templates.TemplateResponse(
        request,
        "shop/workspace.html",
        _workspace_template_context(db, shop_context, user_id=UserId(current_user.id)),
    )
    return mark_auth_response_no_store(response)


@router.get("/select", response_class=HTMLResponse, response_model=None)
def select_shop_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> Response:
    try:
        current_user = require_user(session_context)
    except LoginRequired:
        return _redirect_auth_login(session_context, settings)

    response = templates.TemplateResponse(
        request,
        "shop/select.html",
        with_csrf_context(
            _select_template_context(
                db,
                user_id=UserId(current_user.id),
            ),
            session_context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post("/select", response_model=None)
def submit_select_shop(
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    _csrf: Annotated[None, Depends(validate_csrf)],
    shop_id: Annotated[str | None, Form()] = None,
) -> Response:
    _ = _csrf
    try:
        current_user = require_user(session_context)
    except LoginRequired:
        return _redirect_auth_login(session_context, settings)

    target_shop_id = _parse_shop_id(shop_id)
    auth_session = session_context.get_session_row()
    if target_shop_id is None or auth_session is None:
        return _render_select_forbidden()

    selectable_shop_ids = {
        shop.id
        for _staff, shop in list_user_active_staff(
            db,
            user_id=UserId(current_user.id),
        )
    }
    if target_shop_id not in selectable_shop_ids:
        return _render_select_forbidden()

    set_session_active_shop_id(db, auth_session, shop_id=target_shop_id)
    response = RedirectResponse("/shop", status_code=status.HTTP_303_SEE_OTHER)
    return mark_auth_response_no_store(response)


@router.get("/staff", response_class=HTMLResponse, response_model=None)
def staff_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    try:
        current_user = require_user(session_context)
    except LoginRequired:
        return _redirect_auth_login(session_context, settings)

    try:
        shop_context = require_shop_staff(db, current_user, session_context)
    except ShopSelectionRequired:
        return _redirect_shop_select()

    response = templates.TemplateResponse(
        request,
        "shop/staff.html",
        with_csrf_context(
            _staff_template_context(
                db,
                shop_context,
                error=error,
                notice=notice,
            ),
            session_context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post("/staff/add", response_model=None)
def submit_add_staff(
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    phone: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
) -> Response:
    _ = _csrf
    owner_context = _require_owner_context(db, settings, session_context)
    if isinstance(owner_context, Response):
        return owner_context

    result = add_staff(
        db,
        shop_id=owner_context.shop_id,
        actor_user_id=UserId(owner_context.user.id),
        phone=phone,
        role=role,
        now=now,
    )
    if result.succeeded:
        return _redirect_staff_page(notice="staff_added")
    return _redirect_staff_page(error=_staff_error_query(result.error, "add_failed"))


@router.post("/staff/{staff_id}/role", response_model=None)
def submit_change_staff_role(
    staff_id: UUID,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    new_role: Annotated[str, Form()] = "",
) -> Response:
    _ = _csrf
    owner_context = _require_owner_context(db, settings, session_context)
    if isinstance(owner_context, Response):
        return owner_context

    result = change_staff_role(
        db,
        shop_id=owner_context.shop_id,
        actor_user_id=UserId(owner_context.user.id),
        target_staff_id=ShopStaffId(staff_id),
        new_role=new_role,
        now=now,
    )
    if result.succeeded:
        return _redirect_staff_page(notice="role_updated")
    return _redirect_staff_page(
        error=_staff_error_query(result.error, "staff_action_failed")
    )


@router.post("/staff/{staff_id}/revoke", response_model=None)
def submit_revoke_staff(
    staff_id: UUID,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> Response:
    _ = _csrf
    owner_context = _require_owner_context(db, settings, session_context)
    if isinstance(owner_context, Response):
        return owner_context

    result = revoke_staff(
        db,
        shop_id=owner_context.shop_id,
        actor_user_id=UserId(owner_context.user.id),
        target_staff_id=ShopStaffId(staff_id),
        now=now,
    )
    if result.succeeded:
        assert result.revocation is not None
        if result.revocation.outcome is RevokeStaffOutcome.NOT_FOUND:
            return _redirect_staff_page(error="staff_action_failed")
        return _redirect_staff_page(notice="staff_revoked")
    return _redirect_staff_page(
        error=_staff_error_query(result.error, "staff_action_failed")
    )


@dataclass(frozen=True)
class _OwnerContext:
    user: User
    shop_id: ShopId


def _workspace_template_context(
    db: DatabaseSession,
    shop_context: CurrentShopContext,
    *,
    user_id: UserId,
) -> dict[str, object]:
    assert shop_context.shop is not None
    assert shop_context.role is not None
    assert shop_context.status is not None

    active_staff_count = len(
        list_active_shop_staff(
            db,
            shop_id=ShopId(shop_context.shop.id),
        )
    )
    user_membership_count = len(list_user_active_staff(db, user_id=user_id))
    return {
        "shop_name": shop_context.shop.name,
        "status_label": STATUS_LABELS[shop_context.status],
        "role_label": ROLE_LABELS[shop_context.role],
        "active_staff_count": active_staff_count,
        "is_read_only": shop_context.status == ShopStatus.SUSPENDED,
        "show_shop_switcher": user_membership_count > 1,
    }


def _staff_template_context(
    db: DatabaseSession,
    shop_context: CurrentShopContext,
    *,
    error: str | None,
    notice: str | None,
) -> dict[str, object]:
    assert shop_context.shop is not None
    assert shop_context.role is not None
    assert shop_context.status is not None

    staff_rows = [
        {
            "staff_id": str(staff.id),
            "masked_phone": mask_phone_for_display(user.phone),
            "role": ShopRole(staff.role),
            "role_label": ROLE_LABELS[ShopRole(staff.role)],
            "created_at": _format_utc_datetime(staff.created_at),
        }
        for staff, user in list_active_shop_staff(
            db,
            shop_id=ShopId(shop_context.shop.id),
        )
    ]
    return {
        "shop_name": shop_context.shop.name,
        "staff_rows": staff_rows,
        "has_staff": bool(staff_rows),
        "can_manage_staff": (
            shop_context.role is ShopRole.OWNER
            and shop_context.status is ShopStatus.ACTIVE
        ),
        "is_read_only": shop_context.status is ShopStatus.SUSPENDED,
        "role_options": [
            {"value": role.value, "label": ROLE_LABELS[role]}
            for role in STAFF_ROLE_OPTIONS
        ],
        "error_message": STAFF_ERROR_MESSAGES.get(error or ""),
        "notice_message": STAFF_NOTICE_MESSAGES.get(notice or ""),
    }


def _select_template_context(
    db: DatabaseSession,
    *,
    user_id: UserId,
) -> dict[str, object]:
    memberships = [
        {
            "shop_id": str(shop.id),
            "shop_name": shop.name,
            "status_label": STATUS_LABELS[ShopStatus(shop.status)],
        }
        for _staff, shop in list_user_active_staff(db, user_id=user_id)
    ]
    return {
        "memberships": memberships,
        "has_memberships": bool(memberships),
    }


def _parse_shop_id(raw_shop_id: str | None) -> UUID | None:
    if raw_shop_id is None:
        return None
    try:
        return UUID(raw_shop_id)
    except ValueError:
        return None


def _require_owner_context(
    db: DatabaseSession,
    settings: Settings,
    session_context: CurrentSessionContext,
) -> _OwnerContext | Response:
    try:
        current_user = require_user(session_context)
    except LoginRequired:
        return _redirect_auth_login(session_context, settings)

    try:
        shop_context = require_shop_staff(db, current_user, session_context)
        owner_context = require_shop_owner(shop_context)
    except ShopSelectionRequired:
        return _redirect_shop_select()
    except ShopOwnerRequired:
        return _render_forbidden()

    assert owner_context.shop is not None
    return _OwnerContext(user=current_user, shop_id=ShopId(owner_context.shop.id))


def _staff_error_query(error_code: ErrorCode | None, fallback: str) -> str:
    if error_code is ErrorCode.LAST_OWNER:
        return "last_owner"
    if error_code is ErrorCode.SHOP_SUSPENDED:
        return "shop_suspended"
    if error_code is ErrorCode.VALIDATION_ERROR:
        return "add_failed" if fallback == "add_failed" else "staff_action_failed"
    return fallback


def _redirect_staff_page(
    *,
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    target = "/shop/staff"
    if error is not None:
        target = f"{target}?error={error}"
    elif notice is not None:
        target = f"{target}?notice={notice}"

    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    return mark_auth_response_no_store(response)


def _redirect_shop_select() -> Response:
    response = RedirectResponse(
        SHOP_SELECT_PATH,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    return mark_auth_response_no_store(response)


def _redirect_auth_login(
    context: CurrentSessionContext,
    settings: Settings,
) -> Response:
    response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    if context.status == CurrentSessionStatus.EXPIRED:
        response.headers["X-Error-Code"] = ErrorCode.SESSION_EXPIRED.value
    else:
        response.headers["X-Error-Code"] = ErrorCode.UNAUTHORIZED.value

    if context.status in {
        CurrentSessionStatus.INVALID,
        CurrentSessionStatus.REVOKED,
        CurrentSessionStatus.EXPIRED,
        CurrentSessionStatus.INACTIVE_USER,
    }:
        delete_session_cookie(response, settings)

    return mark_auth_response_no_store(response)


def _render_forbidden() -> Response:
    response = HTMLResponse(
        "<!doctype html>"
        '<html lang="uz">'
        "<head>"
        '<meta charset="utf-8">'
        "<title>Ruxsat yo'q</title>"
        "</head>"
        "<body>"
        "<main>"
        "<h1>Ruxsat yo'q</h1>"
        "<p>Bu amal uchun ruxsat yo'q.</p>"
        "</main>"
        "</body>"
        "</html>",
        status_code=status.HTTP_403_FORBIDDEN,
    )
    response.headers["X-Error-Code"] = ErrorCode.FORBIDDEN.value
    return mark_auth_response_no_store(response)


def _render_select_forbidden() -> Response:
    response = HTMLResponse(
        "<!doctype html>"
        '<html lang="uz">'
        "<head>"
        '<meta charset="utf-8">'
        "<title>Do'kon tanlanmadi</title>"
        "</head>"
        "<body>"
        "<main>"
        "<h1>Do'kon tanlanmadi</h1>"
        "<p>Bu do'konni tanlash mumkin emas.</p>"
        "</main>"
        "</body>"
        "</html>",
        status_code=status.HTTP_403_FORBIDDEN,
    )
    response.headers["X-Error-Code"] = ErrorCode.FORBIDDEN.value
    return mark_auth_response_no_store(response)


def _format_utc_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
