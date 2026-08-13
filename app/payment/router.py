"""Thin authenticated SSR/PRG adapters for the frozen eight Payment routes."""

from __future__ import annotations

from collections.abc import Mapping
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
from app.auth.models import User
from app.auth.template_context import with_csrf_context
from app.debt.enums import DebtStatus
from app.debt.presentation import DebtWebLanguage
from app.debt.tenant_read_service import TenantDebtDetailProjection
from app.debt.values import DebtId
from app.debt.web_presentation import resolve_debt_web_language
from app.payment.commands import (
    CreatePaymentV2RawForm,
    VoidPaymentRawForm,
    assemble_create_payment_request,
    assemble_void_payment_command,
)
from app.payment.dependencies import (
    DetachedPaymentActorContext,
    DetachedPaymentReadActorContext,
    get_detached_current_shop_payment_actor_context,
    get_detached_current_shop_payment_read_actor_context,
)
from app.payment.enums import PaymentMethod, PaymentVoidReason
from app.payment.presentation import (
    get_payment_void_reason_label,
    get_payment_web_copy,
    get_payment_web_error_message,
)
from app.payment.read_service import (
    get_own_customer_payment_history_view,
    get_own_customer_payment_receipt_view,
    get_tenant_debt_detail_with_payment_progress,
    get_tenant_payment_history_view,
    get_tenant_payment_receipt_view,
)
from app.payment.repository import get_tenant_payment
from app.payment.service import (
    PaymentMutationRejected,
    record_debt_payment,
    resolve_completed_m14_payment_replay,
)
from app.payment.values import PaymentId
from app.payment.void_service import void_payment
from app.payment.void_targeting import discover_tenant_payment_void_target
from app.security_headers import mark_auth_response_no_store
from app.settings import Settings
from app.shop.context import CurrentShopContext
from app.shop.dependencies import ShopSelectionRequired, require_shop_staff
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import get_shop
from app.shop.values import ShopId

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
LOGIN_PATH = "/auth/login"


@router.get(
    "/shop/debts/{debt_id}/payments",
    name="shop_debt_payment_list",
    response_class=HTMLResponse,
    response_model=None,
)
def shop_debt_payment_list(
    debt_id: str,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    actor: Annotated[
        DetachedPaymentReadActorContext,
        Depends(get_detached_current_shop_payment_read_actor_context),
    ],
) -> Response:
    parsed_debt_id = _parse_debt_path_locator(debt_id)
    if parsed_debt_id is None:
        return _redirect("/shop/customers", error=ErrorCode.DEBT_UNAVAILABLE)
    with request.app.state.database_session_factory() as db:
        view = get_tenant_payment_history_view(
            db, actor=actor, debt_id=parsed_debt_id, server_now=now
        )
    if view.error is not None:
        return _shop_view_error_response(parsed_debt_id.as_uuid(), view.error)
    assert view.debt is not None and view.shop_status is not None
    progress = view.debt.payment_progress
    assert progress is not None
    can_create = view.shop_status is ShopStatus.ACTIVE and progress.is_payable
    return _template_response(
        request,
        "payment/shop_list.html",
        actor.language,
        {
            "debt": view.debt,
            "history": view.history,
            "debt_id": parsed_debt_id.as_uuid(),
            "can_create": can_create,
            "status_label": _status_label(
                actor.language,
                view.debt.status,
                is_effectively_overdue=progress.is_effectively_overdue,
            ),
            "balance_basis_label": _basis_label(
                actor.language, progress.balance_basis.value
            ),
            "target_amount_label": _target_amount_label(
                actor.language, progress.balance_basis.value
            ),
            "late_terms_message": _late_terms_message(
                actor.language, progress.is_effectively_overdue
            ),
            "recovery_terms_message": _recovery_terms_message(
                actor.language, view.debt.status
            ),
            "read_only_message": _shop_read_only_message(
                actor.language,
                shop_status=view.shop_status,
                debt_status=view.debt.status,
                payable=progress.is_payable,
            ),
            "method_labels": _method_labels(actor.language),
        },
    )


