import hmac
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from html import escape
from typing import Annotated, Final
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.csrf import verify_csrf_token
from app.auth.error_codes import (
    ErrorCode,
    get_error_http_status,
    get_public_error_body,
)
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import (
    RawSessionToken,
    hash_session_token,
    resolve_by_raw_token,
    touch_session,
)
from app.settings import Settings

LOGIN_PATH: Final = "/auth/login"
CSRF_FORM_FIELD_NAME: Final = "csrf_token"
CSRF_HEADER_NAME: Final = "X-CSRF-Token"
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
FORM_CONTENT_TYPES: Final = frozenset(
    {"application/x-www-form-urlencoded", "multipart/form-data"}
)


class CurrentSessionStatus(StrEnum):
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"
    INVALID = "invalid"
    REVOKED = "revoked"
    EXPIRED = "expired"
    INACTIVE_USER = "inactive_user"


@dataclass(frozen=True, repr=False)
class CurrentSessionContext:
    status: CurrentSessionStatus
    session_id: UUID | None = None
    user_id: UUID | None = None
    _session: AuthSession | None = field(default=None, repr=False, compare=False)
    _user: User | None = field(default=None, repr=False, compare=False)

    @property
    def is_authenticated(self) -> bool:
        return self.status == CurrentSessionStatus.AUTHENTICATED

    def get_authenticated_user(self) -> User | None:
        return self._user

    def get_session_row(self) -> AuthSession | None:
        return self._session

    def __repr__(self) -> str:
        return (
            "CurrentSessionContext("
            f"status={self.status!s}, session_id={self.session_id}, "
            f"user_id={self.user_id}"
            ")"
        )


class LoginRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Login required",
            headers={"Location": LOGIN_PATH},
        )


class CsrfFailed(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=get_error_http_status(ErrorCode.CSRF_FAILED),
            detail=get_public_error_body(
                ErrorCode.CSRF_FAILED,
                internal_detail="csrf validation failed",
            ),
            headers={"X-Error-Code": ErrorCode.CSRF_FAILED.value},
        )


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database_session(
    request: Request,
) -> Generator[DatabaseSession, None, None]:
    yield from request.app.state.get_database_session()


def get_current_time() -> datetime:
    return datetime.now(UTC)


