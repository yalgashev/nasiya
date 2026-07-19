from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.cookies import delete_session_cookie, set_session_cookie
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
from app.auth.login_rate_limit import LoginRateLimitPolicy, get_login_client_host
from app.auth.phone import (
    PhoneNormalizationError,
    mask_phone_for_display,
    normalize_uzbekistan_phone,
)
from app.auth.service import authenticate
from app.auth.sessions import (
    CreatedSession,
    RawSessionToken,
    UserSessionStatus,
    UserSessionSummary,
    create_anonymous_session,
    list_user_sessions,
    revoke_other_sessions,
    revoke_session,
    revoke_user_session,
    rotate_session,
)
from app.auth.template_context import with_csrf_context
from app.security_headers import mark_auth_response_no_store
from app.settings import Settings

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
LOGIN_FAILED_MESSAGE = "Telefon raqam yoki parol noto'g'ri."
ACCOUNT_PATH = "/auth/account"
LOGIN_PATH = "/auth/login"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
router = APIRouter(prefix="/auth")


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> Response:
    if context.is_authenticated:
        response = RedirectResponse(
            "/auth/account",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        return mark_auth_response_no_store(response)

    created_session = _get_or_create_anonymous_session(
        db=db,
        request=request,
        settings=settings,
        context=context,
        now=now,
    )
    session = created_session.session
    response = templates.TemplateResponse(
        request,
        "auth/login.html",
        with_csrf_context({"error_message": None}, session),
    )
    set_session_cookie(response, created_session.raw_token, settings)
    return mark_auth_response_no_store(response)


@router.post("/login", response_class=HTMLResponse, response_model=None)
def submit_login(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    _csrf: Annotated[None, Depends(validate_csrf)],
    phone: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next_url: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    _ = _csrf
    client_host = get_login_client_host(request)
    rate_limit_policy = LoginRateLimitPolicy(db=db, settings=settings)
    if not _is_login_input_valid(phone, password):
        validation_rate_limit_result = rate_limit_policy.record_failure(
            phone,
            client_host,
            now,
        )
        if not validation_rate_limit_result.allowed:
            return _render_login_failure(
                request=request,
                context=context,
                message=validation_rate_limit_result.public_error["message"]
                if validation_rate_limit_result.public_error
                else LOGIN_FAILED_MESSAGE,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_code=ErrorCode.RATE_LIMITED,
            )
        return _render_login_failure(
            request=request,
            context=context,
            message=LOGIN_FAILED_MESSAGE,
            status_code=status.HTTP_200_OK,
            error_code=ErrorCode.VALIDATION_ERROR,
        )

    rate_limit_result = rate_limit_policy.check(phone, client_host, now)
    if not rate_limit_result.allowed:
        return _render_login_failure(
            request=request,
            context=context,
            message=rate_limit_result.public_error["message"]
            if rate_limit_result.public_error
            else LOGIN_FAILED_MESSAGE,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_code=ErrorCode.RATE_LIMITED,
        )

    user = authenticate(db, phone, password)
    if user is None:
        failure_rate_limit_result = rate_limit_policy.record_failure(
            phone,
            client_host,
            now,
        )
        if not failure_rate_limit_result.allowed:
            return _render_login_failure(
                request=request,
                context=context,
                message=failure_rate_limit_result.public_error["message"]
                if failure_rate_limit_result.public_error
                else LOGIN_FAILED_MESSAGE,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_code=ErrorCode.RATE_LIMITED,
            )
        return _render_login_failure(
            request=request,
            context=context,
            message=LOGIN_FAILED_MESSAGE,
            status_code=status.HTTP_200_OK,
            error_code=ErrorCode.UNAUTHORIZED,
        )

    rate_limit_policy.clear_phone_failures_after_success(phone)
    created_session = rotate_session(
        db=db,
        current_session=context.get_session_row(),
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        now=now,
        settings=settings,
    )
    response = RedirectResponse(
        _get_safe_redirect_target(next_url),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_session_cookie(response, created_session.raw_token, settings)
    return mark_auth_response_no_store(response)


@router.get("/account", response_class=HTMLResponse, response_model=None)
def account_page(
    request: Request,
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

    session = context.get_session_row()
    response = templates.TemplateResponse(
        request,
        "auth/account.html",
        with_csrf_context(
            {"masked_phone": mask_phone_for_display(user.phone)},
            session,
        ),
    )
    return mark_auth_response_no_store(response)


@router.get("/sessions", response_class=HTMLResponse, response_model=None)
def sessions_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> Response:
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    session = context.get_session_row()
    session_summaries = list_user_sessions(db, user.id, now)
    response = templates.TemplateResponse(
        request,
        "auth/sessions.html",
        with_csrf_context(
            {
                "sessions": [
                    _get_session_view_model(summary, context.session_id)
                    for summary in session_summaries
                ],
                "has_other_active_sessions": any(
                    _can_revoke_session(summary, context.session_id)
                    for summary in session_summaries
                ),
            },
            session,
        ),
    )
    return mark_auth_response_no_store(response)


@router.post("/logout", response_class=HTMLResponse, response_model=None)
def submit_logout(
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> Response:
    _ = _csrf
    try:
        require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    session = context.get_session_row()
    if session is not None and session.revoked_at is None:
        revoke_session(db, session, now)

    response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    delete_session_cookie(response, settings)
    return mark_auth_response_no_store(response)


@router.post("/sessions/{session_id}/revoke", response_model=None)
def revoke_one_session(
    session_id: UUID,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
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

    revoked = revoke_user_session(db, user.id, session_id, now)
    if not revoked:
        return _render_session_not_found()

    if session_id == context.session_id:
        response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
        delete_session_cookie(response, settings)
    else:
        response = RedirectResponse(
            "/auth/sessions",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return mark_auth_response_no_store(response)


@router.post("/sessions/revoke-others", response_model=None)
def revoke_other_user_sessions(
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
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

    if context.session_id is None:
        return _redirect_auth_login(context, settings)

    revoke_other_sessions(db, user.id, context.session_id, now)
    response = RedirectResponse(
        "/auth/sessions",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    return mark_auth_response_no_store(response)


def _get_or_create_anonymous_session(
    db: DatabaseSession,
    request: Request,
    settings: Settings,
    context: CurrentSessionContext,
    now: datetime,
) -> CreatedSession:
    session = context.get_session_row()
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if context.status == CurrentSessionStatus.ANONYMOUS and session and cookie_value:
        return CreatedSession(
            raw_token=RawSessionToken(cookie_value),
            session=session,
        )

    return create_anonymous_session(
        db,
        request.headers.get("user-agent"),
        now,
        settings=settings,
    )


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


def _render_session_not_found() -> Response:
    response = HTMLResponse(
        "<!doctype html>"
        '<html lang="uz">'
        "<head>"
        '<meta charset="utf-8">'
        "<title>Topilmadi</title>"
        "</head>"
        "<body>"
        "<main>"
        "<h1>Sessiya topilmadi</h1>"
        "<p>Sessiya topilmadi yoki bekor qilish mumkin emas.</p>"
        "</main>"
        "</body>"
        "</html>",
        status_code=status.HTTP_404_NOT_FOUND,
    )
    return mark_auth_response_no_store(response)


def _get_session_view_model(
    summary: UserSessionSummary,
    current_session_id: UUID | None,
) -> dict[str, str | bool | None]:
    return {
        "session_id": str(summary.session_id),
        "browser_label": summary.browser_label,
        "device_label": summary.device_label,
        "user_agent": summary.user_agent,
        "last_seen_at": _format_utc_datetime(summary.last_seen_at),
        "expires_at": _format_utc_datetime(summary.expires_at),
        "status_label": _get_session_status_label(summary.status),
        "is_current": summary.session_id == current_session_id,
        "can_revoke": _can_revoke_session(summary, current_session_id),
    }


def _can_revoke_session(
    summary: UserSessionSummary,
    current_session_id: UUID | None,
) -> bool:
    return (
        summary.status == UserSessionStatus.ACTIVE
        and summary.session_id != current_session_id
    )


def _get_session_status_label(status_value: UserSessionStatus) -> str:
    return {
        UserSessionStatus.ACTIVE: "Faol",
        UserSessionStatus.EXPIRED: "Muddati tugagan",
        UserSessionStatus.REVOKED: "Bekor qilingan",
    }[status_value]


def _format_utc_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _render_login_failure(
    request: Request,
    context: CurrentSessionContext,
    message: str,
    status_code: int,
    error_code: ErrorCode,
) -> Response:
    response = templates.TemplateResponse(
        request,
        "auth/login.html",
        with_csrf_context(
            {"error_message": message},
            context.get_session_row(),
        ),
        status_code=status_code,
    )
    response.headers["X-Error-Code"] = error_code.value
    return mark_auth_response_no_store(response)


def _is_login_input_valid(phone: str, password: str) -> bool:
    if not password:
        return False
    try:
        normalize_uzbekistan_phone(phone)
    except PhoneNormalizationError:
        return False
    return True


def _get_safe_redirect_target(next_url: str | None) -> str:
    if not next_url:
        return ACCOUNT_PATH

    parsed = urlsplit(next_url)
    if parsed.scheme or parsed.netloc:
        return ACCOUNT_PATH
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return ACCOUNT_PATH
    return next_url
