from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.cookies import delete_session_cookie
from app.auth.deps import (
    CurrentSessionContext,
    CurrentSessionStatus,
    LoginRequired,
    get_current_session_context,
    get_database_session,
    get_settings,
    require_user,
    validate_csrf,
)
from app.auth.error_codes import ErrorCode
from app.auth.template_context import with_csrf_context
from app.customer.service import (
    CustomerDraftStartError,
    get_current_customer_draft_state,
    start_customer_draft,
)
from app.security_headers import mark_auth_response_no_store
from app.settings import Settings

router = APIRouter(prefix="/customer")
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
LOGIN_PATH = "/auth/login"
ONBOARDING_PATH = "/customer/onboarding"


@router.get("/onboarding", response_class=HTMLResponse, response_model=None)
def onboarding_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> Response:
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    customer_state = get_current_customer_draft_state(db, user)
    response = templates.TemplateResponse(
        request,
        "customer/onboarding.html",
        with_csrf_context(
            {"customer_state": customer_state},
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.get("/profile", response_class=HTMLResponse, response_model=None)
def profile_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> Response:
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    customer_state = get_current_customer_draft_state(db, user)
    if customer_state is None:
        response = RedirectResponse(
            ONBOARDING_PATH,
            status_code=status.HTTP_303_SEE_OTHER,
        )
        return mark_auth_response_no_store(response)

    response = templates.TemplateResponse(
        request,
        "customer/profile.html",
        {"customer_state": customer_state},
    )
    return mark_auth_response_no_store(response)


@router.post("/onboarding/start", response_model=None)
def start_onboarding(
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> Response:
    _ = _csrf
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    try:
        start_customer_draft(db, user.id)
    except CustomerDraftStartError:
        return _render_customer_start_failed()

    response = RedirectResponse(
        "/customer/profile",
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


def _render_customer_start_failed() -> Response:
    response = HTMLResponse(
        "<!doctype html>"
        '<html lang="uz">'
        "<head>"
        '<meta charset="utf-8">'
        "<title>Customer draft xatosi</title>"
        "</head>"
        "<body>"
        "<main>"
        "<h1>Customer draft boshlanmadi</h1>"
        "</main>"
        "</body>"
        "</html>",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
    return mark_auth_response_no_store(response)
