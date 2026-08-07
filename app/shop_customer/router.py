"""Authenticated, tenant-scoped M12 shop-customer web flows."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session as DatabaseSession

from app.audit.repository import SqlAlchemyAuditWriter
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
from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.auth.template_context import with_csrf_context
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.customer.repository import load_existing_own_customer
from app.otp.web_presentation import OTP_LOCALE_COOKIE_NAME
from app.request_client_ip import ClientIpResolutionError, resolve_client_ip
from app.security_headers import mark_auth_response_no_store
from app.settings import Settings
from app.shop.context import CurrentShopContext
from app.shop.dependencies import ShopSelectionRequired, require_shop_staff
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import list_shops_by_ids
from app.shop.values import ShopId
from app.shop_customer.contracts import (
    DetachedShopCustomerAuthority,
    ExpectedShopUpdatedAt,
    LinkShopCustomerCommand,
    ShopCustomerLinkOutcome,
    ShopCustomerPathLocator,
    ShopCustomerPolicy,
    ShopCustomerPolicyUpdateOutcome,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
    ShopDefaultCreditPolicyUpdate,
    ShopDefaultPolicyUpdateOutcome,
    TransientCanonicalShopCustomerPhone,
    UpdateShopCustomerPolicyCommand,
)
from app.shop_customer.dependencies import get_detached_shop_customer_authority
from app.shop_customer.enums import (
    ShopCustomerListStatus,
    parse_shop_customer_list_status,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.presentation import (
    ShopCustomerWebLanguage,
    get_shop_customer_web_copy,
    get_shop_customer_web_error_message,
    resolve_shop_customer_web_language,
)
from app.shop_customer.rate_limit import record_shop_customer_link_attempt
from app.shop_customer.repository import list_customer_own_shop_customers
from app.shop_customer.service import (
    ShopCustomerLinkInternalError,
    ShopCustomerMutationDenied,
    coordinate_link_active_customer,
    update_shop_customer_policy,
    update_shop_default_credit_policy,
)
from app.shop_customer.values import (
    CreditLimitUzbekistanSom,
    MaxOpenDebts,
    ShopCustomerId,
    parse_credit_limit_uzs,
)
from app.shop_customer.web_contracts import (
    CUSTOMER_SHOPS_PATH,
    SHOP_CUSTOMER_LINK_PATH,
    SHOP_CUSTOMER_ROSTER_PAGE_SIZE,
    SHOP_CUSTOMERS_PATH,
    SHOP_SETTINGS_CREDIT_PATH,
)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
LOGIN_PATH = "/auth/login"
SHOP_SELECT_PATH = "/shop/select"


@router.get(SHOP_CUSTOMERS_PATH, response_class=HTMLResponse, response_model=None)
def shop_customers_page(
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext, Depends(get_current_session_context)
    ],
    page: int = 1,
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    resolved = _require_current_shop(db, settings, session_context)
    if isinstance(resolved, Response):
        return resolved
    user, shop_context = resolved
    assert shop_context.shop is not None
    assert shop_context.role is not None
    assert shop_context.status is not None
    safe_page = max(1, page)
    rows = _list_masked_roster(
        db,
        shop_id=ShopId(shop_context.shop.id),
        page=safe_page,
    )
    language = _language(request)
    response = templates.TemplateResponse(
        request,
        "shop_customer/roster.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": get_shop_customer_web_copy(language),
                "error_message": _message(language, error),
                "notice": _notice(language, notice),
                "rows": rows,
                "has_rows": bool(rows),
                "page": safe_page,
                "can_link": shop_context.status is ShopStatus.ACTIVE,
                "can_edit_policy": (
                    shop_context.status is ShopStatus.ACTIVE
                    and shop_context.role in {ShopRole.OWNER, ShopRole.MANAGER}
                ),
                "is_read_only": shop_context.status is ShopStatus.SUSPENDED,
                "list_statuses": tuple(ShopCustomerListStatus),
                "list_status_labels": _list_status_labels(language),
                "shop_name": shop_context.shop.name,
                "current_role": shop_context.role.value,
                "actor_is_platform_admin": user.is_platform_admin,
            },
            session_context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post(SHOP_CUSTOMER_LINK_PATH, response_model=None)
def link_shop_customer(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
    authority: Annotated[object, Depends(get_detached_shop_customer_authority)],
    phone: Annotated[str, Form()] = "",
) -> Response:
    # TX-A (auth/session touch/current-shop + CSRF) is complete in the dependency.
    # TX-B intentionally persists independently from the later domain transaction.
    if not isinstance(authority, DetachedShopCustomerAuthority):
        return _redirect_roster(error=ErrorCode.FORBIDDEN)
    try:
        client_ip = resolve_client_ip(request, settings)
    except ClientIpResolutionError:
        return _redirect_roster(error=ErrorCode.VALIDATION_ERROR)
    rate = record_shop_customer_link_attempt(
        request.app.state.database_session_factory,
        settings=settings,
        authority=authority,
        submitted_phone=phone,
        client_ip=client_ip,
        now=now,
    )
    if not rate.allowed:
        return _redirect_roster(error=ErrorCode.RATE_LIMITED)
    try:
        command = LinkShopCustomerCommand(
            authority=authority,
            target_phone=TransientCanonicalShopCustomerPhone(phone),
        )
    except ValueError:
        return _redirect_roster(error=ErrorCode.VALIDATION_ERROR)
    try:
        result = coordinate_link_active_customer(
            request.app.state.database_session_factory,
            command=command,
            now=now,
        )
    except ShopCustomerLinkInternalError:
        return _redirect_roster(error=ErrorCode.CUSTOMER_LINK_UNAVAILABLE)
    if result.outcome is ShopCustomerLinkOutcome.CREATED:
        return _redirect_roster(notice="linked")
    if result.outcome is ShopCustomerLinkOutcome.ALREADY_LINKED:
        return _redirect_roster(notice="already_linked")
    return _redirect_roster(error=ErrorCode.CUSTOMER_LINK_UNAVAILABLE)


@router.get(SHOP_SETTINGS_CREDIT_PATH, response_class=HTMLResponse, response_model=None)
def shop_credit_settings_page(
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext, Depends(get_current_session_context)
    ],
    error: str | None = None,
    notice: str | None = None,
) -> Response:
    resolved = _require_current_shop(db, settings, session_context)
    if isinstance(resolved, Response):
        return resolved
    _user, shop_context = resolved
    assert shop_context.shop is not None
    assert shop_context.role is not None
    assert shop_context.status is not None
    defaults = ShopDefaultCreditPolicy(
        credit_limit=CreditLimitUzbekistanSom(
            shop_context.shop.default_credit_limit_uzs
        ),
        max_open_debts=MaxOpenDebts(shop_context.shop.default_max_open_debts),
    )
    language = _language(request)
    response = templates.TemplateResponse(
        request,
        "shop_customer/defaults.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": get_shop_customer_web_copy(language),
                "error_message": _message(language, error),
                "notice": _notice(language, notice),
                "defaults": defaults,
                "expected_updated_at": _format_timestamp(shop_context.shop.updated_at),
                "can_edit": (
                    shop_context.status is ShopStatus.ACTIVE
                    and shop_context.role is ShopRole.OWNER
                ),
                "is_read_only": shop_context.status is ShopStatus.SUSPENDED,
            },
            session_context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post(SHOP_SETTINGS_CREDIT_PATH, response_model=None)
def update_shop_credit_settings(
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    authority: Annotated[object, Depends(get_detached_shop_customer_authority)],
    expected_updated_at: Annotated[str, Form()] = "",
    credit_limit_uzs: Annotated[str, Form()] = "",
    max_open_debts: Annotated[str, Form()] = "",
) -> Response:
    command = _parse_default_command(
        expected_updated_at=expected_updated_at,
        credit_limit_uzs=credit_limit_uzs,
        max_open_debts=max_open_debts,
    )
    if command is None:
        return _redirect_defaults(error=ErrorCode.VALIDATION_ERROR)
    if not isinstance(authority, DetachedShopCustomerAuthority):
        return _redirect_defaults(error=ErrorCode.FORBIDDEN)
    try:
        with request.app.state.database_session_factory.begin() as db:
            result = update_shop_default_credit_policy(
                db,
                authority=authority,
                command=command,
                now=now,
                audit_writer=SqlAlchemyAuditWriter(db),
            )
    except ShopCustomerMutationDenied as exc:
        return _redirect_defaults(error=exc.error_code)
    if result.outcome is ShopDefaultPolicyUpdateOutcome.STALE:
        return _redirect_defaults(error=ErrorCode.SHOP_CUSTOMER_CHANGED)
    if result.outcome is ShopDefaultPolicyUpdateOutcome.NO_CHANGE:
        return _redirect_defaults(notice="unchanged")
    return _redirect_defaults(notice="updated")


@router.post("/shop/customers/{shop_customer_id}/policy", response_model=None)
def update_customer_policy(
    shop_customer_id: UUID,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    authority: Annotated[object, Depends(get_detached_shop_customer_authority)],
    expected_revision: Annotated[str, Form()] = "",
    credit_limit_uzs: Annotated[str, Form()] = "",
    max_open_debts: Annotated[str, Form()] = "",
    list_status: Annotated[str, Form()] = "",
) -> Response:
    command = _parse_policy_command(
        shop_customer_id=shop_customer_id,
        expected_revision=expected_revision,
        credit_limit_uzs=credit_limit_uzs,
        max_open_debts=max_open_debts,
        list_status=list_status,
    )
    if command is None:
        return _redirect_roster(error=ErrorCode.VALIDATION_ERROR)
    if not isinstance(authority, DetachedShopCustomerAuthority):
        return _redirect_roster(error=ErrorCode.FORBIDDEN)
    try:
        with request.app.state.database_session_factory.begin() as db:
            result = update_shop_customer_policy(
                db,
                authority=authority,
                command=command,
                now=now,
                audit_writer=SqlAlchemyAuditWriter(db),
            )
    except ShopCustomerMutationDenied as exc:
        return _redirect_roster(error=exc.error_code)
    if result.outcome is ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_UNAVAILABLE:
        return _redirect_roster(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)
    if result.outcome is ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_CHANGED:
        return _redirect_roster(error=ErrorCode.SHOP_CUSTOMER_CHANGED)
    if result.outcome is ShopCustomerPolicyUpdateOutcome.NO_CHANGE:
        return _redirect_roster(notice="unchanged")
    return _redirect_roster(notice="updated")


@router.get(CUSTOMER_SHOPS_PATH, response_class=HTMLResponse, response_model=None)
def own_customer_shops_page(
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    session_context: Annotated[
        CurrentSessionContext, Depends(get_current_session_context)
    ],
) -> Response:
    try:
        user = require_user(session_context)
    except LoginRequired:
        return _redirect_login(session_context, settings)
    customer = load_existing_own_customer(db, actor_user_id=user.id)
    if (
        customer is None
        or customer.onboarding_status != CUSTOMER_ONBOARDING_STATUS_ACTIVE
    ):
        response = RedirectResponse(
            "/customer/profile", status_code=status.HTTP_303_SEE_OTHER
        )
        return mark_auth_response_no_store(response)
    shop_names = _list_own_shop_names(db, customer=customer)
    language = _language(request)
    response = templates.TemplateResponse(
        request,
        "shop_customer/own_shops.html",
        {
            "page_language": language.value,
            "copy": get_shop_customer_web_copy(language),
            "shop_names": shop_names,
            "has_shops": bool(shop_names),
        },
    )
    return mark_auth_response_no_store(response)


def _require_current_shop(
    db: DatabaseSession,
    settings: Settings,
    session_context: CurrentSessionContext,
) -> tuple[User, CurrentShopContext] | Response:
    try:
        user = require_user(session_context)
    except LoginRequired:
        return _redirect_login(session_context, settings)
    try:
        shop_context = require_shop_staff(db, user, session_context)
    except ShopSelectionRequired:
        response = RedirectResponse(
            SHOP_SELECT_PATH, status_code=status.HTTP_303_SEE_OTHER
        )
        return mark_auth_response_no_store(response)
    return user, shop_context


def _list_masked_roster(
    db: DatabaseSession,
    *,
    shop_id: ShopId,
    page: int,
) -> list[dict[str, object]]:
    statement = (
        select(ShopCustomer, User.phone)
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .join(User, User.id == Customer.user_id)
        .where(ShopCustomer.shop_id == shop_id)
        .order_by(ShopCustomer.created_at.asc(), ShopCustomer.id.asc())
        .offset((page - 1) * SHOP_CUSTOMER_ROSTER_PAGE_SIZE)
        .limit(SHOP_CUSTOMER_ROSTER_PAGE_SIZE)
    )
    return [
        {
            "locator": str(row.id),
            "masked_phone": mask_phone_for_display(phone),
            "credit_limit_uzs": str(row.credit_limit_uzs),
            "max_open_debts": row.max_open_debts,
            "list_status": row.list_status,
            "revision": row.revision,
        }
        for row, phone in db.execute(statement)
    ]


def _list_own_shop_names(db: DatabaseSession, *, customer: Customer) -> list[str]:
    linked = list_customer_own_shop_customers(db, customer_id=customer.id)
    if not linked:
        return []
    shop_ids = {ShopId(row.shop_id) for row in linked}
    shops = list_shops_by_ids(db, shop_ids=shop_ids)
    names_by_id = {shop.id: shop.name for shop in shops}
    return [names_by_id[row.shop_id] for row in linked if row.shop_id in names_by_id]


def _parse_default_command(
    *, expected_updated_at: str, credit_limit_uzs: str, max_open_debts: str
) -> ShopDefaultCreditPolicyUpdate | None:
    try:
        return ShopDefaultCreditPolicyUpdate(
            expected_updated_at=ExpectedShopUpdatedAt(
                datetime.fromisoformat(expected_updated_at)
            ),
            new_defaults=ShopDefaultCreditPolicy(
                credit_limit=parse_credit_limit_uzs(credit_limit_uzs),
                max_open_debts=MaxOpenDebts(int(max_open_debts)),
            ),
        )
    except (TypeError, ValueError):
        return None


def _parse_policy_command(
    *,
    shop_customer_id: UUID,
    expected_revision: str,
    credit_limit_uzs: str,
    max_open_debts: str,
    list_status: str,
) -> UpdateShopCustomerPolicyCommand | None:
    try:
        return UpdateShopCustomerPolicyCommand(
            locator=ShopCustomerPathLocator(ShopCustomerId(shop_customer_id)),
            expected_revision=ShopCustomerRevision(int(expected_revision)),
            new_policy=ShopCustomerPolicy(
                credit_limit=parse_credit_limit_uzs(credit_limit_uzs),
                max_open_debts=MaxOpenDebts(int(max_open_debts)),
                list_status=parse_shop_customer_list_status(list_status),
            ),
        )
    except (TypeError, ValueError):
        return None


def _redirect_roster(
    *, error: ErrorCode | None = None, notice: str | None = None
) -> Response:
    return _redirect(SHOP_CUSTOMERS_PATH, error=error, notice=notice)


def _redirect_defaults(
    *, error: ErrorCode | None = None, notice: str | None = None
) -> Response:
    return _redirect(SHOP_SETTINGS_CREDIT_PATH, error=error, notice=notice)


def _redirect(path: str, *, error: ErrorCode | None, notice: str | None) -> Response:
    marker = error.value if error is not None else notice
    target = (
        path
        if marker is None
        else f"{path}?{'error' if error is not None else 'notice'}={marker}"
    )
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    return mark_auth_response_no_store(response)


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


def _language(request: Request):
    return resolve_shop_customer_web_language(
        request.cookies.get(OTP_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )


def _message(language, raw_error: str | None) -> str | None:
    try:
        error = ErrorCode(raw_error) if raw_error is not None else None
    except ValueError:
        error = None
    return get_shop_customer_web_error_message(language, error) if error else None


def _notice(language: ShopCustomerWebLanguage, value: str | None) -> str | None:
    copy = get_shop_customer_web_copy(language)
    if value == "linked":
        return copy.linked_notice
    if value == "updated":
        return copy.updated_notice
    if value == "already_linked":
        return copy.already_linked_notice
    if value == "unchanged":
        return copy.unchanged_notice
    return None


def _list_status_labels(
    language: ShopCustomerWebLanguage,
) -> dict[ShopCustomerListStatus, str]:
    copy = get_shop_customer_web_copy(language)
    return {
        ShopCustomerListStatus.NORMAL: copy.normal_status,
        ShopCustomerListStatus.WHITELISTED: copy.whitelisted_status,
        ShopCustomerListStatus.BLACKLISTED: copy.blacklisted_status,
    }


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
