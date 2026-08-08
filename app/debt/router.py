"""Thin authenticated SSR/PRG adapters for the exact M13 debt routes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

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
)
from app.auth.error_codes import ErrorCode
from app.auth.template_context import with_csrf_context
from app.debt.commands import CreateDebtRawForm, assemble_create_debt_command
from app.debt.customer_accept_service import (
    AcceptCustomerDebtCommand,
    accept_own_customer_debt,
)
from app.debt.customer_authority import (
    CustomerDebtAuthority,
    resolve_own_customer_debt_authority,
)
from app.debt.customer_reject_service import (
    RejectCustomerDebtCommand,
    reject_own_customer_debt,
)
from app.debt.customer_web_read_service import (
    get_own_customer_debt_web_detail,
    list_own_customer_debt_web_items,
)
from app.debt.dependencies import (
    DetachedCustomerDebtAuthority,
    DetachedDebtActorAuthority,
    get_detached_current_shop_debt_actor_authority,
    get_detached_customer_debt_authority,
)
from app.debt.enums import DebtStatus
from app.debt.service import create_pending_debt_proposal
from app.debt.tenant_cancel_service import CancelTenantDebtCommand, cancel_tenant_debt
from app.debt.tenant_read_service import (
    get_tenant_debt_detail,
    list_tenant_customer_debts,
)
from app.debt.values import DebtId, DebtRevision
from app.debt.web_presentation import (
    COPY,
    debt_error_message,
    debt_notice,
    resolve_debt_web_language,
)
from app.offers.enums import OfferLanguage
from app.security_headers import mark_auth_response_no_store
from app.settings import Settings
from app.shop.context import CurrentShopContext
from app.shop.dependencies import ShopSelectionRequired, require_shop_staff
from app.shop.enums import ShopStatus
from app.shop.values import ShopId
from app.shop_customer.repository import get_shop_customer_by_shop
from app.shop_customer.values import ShopCustomerId

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
LOGIN_PATH = "/auth/login"
SHOP_SELECT_PATH = "/shop/select"


@router.get(
    "/shop/customers/{shop_customer_id}/debts",
    response_class=HTMLResponse,
    response_model=None,
)
def shop_customer_debts_page(
    shop_customer_id: UUID,
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    resolved = _require_current_shop(db, settings, context)
    if isinstance(resolved, Response):
        return resolved
    _user, shop = resolved
    assert shop.shop is not None
    linked = get_shop_customer_by_shop(
        db,
        shop_id=ShopId(shop.shop.id),
        shop_customer_id=ShopCustomerId(shop_customer_id),
    )
    if linked is None:
        return _redirect("/shop/customers", error=ErrorCode.DEBT_UNAVAILABLE)
    rows = list_tenant_customer_debts(
        db,
        shop_id=ShopId(shop.shop.id),
        shop_customer_id=ShopCustomerId(shop_customer_id),
    )
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    response = templates.TemplateResponse(
        request,
        "debt/shop_list.html",
        {
            "page_language": language.value,
            "copy": COPY[language],
            "rows": rows,
            "has_rows": bool(rows),
            "shop_customer_id": shop_customer_id,
            "can_create": shop.status is ShopStatus.ACTIVE,
            "error_message": debt_error_message(language, error),
            "notice": debt_notice(language, notice),
        },
    )
    return mark_auth_response_no_store(response)


@router.get(
    "/shop/customers/{shop_customer_id}/debts/new",
    response_class=HTMLResponse,
    response_model=None,
)
def new_shop_customer_debt_page(
    shop_customer_id: UUID,
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
    error: str | None = None,
) -> Response:
    resolved = _require_current_shop(db, settings, context)
    if isinstance(resolved, Response):
        return resolved
    _user, shop = resolved
    assert shop.shop is not None
    if shop.status is not ShopStatus.ACTIVE:
        return _redirect(
            f"/shop/customers/{shop_customer_id}/debts",
            error=ErrorCode.SHOP_SUSPENDED,
        )
    linked = get_shop_customer_by_shop(
        db,
        shop_id=ShopId(shop.shop.id),
        shop_customer_id=ShopCustomerId(shop_customer_id),
    )
    if linked is None:
        return _redirect("/shop/customers", error=ErrorCode.DEBT_UNAVAILABLE)
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    response = templates.TemplateResponse(
        request,
        "debt/shop_new.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": COPY[language],
                "shop_customer_id": shop_customer_id,
                "idempotency_key": str(uuid4()),
                "error_message": debt_error_message(language, error),
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post("/shop/customers/{shop_customer_id}/debts", response_model=None)
def create_shop_customer_debt(
    shop_customer_id: UUID,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    settings: Annotated[Settings, Depends(get_settings)],
    authority: Annotated[
        object, Depends(get_detached_current_shop_debt_actor_authority)
    ],
    original_amount_uzs: Annotated[str, Form()] = "",
    discount_percent: Annotated[str, Form()] = "",
    due_date: Annotated[str, Form()] = "",
    idempotency_key: Annotated[str, Form()] = "",
) -> Response:
    list_path = f"/shop/customers/{shop_customer_id}/debts"
    new_path = f"{list_path}/new"
    if not isinstance(authority, DetachedDebtActorAuthority):
        return _redirect(new_path, error=ErrorCode.FORBIDDEN)
    if not authority.is_authenticated:
        return _redirect_detached_shop_authority(authority, settings)
    try:
        command = assemble_create_debt_command(
            form=CreateDebtRawForm(
                original_amount_uzs=original_amount_uzs,
                discount_percent=discount_percent,
                due_date=due_date,
                idempotency_key=idempotency_key,
            ),
            header_idempotency_key=request.headers.get("Idempotency-Key"),
            now=now,
        )
    except (TypeError, ValueError):
        return _redirect(new_path, error=ErrorCode.VALIDATION_ERROR)
    with request.app.state.database_session_factory.begin() as db:
        result = create_pending_debt_proposal(
            db,
            authority=authority,
            shop_customer_id=ShopCustomerId(shop_customer_id),
            command=command,
        )
    if result.error is not None:
        return _redirect(new_path, error=result.error)
    assert result.debt_id is not None
    return _redirect(f"/shop/debts/{result.debt_id.as_uuid()}", notice="created")


@router.get("/shop/debts/{debt_id}", response_class=HTMLResponse, response_model=None)
def shop_debt_detail_page(
    debt_id: UUID,
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    resolved = _require_current_shop(db, settings, context)
    if isinstance(resolved, Response):
        return resolved
    _user, shop = resolved
    assert shop.shop is not None
    detail = get_tenant_debt_detail(
        db,
        shop_id=ShopId(shop.shop.id),
        debt_id=DebtId(debt_id),
    )
    if detail is None:
        return _redirect("/shop/customers", error=ErrorCode.DEBT_UNAVAILABLE)
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    response = templates.TemplateResponse(
        request,
        "debt/shop_detail.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": COPY[language],
                "detail": detail,
                "can_cancel": (
                    detail.status is DebtStatus.PENDING
                    and shop.status is ShopStatus.ACTIVE
                ),
                "error_message": debt_error_message(language, error),
                "notice": debt_notice(language, notice),
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post("/shop/debts/{debt_id}/cancel", response_model=None)
def cancel_shop_debt(
    debt_id: UUID,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    settings: Annotated[Settings, Depends(get_settings)],
    authority: Annotated[
        object, Depends(get_detached_current_shop_debt_actor_authority)
    ],
    expected_revision: Annotated[str, Form()] = "",
    reason: Annotated[str, Form()] = "",
) -> Response:
    path = f"/shop/debts/{debt_id}"
    if not isinstance(authority, DetachedDebtActorAuthority):
        return _redirect(path, error=ErrorCode.FORBIDDEN)
    if not authority.is_authenticated:
        return _redirect_detached_shop_authority(authority, settings)
    try:
        command = CancelTenantDebtCommand(
            debt_id=DebtId(debt_id),
            expected_revision=DebtRevision(int(expected_revision)),
            now=now,
            raw_reason=reason,
        )
    except (TypeError, ValueError):
        return _redirect(path, error=ErrorCode.VALIDATION_ERROR)
    with request.app.state.database_session_factory.begin() as db:
        result = cancel_tenant_debt(db, authority=authority, command=command)
    return _redirect(
        path,
        error=result.error,
        notice="cancelled" if result.error is None else None,
    )


@router.get("/customer/debts", response_class=HTMLResponse, response_model=None)
def customer_debts_page(
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
) -> Response:
    resolved = _customer_authority_or_response(db, settings, context)
    if isinstance(resolved, Response):
        return resolved
    rows = list_own_customer_debt_web_items(db, authority=resolved)
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    response = templates.TemplateResponse(
        request,
        "debt/customer_list.html",
        {
            "page_language": language.value,
            "copy": COPY[language],
            "rows": rows,
            "has_rows": bool(rows),
        },
    )
    return mark_auth_response_no_store(response)


@router.get(
    "/customer/debts/{debt_id}", response_class=HTMLResponse, response_model=None
)
def customer_debt_detail_page(
    debt_id: UUID,
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    resolved = _customer_authority_or_response(db, settings, context)
    if isinstance(resolved, Response):
        return resolved
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    offer_language = _offer_language(language.value)
    result = get_own_customer_debt_web_detail(
        db,
        authority=resolved,
        debt_id=DebtId(debt_id),
        language=offer_language,
    )
    if result.error is not None:
        return _redirect("/customer/debts", error=ErrorCode.DEBT_UNAVAILABLE)
    assert result.detail is not None
    response = templates.TemplateResponse(
        request,
        "debt/customer_detail.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": COPY[language],
                "detail": result.detail,
                "error_message": debt_error_message(language, error),
                "notice": debt_notice(language, notice),
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post("/customer/debts/{debt_id}/accept", response_model=None)
def accept_customer_debt(
    debt_id: UUID,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    authority: Annotated[object, Depends(get_detached_customer_debt_authority)],
    expected_revision: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    displayed_offer_text_id: Annotated[str, Form()] = "",
) -> Response:
    path = f"/customer/debts/{debt_id}"
    if not isinstance(authority, DetachedCustomerDebtAuthority):
        return _redirect(path, error=ErrorCode.DEBT_UNAVAILABLE)
    if not authority.is_authenticated:
        return _redirect_detached_customer_authority(authority, request)
    if authority.authority is None:
        return _redirect("/customer/debts", error=ErrorCode.DEBT_UNAVAILABLE)
    try:
        command = AcceptCustomerDebtCommand(
            debt_id=DebtId(debt_id),
            expected_revision=DebtRevision(int(expected_revision)),
            language=OfferLanguage(language),
            displayed_offer_text_id=UUID(displayed_offer_text_id),
            now=now,
            raw_user_agent=request.headers.get("user-agent"),
        )
    except (TypeError, ValueError):
        return _redirect(path, error=ErrorCode.VALIDATION_ERROR)
    with request.app.state.database_session_factory.begin() as db:
        result = accept_own_customer_debt(
            db, authority=authority.authority, command=command
        )
    return _redirect(
        path,
        error=result.error,
        notice="accepted" if result.error is None else None,
    )


@router.post("/customer/debts/{debt_id}/reject", response_model=None)
def reject_customer_debt(
    debt_id: UUID,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    authority: Annotated[object, Depends(get_detached_customer_debt_authority)],
    expected_revision: Annotated[str, Form()] = "",
    reason: Annotated[str, Form()] = "",
) -> Response:
    path = f"/customer/debts/{debt_id}"
    if not isinstance(authority, DetachedCustomerDebtAuthority):
        return _redirect(path, error=ErrorCode.DEBT_UNAVAILABLE)
    if not authority.is_authenticated:
        return _redirect_detached_customer_authority(authority, request)
    if authority.authority is None:
        return _redirect("/customer/debts", error=ErrorCode.DEBT_UNAVAILABLE)
    try:
        command = RejectCustomerDebtCommand(
            debt_id=DebtId(debt_id),
            expected_revision=DebtRevision(int(expected_revision)),
            now=now,
            raw_reason=reason,
        )
    except (TypeError, ValueError):
        return _redirect(path, error=ErrorCode.VALIDATION_ERROR)
    with request.app.state.database_session_factory.begin() as db:
        result = reject_own_customer_debt(
            db, authority=authority.authority, command=command
        )
    return _redirect(
        path,
        error=result.error,
        notice="rejected" if result.error is None else None,
    )


def _require_current_shop(
    db: DatabaseSession,
    settings: Settings,
    context: CurrentSessionContext,
) -> tuple[object, CurrentShopContext] | Response:
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_login(context, settings)
    try:
        shop = require_shop_staff(db, user, context)
    except ShopSelectionRequired:
        return mark_auth_response_no_store(
            RedirectResponse(SHOP_SELECT_PATH, status_code=status.HTTP_303_SEE_OTHER)
        )
    return user, shop


def _customer_authority_or_response(
    db: DatabaseSession,
    settings: Settings,
    context: CurrentSessionContext,
) -> CustomerDebtAuthority | Response:
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_login(context, settings)
    authority = resolve_own_customer_debt_authority(db, authenticated_user=user)
    if authority is None:
        return mark_auth_response_no_store(
            RedirectResponse("/customer/profile", status_code=status.HTTP_303_SEE_OTHER)
        )
    return authority


def _redirect(
    path: str,
    *,
    error: ErrorCode | None = None,
    notice: str | None = None,
) -> Response:
    marker = error.value if error is not None else notice
    if marker is not None:
        path = f"{path}?{'error' if error is not None else 'notice'}={marker}"
    return mark_auth_response_no_store(
        RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)
    )


def _redirect_login(context: CurrentSessionContext, settings: Settings) -> Response:
    response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    response.headers["X-Error-Code"] = (
        ErrorCode.SESSION_EXPIRED.value
        if context.status is CurrentSessionStatus.EXPIRED
        else ErrorCode.UNAUTHORIZED.value
    )
    if context.status in {
        CurrentSessionStatus.INVALID,
        CurrentSessionStatus.REVOKED,
        CurrentSessionStatus.EXPIRED,
        CurrentSessionStatus.INACTIVE_USER,
    }:
        delete_session_cookie(response, settings)
    return mark_auth_response_no_store(response)


def _redirect_detached_shop_authority(
    authority: DetachedDebtActorAuthority, settings: Settings
) -> Response:
    if authority.status is CurrentSessionStatus.AUTHENTICATED:
        return mark_auth_response_no_store(
            RedirectResponse(SHOP_SELECT_PATH, status_code=status.HTTP_303_SEE_OTHER)
        )
    response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    response.headers["X-Error-Code"] = (
        ErrorCode.SESSION_EXPIRED.value
        if authority.status is CurrentSessionStatus.EXPIRED
        else ErrorCode.UNAUTHORIZED.value
    )
    if authority.status in {
        CurrentSessionStatus.INVALID,
        CurrentSessionStatus.REVOKED,
        CurrentSessionStatus.EXPIRED,
        CurrentSessionStatus.INACTIVE_USER,
    }:
        delete_session_cookie(response, settings)
    return mark_auth_response_no_store(response)


def _redirect_detached_customer_authority(
    authority: DetachedCustomerDebtAuthority, request: Request
) -> Response:
    settings = request.app.state.settings
    response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    response.headers["X-Error-Code"] = (
        ErrorCode.SESSION_EXPIRED.value
        if authority.status is CurrentSessionStatus.EXPIRED
        else ErrorCode.UNAUTHORIZED.value
    )
    if authority.status in {
        CurrentSessionStatus.INVALID,
        CurrentSessionStatus.REVOKED,
        CurrentSessionStatus.EXPIRED,
        CurrentSessionStatus.INACTIVE_USER,
    }:
        delete_session_cookie(response, settings)
    return mark_auth_response_no_store(response)


def _offer_language(value: str) -> OfferLanguage:
    return OfferLanguage.RU if value == "ru" else OfferLanguage.UZ_LATN
