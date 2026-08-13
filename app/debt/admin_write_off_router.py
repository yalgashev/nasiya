"""Bounded platform-admin SSR routes for the M17 write-off action."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.deps import (
    CurrentSessionContext,
    get_current_session_context,
    get_database_session,
    validate_csrf,
)
from app.auth.error_codes import ErrorCode
from app.auth.template_context import with_csrf_context
from app.debt.admin_write_off_presentation import (
    ADMIN_WRITE_OFF_COPY,
    present_admin_write_off_candidates,
    present_admin_write_off_completed,
    present_admin_write_off_fresh,
)
from app.debt.commands import (
    WriteOffDebtRawForm,
    assemble_write_off_debt_command,
)
from app.debt.web_presentation import resolve_debt_web_language
from app.debt.write_off_service import WriteOffMutationRejected, write_off_overdue_debt
from app.offers.authorization import PlatformAdminActor, require_platform_admin_actor
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.security_headers import mark_auth_response_no_store

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get(
    "/admin/debts/write-off-candidates",
    response_class=HTMLResponse,
    response_model=None,
)
def admin_write_off_candidates(
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    actor: Annotated[PlatformAdminActor, Depends(require_platform_admin_actor)],
) -> Response:
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    try:
        candidates = present_admin_write_off_candidates(db, actor=actor)
    except PermissionError:
        return _redirect_candidates()
    return _render(
        request,
        "debt/admin_write_off_candidates.html",
        {
            "copy": ADMIN_WRITE_OFF_COPY[language],
            "candidates": candidates,
        },
        page_language=language.value,
    )


@router.get(
    "/admin/debts/{debt_id}/write-off",
    response_class=HTMLResponse,
    response_model=None,
)
def admin_write_off_detail(
    debt_id: UUID,
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session, scope="function")],
    actor: Annotated[PlatformAdminActor, Depends(require_platform_admin_actor)],
    context: Annotated[CurrentSessionContext, Depends(get_current_session_context)],
    error: str | None = None,
) -> Response:
    from app.debt.values import DebtId

    locator = DebtId(debt_id)
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    try:
        completed = present_admin_write_off_completed(db, actor=actor, debt_id=locator)
        fresh = (
            None
            if completed is not None
            else present_admin_write_off_fresh(db, actor=actor, debt_id=locator)
        )
    except PermissionError:
        return _redirect_candidates()
    if completed is None and fresh is None:
        return _redirect_candidates()
    template_context: dict[str, object] = {
        "copy": ADMIN_WRITE_OFF_COPY[language],
        "completed": completed,
        "fresh": fresh,
        "error_message": (
            ADMIN_WRITE_OFF_COPY[language].generic_error
            if error == "unavailable"
            else None
        ),
    }
    if fresh is not None:
        template_context.update(
            {
                "post_action": fresh.summary.detail_path,
                "idempotency_key": str(uuid4()),
                "expected_revision": fresh.revision.value,
            }
        )
        template_context = with_csrf_context(
            template_context,
            context.get_session_row(),
        )
    return _render(
        request,
        "debt/admin_write_off_detail.html",
        template_context,
        page_language=language.value,
    )


@router.post("/admin/debts/{debt_id}/write-off", response_model=None)
def admin_write_off_create(
    debt_id: UUID,
    request: Request,
    actor: Annotated[PlatformAdminActor, Depends(require_platform_admin_actor)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    expected_revision: Annotated[str, Form()] = "",
    reason: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Form()] = None,
    confirmed: Annotated[str | None, Form()] = None,
) -> Response:
    _ = _csrf
    if confirmed != "yes":
        return _redirect_detail(debt_id, error=True)
    assembly = assemble_write_off_debt_command(
        actor=actor,
        raw=WriteOffDebtRawForm(
            debt_id=str(debt_id),
            expected_revision=expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        ),
    )
    if assembly.command is None:
        return _redirect_detail(debt_id, error=True)
    try:
        with request.app.state.database_session_factory.begin() as session:
            write_off_overdue_debt(
                session,
                command=assembly.command,
                rating_append_port=_rating_append_port(request),
                clock=_write_off_clock(request),
            )
    except (PermissionError, WriteOffMutationRejected, RuntimeError, ValueError):
        return _redirect_detail(debt_id, error=True)
    return _redirect_detail(debt_id)


def _rating_append_port(request: Request) -> SqlAlchemyLockedRatingAppendAdapter:
    port = request.app.state.rating_append_port
    if not isinstance(port, SqlAlchemyLockedRatingAppendAdapter):
        raise RuntimeError("Write-off rating adapter is unavailable")
    return port


def _write_off_clock(request: Request) -> Callable[[], datetime]:
    clock = request.app.state.write_off_clock
    if not callable(clock):
        raise RuntimeError("Write-off clock is unavailable")
    return clock


def _render(
    request: Request,
    template_name: str,
    context: dict[str, object],
    *,
    page_language: str,
) -> Response:
    response = templates.TemplateResponse(
        request,
        template_name,
        {"page_language": page_language, **context},
    )
    return mark_auth_response_no_store(response)


def _redirect_candidates() -> Response:
    response = RedirectResponse(
        "/admin/debts/write-off-candidates",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.headers["X-Error-Code"] = ErrorCode.DEBT_UNAVAILABLE.value
    return mark_auth_response_no_store(response)


def _redirect_detail(debt_id: UUID, *, error: bool = False) -> Response:
    path = f"/admin/debts/{debt_id}/write-off"
    if error:
        path = f"{path}?error=unavailable"
    response = RedirectResponse(
        path,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    return mark_auth_response_no_store(response)