def get_current_session_context(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> CurrentSessionContext:
    cookie_value = request.cookies.get(settings.session_cookie_name)
    current_time = _as_utc(now)
    if cookie_value is None:
        return CurrentSessionContext(status=CurrentSessionStatus.ANONYMOUS)

    try:
        raw_token = RawSessionToken(cookie_value)
    except ValueError:
        return CurrentSessionContext(status=CurrentSessionStatus.INVALID)

    resolved = resolve_by_raw_token(db, raw_token, current_time)
    if resolved is None:
        return _get_unresolved_session_context(db, raw_token, current_time)
    if resolved.authenticated_user is None:
        return CurrentSessionContext(
            status=CurrentSessionStatus.ANONYMOUS,
            session_id=resolved.session.id,
            _session=resolved.session,
        )

    touch_session(db, resolved.session, current_time, settings=settings)
    return CurrentSessionContext(
        status=CurrentSessionStatus.AUTHENTICATED,
        session_id=resolved.session.id,
        user_id=resolved.authenticated_user.id,
        _session=resolved.session,
        _user=resolved.authenticated_user,
    )


def require_user(
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> User:
    user = context.get_authenticated_user()
    if user is None:
        raise LoginRequired()
    return user


async def validate_csrf(
    request: Request,
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
) -> None:
    if request.method in SAFE_METHODS:
        return

    session = context.get_session_row()
    if session is None:
        raise_csrf_failed()

    submitted_token = await _get_submitted_csrf_token(request)
    if not verify_csrf_token(session, submitted_token, now):
        raise_csrf_failed()


def _get_unresolved_session_context(
    db: DatabaseSession,
    raw_token: RawSessionToken,
    now: datetime,
) -> CurrentSessionContext:
    statement = select(AuthSession).where(
        AuthSession.token_hash == hash_session_token(raw_token)
    )
    session = db.scalar(statement)
    if session is None:
        return CurrentSessionContext(status=CurrentSessionStatus.INVALID)
    if session.revoked_at is not None:
        return _failed_context(CurrentSessionStatus.REVOKED, session)
    if _as_utc(session.expires_at) <= now:
        return _failed_context(CurrentSessionStatus.EXPIRED, session)
    if session.user_id is not None:
        user = db.get(User, session.user_id)
        if user is None or not user.is_active:
            return _failed_context(CurrentSessionStatus.INACTIVE_USER, session)
    return CurrentSessionContext(status=CurrentSessionStatus.INVALID)


def raise_csrf_failed() -> None:
    raise CsrfFailed()


async def csrf_failed_exception_handler(
    request: Request,
    exc: CsrfFailed,
) -> HTMLResponse | JSONResponse:
    public_body = get_public_error_body(
        ErrorCode.CSRF_FAILED,
        internal_detail=str(exc.detail),
    )
    headers = {"X-Error-Code": ErrorCode.CSRF_FAILED.value}
    if _is_htmx_request(request):
        return HTMLResponse(
            content=_render_csrf_fragment(public_body),
            status_code=exc.status_code,
            headers=headers,
        )
    if _accepts_html(request):
        return HTMLResponse(
            content=_render_csrf_page(public_body),
            status_code=exc.status_code,
            headers=headers,
        )
    return JSONResponse(
        content={"detail": public_body},
        status_code=exc.status_code,
        headers=headers,
    )


async def _get_submitted_csrf_token(request: Request) -> str | None:
    header_token = request.headers.get(CSRF_HEADER_NAME)
    form_token = await _get_form_csrf_token(request)

    if header_token is not None and form_token is not None:
        if not hmac.compare_digest(header_token, form_token):
            return None
        return header_token
    return header_token if header_token is not None else form_token


async def _get_form_csrf_token(request: Request) -> str | None:
    content_type = _get_request_content_type(request)
    if content_type not in FORM_CONTENT_TYPES:
        return None

    cached_form = getattr(request, "_form", None)
    if cached_form is not None:
        token = cached_form.get(CSRF_FORM_FIELD_NAME)
        return token if isinstance(token, str) else None

    if content_type == "application/x-www-form-urlencoded":
        return await _get_urlencoded_form_csrf_token(request)

    form = await request.form()
    token = form.get(CSRF_FORM_FIELD_NAME)
    return token if isinstance(token, str) else None


async def _get_urlencoded_form_csrf_token(request: Request) -> str | None:
    body = await request.body()
    try:
        decoded_body = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    submitted_tokens = parse_qs(
        decoded_body,
        keep_blank_values=True,
    ).get(CSRF_FORM_FIELD_NAME)
    if not submitted_tokens:
        return None
    return submitted_tokens[0]


def _get_request_content_type(request: Request) -> str:
    content_type = request.headers.get("content-type", "")
    return content_type.split(";", 1)[0].strip().casefold()


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").casefold() == "true"


def _accepts_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").casefold()


def _render_csrf_fragment(public_body: dict[str, str]) -> str:
    code = escape(public_body["code"])
    message = escape(public_body["message"])
    return f'<div role="alert" data-error-code="{code}">{message}</div>'


def _render_csrf_page(public_body: dict[str, str]) -> str:
    code = escape(public_body["code"])
    message = escape(public_body["message"])
    return (
        "<!doctype html>"
        '<html lang="uz">'
        "<head>"
        '<meta charset="utf-8">'
        "<title>Xatolik</title>"
        "</head>"
        "<body>"
        f'<main role="alert" data-error-code="{code}">'
        "<h1>So'rov bajarilmadi</h1>"
        f"<p>{message}</p>"
        "</main>"
        "</body>"
        "</html>"
    )


def _failed_context(
    context_status: CurrentSessionStatus,
    session: AuthSession,
) -> CurrentSessionContext:
    return CurrentSessionContext(
        status=context_status,
        session_id=session.id,
        user_id=session.user_id,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Session timestamps must be timezone-aware")
    return value.astimezone(UTC)