@router.get(
    "/shop/debts/{debt_id}/payments/new",
    name="shop_debt_payment_new",
    response_class=HTMLResponse,
    response_model=None,
)
def new_shop_debt_payment_page(
    debt_id: str,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
    error: str | None = None,
) -> Response:
    resolved = _current_shop_or_response(db, context, settings)
    if isinstance(resolved, Response):
        return resolved
    user, shop = resolved
    assert shop.shop is not None and shop.role is not None
    parsed_debt_id = _parse_debt_path_locator(debt_id)
    if parsed_debt_id is None:
        return _redirect("/shop/customers", error=ErrorCode.DEBT_UNAVAILABLE)
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    actor = DetachedPaymentReadActorContext(
        actor_user_id=user.id,
        current_shop_id=shop.shop.id,
        role_hint=shop.role,
        language=language,
    )
    if shop.status is not ShopStatus.ACTIVE:
        return _redirect(
            f"/shop/debts/{parsed_debt_id.as_uuid()}",
            error=ErrorCode.SHOP_SUSPENDED,
        )
    create_error, detail = _shop_payment_form_detail(
        db,
        actor=actor,
        debt_id=parsed_debt_id,
        server_now=now,
    )
    if create_error is not None:
        return _redirect(f"/shop/debts/{parsed_debt_id.as_uuid()}", error=create_error)
    assert detail is not None and detail.payment_progress is not None
    response = templates.TemplateResponse(
        request,
        "payment/shop_new.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": get_payment_web_copy(language),
                "debt_id": parsed_debt_id.as_uuid(),
                "idempotency_key": str(uuid4()),
                "expected_revision": detail.expected_revision,
                "expected_balance_basis": (detail.payment_progress.balance_basis.value),
                "remaining_due_uzs": detail.payment_progress.remaining_due_uzs,
                "balance_basis_label": _basis_label(
                    language, detail.payment_progress.balance_basis.value
                ),
                "late_terms_message": _late_terms_message(
                    language, detail.payment_progress.is_effectively_overdue
                ),
                "recovery_terms_message": _recovery_terms_message(
                    language, detail.status
                ),
                "methods": tuple(PaymentMethod),
                "error_message": _payment_error_message(language, error),
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post(
    "/shop/debts/{debt_id}/payments",
    name="shop_debt_payment_create",
    response_model=None,
)
def create_shop_debt_payment(
    debt_id: str,
    request: Request,
    authority: Annotated[
        DetachedPaymentActorContext,
        Depends(get_detached_current_shop_payment_actor_context),
    ],
    amount_uzs: Annotated[str, Form()] = "",
    method: Annotated[str, Form()] = "",
    idempotency_key: Annotated[str | None, Form()] = None,
    expected_revision: Annotated[str, Form()] = "",
    expected_balance_basis: Annotated[str | None, Form()] = None,
) -> Response:
    parsed_debt_id = _parse_debt_path_locator(debt_id)
    if parsed_debt_id is None:
        return _redirect("/shop/customers", error=ErrorCode.DEBT_UNAVAILABLE)
    new_path = f"/shop/debts/{parsed_debt_id.as_uuid()}/payments/new"
    assembled = assemble_create_payment_request(
        actor=authority,
        form=CreatePaymentV2RawForm(
            debt_id=str(parsed_debt_id.as_uuid()),
            amount_uzs=amount_uzs,
            method=method,
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
            expected_balance_basis=expected_balance_basis,
        ),
        header_idempotency_key=request.headers.get("Idempotency-Key"),
    )
    if assembled.error is not None:
        return _redirect(new_path, error=assembled.error)
    try:
        with request.app.state.database_session_factory.begin() as db:
            if assembled.command is not None:
                result = record_debt_payment(
                    db,
                    actor=authority,
                    command=assembled.command,
                    rating_append_port=request.app.state.rating_append_port,
                )
            else:
                assert assembled.legacy_completed_replay is not None
                result = resolve_completed_m14_payment_replay(
                    db,
                    actor=authority,
                    candidate=assembled.legacy_completed_replay,
                )
    except PaymentMutationRejected as rejected:
        return _redirect(new_path, error=rejected.error)
    return _redirect(f"/shop/payments/{result.payment_id.as_uuid()}")


@router.get(
    "/shop/payments/{payment_id}",
    name="shop_payment_receipt",
    response_class=HTMLResponse,
    response_model=None,
)
def shop_payment_receipt(
    payment_id: str,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    actor: Annotated[
        DetachedPaymentReadActorContext,
        Depends(get_detached_current_shop_payment_read_actor_context),
    ],
) -> Response:
    parsed_payment_id = _parse_payment_path_locator(payment_id)
    if parsed_payment_id is None:
        return _redirect("/shop/customers", error=ErrorCode.PAYMENT_UNAVAILABLE)
    with request.app.state.database_session_factory() as db:
        view = get_tenant_payment_receipt_view(
            db, actor=actor, payment_id=parsed_payment_id, server_now=now
        )
    if view.error is not None:
        return _redirect("/shop/customers", error=view.error)
    assert (
        view.receipt is not None
        and view.debt_id is not None
        and view.void_state is not None
    )
    return _template_response(
        request,
        "payment/shop_receipt.html",
        actor.language,
        {
            "receipt": view.receipt,
            "debt_id": view.debt_id.as_uuid(),
            "payment_id": parsed_payment_id.as_uuid(),
            "method_label": _method_labels(actor.language)[view.receipt.method.value],
            "status_label": _status_label(
                actor.language,
                view.receipt.current_debt_status,
                is_effectively_overdue=(
                    view.receipt.current_debt_status is DebtStatus.OVERDUE
                ),
            ),
            "historical_basis_label": _basis_label(
                actor.language, view.receipt.historical_balance_basis.value
            ),
            "current_basis_label": _basis_label(
                actor.language, view.receipt.current_balance_basis.value
            ),
            "paid_late": (
                view.receipt.current_debt_status is DebtStatus.PAID
                and view.receipt.current_balance_basis.value == "original"
            ),
            "recovery_terms_message": _recovery_terms_message(
                actor.language, view.receipt.current_debt_status
            ),
            "void_state": view.void_state,
            "can_void": actor.role_hint in {ShopRole.OWNER, ShopRole.MANAGER},
        },
    )


@router.get(
    "/shop/payments/{payment_id}/void",
    name="shop_payment_void_form",
    response_class=HTMLResponse,
    response_model=None,
)
def shop_payment_void_form(
    payment_id: str,
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
    error: str | None = None,
) -> Response:
    resolved = _current_shop_or_response(db, context, settings)
    if isinstance(resolved, Response):
        return resolved
    _user, shop = resolved
    parsed = _parse_payment_path_locator(payment_id)
    if parsed is None or shop.shop is None or shop.role is None:
        return _redirect("/shop/customers", error=ErrorCode.PAYMENT_UNAVAILABLE)
    if shop.status is not ShopStatus.ACTIVE:
        return _redirect("/shop/customers", error=ErrorCode.SHOP_SUSPENDED)
    if shop.role not in {ShopRole.OWNER, ShopRole.MANAGER}:
        return _redirect("/shop/customers", error=ErrorCode.FORBIDDEN)
    row = get_tenant_payment(db, shop_id=ShopId(shop.shop.id), payment_id=parsed)
    if row is None or row.voided_at is not None:
        return _redirect("/shop/customers", error=ErrorCode.PAYMENT_NOT_VOIDABLE)
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    response = templates.TemplateResponse(
        request,
        "payment/shop_void.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": get_payment_web_copy(language),
                "payment_id": parsed.as_uuid(),
                "expected_revision": row.debt.revision,
                "idempotency_key": str(uuid4()),
                "reasons": tuple(
                    (reason, get_payment_void_reason_label(language, reason))
                    for reason in PaymentVoidReason
                ),
                "error_message": _payment_error_message(language, error),
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post(
    "/shop/payments/{payment_id}/void",
    name="shop_payment_void",
    response_model=None,
)
def void_shop_payment(
    payment_id: str,
    request: Request,
    authority: Annotated[
        DetachedPaymentActorContext,
        Depends(get_detached_current_shop_payment_actor_context),
    ],
    reason: Annotated[str, Form()] = "",
    idempotency_key: Annotated[str | None, Form()] = None,
    expected_revision: Annotated[str, Form()] = "",
    confirmation: Annotated[str | None, Form()] = None,
) -> Response:
    parsed = _parse_payment_path_locator(payment_id)
    if parsed is None:
        return _redirect("/shop/customers", error=ErrorCode.PAYMENT_UNAVAILABLE)
    form_path = f"/shop/payments/{parsed.as_uuid()}/void"
    with request.app.state.database_session_factory() as db:
        candidate = discover_tenant_payment_void_target(
            db, actor=authority, payment_id=parsed
        )
    if candidate is None:
        return _redirect("/shop/customers", error=ErrorCode.PAYMENT_UNAVAILABLE)
    assembled = assemble_void_payment_command(
        actor_user_id=authority.actor_user_id,
        current_shop_id=authority.current_shop_id,
        payment_id=parsed,
        server_resolved_debt_id=candidate.debt_id,
        raw=VoidPaymentRawForm(
            expected_revision=expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
            confirmed=confirmation,
        ),
    )
    if assembled.command is None:
        return _redirect(form_path, error=ErrorCode.VALIDATION_ERROR)
    try:
        with request.app.state.database_session_factory.begin() as db:
            result = void_payment(
                db,
                actor=authority,
                command=assembled.command,
                rating_port=request.app.state.rating_append_port,
            )
    except PaymentMutationRejected as rejected:
        return _redirect(form_path, error=rejected.error)
    return _redirect(f"/shop/payments/{result.payment_id.as_uuid()}")


@router.get(
    "/customer/debts/{debt_id}/payments",
    name="customer_debt_payment_list",
    response_class=HTMLResponse,
    response_model=None,
)
def customer_debt_payment_list(
    debt_id: str,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
) -> Response:
    user = _customer_user_or_response(context, settings)
    if isinstance(user, Response):
        return user
    parsed_debt_id = _parse_debt_path_locator(debt_id)
    if parsed_debt_id is None:
        return _redirect("/customer/debts", error=ErrorCode.DEBT_UNAVAILABLE)
    view = get_own_customer_payment_history_view(
        db,
        authenticated_user=user,
        debt_id=parsed_debt_id,
        server_now=now,
    )
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    if view.error is not None:
        return _redirect("/customer/debts", error=view.error)
    assert view.debt is not None
    return _template_response(
        request,
        "payment/customer_list.html",
        language,
        {
            "debt": view.debt,
            "history": view.history,
            "debt_id": parsed_debt_id.as_uuid(),
            "status_label": _status_label(
                language,
                view.debt.status,
                is_effectively_overdue=view.debt.progress.is_effectively_overdue,
            ),
            "balance_basis_label": _basis_label(
                language, view.debt.progress.balance_basis.value
            ),
            "target_amount_label": _target_amount_label(
                language, view.debt.progress.balance_basis.value
            ),
            "late_terms_message": _late_terms_message(
                language, view.debt.progress.is_effectively_overdue
            ),
            "recovery_terms_message": _recovery_terms_message(
                language, view.debt.status
            ),
            "method_labels": _method_labels(language),
        },
    )


@router.get(
    "/customer/payments/{payment_id}",
    name="customer_payment_receipt",
    response_class=HTMLResponse,
    response_model=None,
)
def customer_payment_receipt(
    payment_id: str,
    request: Request,
    now: Annotated[datetime, Depends(get_current_time)],
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
) -> Response:
    user = _customer_user_or_response(context, settings)
    if isinstance(user, Response):
        return user
    parsed_payment_id = _parse_payment_path_locator(payment_id)
    if parsed_payment_id is None:
        return _redirect("/customer/debts", error=ErrorCode.PAYMENT_UNAVAILABLE)
    view = get_own_customer_payment_receipt_view(
        db,
        authenticated_user=user,
        payment_id=parsed_payment_id,
        server_now=now,
    )
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    if view.error is not None:
        return _redirect("/customer/debts", error=view.error)
    assert (
        view.receipt is not None
        and view.debt_id is not None
        and view.void_state is not None
    )
    return _template_response(
        request,
        "payment/customer_receipt.html",
        language,
        {
            "receipt": view.receipt,
            "debt_id": view.debt_id.as_uuid(),
            "method_label": _method_labels(language)[view.receipt.method.value],
            "status_label": _status_label(
                language,
                view.receipt.current_debt_status,
                is_effectively_overdue=(
                    view.receipt.current_debt_status is DebtStatus.OVERDUE
                ),
            ),
            "historical_basis_label": _basis_label(
                language, view.receipt.historical_balance_basis.value
            ),
            "current_basis_label": _basis_label(
                language, view.receipt.current_balance_basis.value
            ),
            "paid_late": (
                view.receipt.current_debt_status is DebtStatus.PAID
                and view.receipt.current_balance_basis.value == "original"
            ),
            "recovery_terms_message": _recovery_terms_message(
                language, view.receipt.current_debt_status
            ),
            "void_state": view.void_state,
        },
    )


def _template_response(
    request: Request,
    template_name: str,
    language: DebtWebLanguage,
    context: Mapping[str, object],
) -> Response:
    response = templates.TemplateResponse(
        request,
        template_name,
        {
            "page_language": language.value,
            "copy": get_payment_web_copy(language),
            **context,
        },
    )
    return mark_auth_response_no_store(response)


def _shop_view_error_response(
    debt_id: UUID,
    error: ErrorCode,
) -> Response:
    if error is ErrorCode.DEBT_UNAVAILABLE:
        return _redirect("/shop/customers", error=error)
    return _redirect(
        f"/shop/debts/{debt_id}",
        error=error,
    )


def _parse_debt_path_locator(raw_value: str) -> DebtId | None:
    parsed = _parse_canonical_path_uuid(raw_value)
    return None if parsed is None else DebtId(parsed)


def _parse_payment_path_locator(raw_value: str) -> PaymentId | None:
    parsed = _parse_canonical_path_uuid(raw_value)
    return None if parsed is None else PaymentId(parsed)


def _parse_canonical_path_uuid(raw_value: str) -> UUID | None:
    if not isinstance(raw_value, str):
        return None
    try:
        parsed = UUID(raw_value)
    except (AttributeError, TypeError, ValueError):
        return None
    return parsed if str(parsed) == raw_value else None


def _shop_payment_form_detail(
    db: DatabaseSession,
    *,
    actor: DetachedPaymentReadActorContext,
    debt_id: DebtId,
    server_now: datetime,
) -> tuple[ErrorCode | None, TenantDebtDetailProjection | None]:
    """Read current server state before advertising the payment form."""

    shop = get_shop(db, shop_id=ShopId(actor.current_shop_id))
    if shop is None:
        return ErrorCode.DEBT_UNAVAILABLE, None
    if shop.status != ShopStatus.ACTIVE.value:
        return ErrorCode.SHOP_SUSPENDED, None
    detail = get_tenant_debt_detail_with_payment_progress(
        db,
        shop_id=ShopId(actor.current_shop_id),
        debt_id=debt_id,
        server_now=server_now,
    )
    if detail is None:
        return ErrorCode.DEBT_UNAVAILABLE, None
    progress = detail.payment_progress
    if (
        detail.status
        not in {DebtStatus.ACTIVE, DebtStatus.OVERDUE, DebtStatus.WRITTEN_OFF}
        or progress is None
        or not progress.is_payable
        or progress.remaining_due_uzs <= 0
    ):
        return ErrorCode.DEBT_NOT_PAYABLE, None
    return None, detail


def _current_shop_or_response(
    db: DatabaseSession,
    context: CurrentSessionContext,
    settings: Settings,
) -> tuple[User, CurrentShopContext] | Response:
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_login(context, settings)
    try:
        shop = require_shop_staff(db, user, context)
    except ShopSelectionRequired:
        return mark_auth_response_no_store(
            RedirectResponse("/shop/select", status_code=status.HTTP_303_SEE_OTHER)
        )
    return user, shop


def _payment_error_message(
    language: DebtWebLanguage, raw_error: str | None
) -> str | None:
    if raw_error is None:
        return None
    try:
        error = ErrorCode(raw_error)
    except ValueError:
        return None
    return get_payment_web_error_message(language, error)


def _method_labels(language: DebtWebLanguage) -> Mapping[str, str]:
    copy = get_payment_web_copy(language)
    return {method.value: copy[method.value] for method in PaymentMethod}


def _status_label(
    language: DebtWebLanguage,
    status_value: DebtStatus,
    *,
    is_effectively_overdue: bool = False,
) -> str:
    if not isinstance(status_value, DebtStatus):
        raise ValueError("Payment status is invalid")
    if is_effectively_overdue:
        return get_payment_web_copy(language)["status_overdue"]
    return get_payment_web_copy(language)[f"status_{status_value.value}"]


def _basis_label(language: DebtWebLanguage, basis_value: str) -> str:
    if basis_value not in {"discounted", "original"}:
        raise ValueError("Payment balance basis is invalid")
    return get_payment_web_copy(language)[f"{basis_value}_basis"]


def _target_amount_label(language: DebtWebLanguage, basis_value: str) -> str:
    if basis_value not in {"discounted", "original"}:
        raise ValueError("Payment target basis is invalid")
    target = "original_target" if basis_value == "original" else "discounted_target"
    return get_payment_web_copy(language)[target]


def _late_terms_message(
    language: DebtWebLanguage, is_effectively_overdue: bool
) -> str | None:
    if not isinstance(is_effectively_overdue, bool):
        raise ValueError("Payment effective overdue state is invalid")
    return (
        get_payment_web_copy(language)["late_terms"] if is_effectively_overdue else None
    )


def _recovery_terms_message(
    language: DebtWebLanguage, debt_status: DebtStatus
) -> str | None:
    if not isinstance(debt_status, DebtStatus):
        raise ValueError("Payment recovery status is invalid")
    return (
        get_payment_web_copy(language)["recovery_terms"]
        if debt_status is DebtStatus.WRITTEN_OFF
        else None
    )


def _shop_read_only_message(
    language: DebtWebLanguage,
    *,
    shop_status: ShopStatus,
    debt_status: DebtStatus,
    payable: bool,
) -> str | None:
    if shop_status is ShopStatus.SUSPENDED:
        return get_payment_web_copy(language)["read_only_suspended"]
    if debt_status is DebtStatus.ACTIVE and not payable:
        return get_payment_web_copy(language)["read_only_past_due"]
    if debt_status not in {
        DebtStatus.ACTIVE,
        DebtStatus.OVERDUE,
        DebtStatus.WRITTEN_OFF,
    }:
        return get_payment_web_copy(language)["read_only_closed"]
    return None


def _customer_user_or_response(
    context: CurrentSessionContext, settings: Settings
) -> User | Response:
    try:
        return require_user(context)
    except LoginRequired:
        return _redirect_login(context, settings)


def _redirect(path: str, *, error: ErrorCode | None = None) -> Response:
    if error is not None:
        path = f"{path}?error={error.value}"
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
