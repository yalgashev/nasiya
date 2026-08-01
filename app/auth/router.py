from base64 import b64encode
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request, status
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
from app.auth.error_codes import ErrorCode, get_error_http_status, get_public_error_body
from app.auth.login_rate_limit import LoginRateLimitPolicy
from app.auth.phone import (
    PhoneNormalizationError,
    mask_phone_for_display,
    normalize_uzbekistan_phone,
)
from app.auth.redirects import get_safe_redirect_target
from app.auth.service import authenticate, check_current_password
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
from app.auth.telegram_reauth import TelegramReauthRateLimitPolicy
from app.auth.template_context import with_csrf_context
from app.customer_identity.web_presentation import (
    CUSTOMER_IDENTITY_LOCALE_COOKIE_NAME,
    get_customer_identity_web_copy,
    resolve_customer_identity_web_language,
)
from app.offers.web_presentation import (
    OFFER_WEB_LOCALE_COOKIE_NAME,
    get_offer_web_copy,
    resolve_offer_web_language,
)
from app.otp.crypto import derive_browser_binding_digest
from app.otp.issuance import request_login_otp, request_new_login_code
from app.otp.session_login import rotate_session_after_otp_consume
from app.otp.verification import verify_login_otp
from app.otp.web_presentation import (
    OTP_LOCALE_COOKIE_NAME,
    OtpWebLanguage,
    get_otp_dispatch_locale,
    get_otp_web_copy,
    resolve_otp_web_language,
)
from app.request_client_ip import ClientIpResolutionError, resolve_client_ip
from app.security_headers import mark_auth_response_no_store
from app.settings import OtpHmacKeySettingsError, Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.qr import TelegramQrRenderError, render_telegram_start_link_qr_png
from app.telegram.service import (
    TelegramLinkLifecycleInternalError,
    TelegramLinkStatus,
    TelegramLinkTokenIssueError,
    TelegramLinkTokenIssueInternalError,
    get_link_status,
    issue_link_token,
    issue_relink_token,
)
from app.telegram.service import unlink as unlink_telegram
from app.telegram.token import build_telegram_start_link
from app.telegram.web_presentation import (
    TELEGRAM_ATTEMPT_POLL_INTERVAL_SECONDS,
    TelegramLinkAttemptPresentation,
    TelegramWebLanguage,
    get_link_attempt_presentation,
    get_telegram_web_copy,
    resolve_telegram_web_language,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
LOGIN_FAILED_MESSAGE = "Telefon raqam yoki parol noto'g'ri."
LOGIN_PATH = "/auth/login"
OTP_PATH = "/auth/otp"
TELEGRAM_PATH = "/auth/telegram"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
router = APIRouter(prefix="/auth")


@router.get("/otp", response_class=HTMLResponse, response_model=None)
def otp_request_page(
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
    next_url: Annotated[str | None, Query(alias="next")] = None,
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
    language = _otp_web_language(request)
    response = templates.TemplateResponse(
        request,
        "auth/otp_request.html",
        with_csrf_context(
            {
                "copy": get_otp_web_copy(language),
                "error_message": None,
                "next_url": _get_safe_next_or_none(next_url),
                "page_language": language.value,
                "password_login_url": LOGIN_PATH,
            },
            created_session.session,
        ),
    )
    set_session_cookie(response, created_session.raw_token, settings)
    return mark_auth_response_no_store(response)


@router.post("/otp/request", response_model=None)
def request_login_otp_route(
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
    next_url: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    _ = _csrf
    if context.is_authenticated:
        response = RedirectResponse(
            "/auth/account",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        return mark_auth_response_no_store(response)

    current_session = context.get_session_row()
    if current_session is None:
        return _redirect_otp_verify(next_url)

    try:
        client_ip = resolve_client_ip(request, settings)
        otp_hmac_key = settings.require_otp_hmac_key()
    except (ClientIpResolutionError, OtpHmacKeySettingsError):
        return _redirect_otp_verify(next_url)

    language = _otp_web_language(request)
    browser_binding_digest = derive_browser_binding_digest(
        otp_hmac_key=otp_hmac_key,
        session_id=current_session.id,
        csrf_secret=current_session.csrf_secret,
    )
    request_login_otp(
        db,
        settings,
        phone_input=phone,
        browser_binding_digest=browser_binding_digest,
        client_ip=client_ip,
        locale=get_otp_dispatch_locale(language),
        now=now,
    )
    return _redirect_otp_verify(next_url)


@router.get("/otp/verify", response_class=HTMLResponse, response_model=None)
def otp_verify_page(
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
    next_url: Annotated[str | None, Query(alias="next")] = None,
    error: Annotated[str | None, Query()] = None,
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
    language = _otp_web_language(request)
    copy = get_otp_web_copy(language)
    response = templates.TemplateResponse(
        request,
        "auth/otp_verify.html",
        with_csrf_context(
            {
                "copy": copy,
                "error_message": copy["invalid_code_message"]
                if error == "invalid"
                else None,
                "next_url": _get_safe_next_or_none(next_url),
                "page_language": language.value,
                "password_login_url": LOGIN_PATH,
            },
            created_session.session,
        ),
    )
    set_session_cookie(response, created_session.raw_token, settings)
    return mark_auth_response_no_store(response)


@router.post("/otp/new-code", response_model=None)
def request_new_login_otp_route(
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
    next_url: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    _ = _csrf
    if context.is_authenticated:
        response = RedirectResponse(
            "/auth/account",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        return mark_auth_response_no_store(response)

    current_session = context.get_session_row()
    if current_session is None:
        return _redirect_otp_verify(next_url)

    try:
        client_ip = resolve_client_ip(request, settings)
        otp_hmac_key = settings.require_otp_hmac_key()
    except (ClientIpResolutionError, OtpHmacKeySettingsError):
        return _redirect_otp_verify(next_url)

    language = _otp_web_language(request)
    browser_binding_digest = derive_browser_binding_digest(
        otp_hmac_key=otp_hmac_key,
        session_id=current_session.id,
        csrf_secret=current_session.csrf_secret,
    )
    request_new_login_code(
        db,
        settings,
        browser_binding_digest=browser_binding_digest,
        client_ip=client_ip,
        locale=get_otp_dispatch_locale(language),
        now=now,
    )
    return _redirect_otp_verify(next_url)


@router.post("/otp/verify", response_model=None)
def verify_login_otp_route(
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
    code: Annotated[str, Form()] = "",
    next_url: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    _ = _csrf
    if context.is_authenticated:
        response = RedirectResponse(
            "/auth/account",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        return mark_auth_response_no_store(response)

    current_session = context.get_session_row()
    if current_session is None:
        return _redirect_otp_verify(next_url, error="invalid")

    try:
        otp_hmac_key = settings.require_otp_hmac_key()
    except OtpHmacKeySettingsError:
        return _redirect_otp_verify(next_url, error="invalid")

    browser_binding_digest = derive_browser_binding_digest(
        otp_hmac_key=otp_hmac_key,
        session_id=current_session.id,
        csrf_secret=current_session.csrf_secret,
    )
    verification_result = verify_login_otp(
        db,
        settings,
        browser_binding_digest=browser_binding_digest,
        candidate_code_input=code,
        now=now,
    )
    created_session = rotate_session_after_otp_consume(
        db,
        verification_result=verification_result,
        current_session=current_session,
        user_agent=request.headers.get("user-agent"),
        now=now,
        settings=settings,
    )
    if created_session is None:
        return _redirect_otp_verify(next_url, error="invalid")

    response = RedirectResponse(
        get_safe_redirect_target(next_url),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    set_session_cookie(response, created_session.raw_token, settings)
    return mark_auth_response_no_store(response)


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
    try:
        client_ip = resolve_client_ip(request, settings)
    except ClientIpResolutionError:
        return _render_login_failure(
            request=request,
            context=context,
            message=LOGIN_FAILED_MESSAGE,
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    rate_limit_policy = LoginRateLimitPolicy(db=db, settings=settings)
    if not _is_login_input_valid(phone, password):
        validation_rate_limit_result = rate_limit_policy.record_failure(
            phone,
            client_ip,
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

    rate_limit_result = rate_limit_policy.check(phone, client_ip, now)
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
            client_ip,
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
        get_safe_redirect_target(next_url),
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
    offer_language = resolve_offer_web_language(
        request.cookies.get(OFFER_WEB_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )
    identity_language = resolve_customer_identity_web_language(
        request.cookies.get(CUSTOMER_IDENTITY_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )
    identity_copy = get_customer_identity_web_copy(identity_language)
    response = templates.TemplateResponse(
        request,
        "auth/account.html",
        with_csrf_context(
            {
                "masked_phone": mask_phone_for_display(user.phone),
                "registration_offer_link": get_offer_web_copy(
                    offer_language
                ).account_registration_offer_link,
                "registration_offer_link_language": offer_language.value,
                "customer_identity_link": identity_copy.page_title,
                "customer_identity_link_language": identity_language.value,
            },
            session,
        ),
    )
    return mark_auth_response_no_store(response)


@router.get("/telegram", response_class=HTMLResponse, response_model=None)
def telegram_page(
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

    response = _render_telegram_page(
        request=request,
        db=db,
        user=user,
        settings=settings,
        context=context,
        error_key=request.query_params.get("error"),
        notice_key=request.query_params.get("notice"),
    )
    return mark_auth_response_no_store(response)


@router.get("/telegram/status", response_class=HTMLResponse, response_model=None)
def telegram_status(
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

    link_status = get_link_status(db, user)
    language = _telegram_web_language(request)
    response = HTMLResponse(
        _render_telegram_status_fragment(link_status, language=language)
    )
    return mark_auth_response_no_store(response)


@router.get(
    "/telegram/attempts/{attempt_id}/status",
    response_class=HTMLResponse,
    response_model=None,
)
def telegram_attempt_status(
    request: Request,
    attempt_id: str,
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

    try:
        parsed_attempt_id = UUID(attempt_id)
    except ValueError:
        parsed_attempt_id = None
    presentation = (
        get_link_attempt_presentation(db, user, parsed_attempt_id, now)
        if parsed_attempt_id is not None
        else TelegramLinkAttemptPresentation.UNAVAILABLE
    )
    language = _telegram_web_language(request)
    response = HTMLResponse(
        _render_telegram_attempt_status_fragment(
            parsed_attempt_id,
            presentation,
            language=language,
        )
    )
    if presentation.is_terminal:
        response.headers["HX-Retarget"] = "#telegram-link-reveal"
        response.headers["HX-Reswap"] = "innerHTML"
    return mark_auth_response_no_store(response)


@router.post("/telegram/link-token", response_class=HTMLResponse, response_model=None)
def issue_telegram_link_token(
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
) -> Response:
    _ = _csrf
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    if not _is_htmx_request(request):
        return _redirect_telegram_javascript_required()

    try:
        client_ip = resolve_client_ip(request, settings)
    except ClientIpResolutionError:
        return _render_telegram_public_error(
            request,
            ErrorCode.VALIDATION_ERROR,
            error_key="client_ip",
        )
    if settings.telegram_bot_username is None:
        return _render_telegram_public_error(
            request,
            ErrorCode.VALIDATION_ERROR,
            error_key="bot_unavailable",
        )

    try:
        issued = issue_link_token(db, settings, user, client_ip, now)
    except TelegramLinkTokenIssueError as exc:
        return _render_telegram_issue_error(request, exc)
    except TelegramLinkTokenIssueInternalError:
        raise

    start_link = build_telegram_start_link(
        settings.telegram_bot_username,
        issued.raw_token,
    )
    return _render_telegram_reveal_response(
        request,
        start_link.as_delivery_url(),
        attempt_id=issued.token.id,
        action="link",
    )


@router.post("/telegram/relink-token", response_class=HTMLResponse, response_model=None)
def issue_telegram_relink_token(
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
    current_password: Annotated[str | None, Form()] = None,
) -> Response:
    _ = _csrf
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    if not _is_htmx_request(request):
        return _redirect_telegram_javascript_required()

    reauth_result = _verify_telegram_account_control_password(
        request=request,
        db=db,
        settings=settings,
        user=user,
        raw_password=current_password,
        now=now,
    )
    if isinstance(reauth_result, Response):
        return reauth_result
    client_ip = reauth_result
    if settings.telegram_bot_username is None:
        return _render_telegram_public_error(
            request,
            ErrorCode.VALIDATION_ERROR,
            error_key="bot_unavailable",
        )

    try:
        issued = issue_relink_token(db, settings, user, client_ip, now)
    except TelegramLinkTokenIssueError as exc:
        return _render_telegram_issue_error(request, exc)
    except TelegramLinkTokenIssueInternalError:
        raise

    start_link = build_telegram_start_link(
        settings.telegram_bot_username,
        issued.raw_token,
    )
    return _render_telegram_reveal_response(
        request,
        start_link.as_delivery_url(),
        attempt_id=issued.token.id,
        action="relink",
    )


@router.post("/telegram/unlink", response_model=None)
def unlink_telegram_account(
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
    current_password: Annotated[str | None, Form()] = None,
) -> Response:
    _ = _csrf
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    reauth_result = _verify_telegram_account_control_password(
        request=request,
        db=db,
        settings=settings,
        user=user,
        raw_password=current_password,
        now=now,
    )
    if isinstance(reauth_result, Response):
        return reauth_result

    try:
        unlink_telegram(db, user, now)
    except TelegramLinkTokenIssueError as exc:
        if exc.error_code is ErrorCode.TELEGRAM_NOT_LINKED:
            response = RedirectResponse(
                f"{TELEGRAM_PATH}?notice=already_unlinked",
                status_code=status.HTTP_303_SEE_OTHER,
            )
            return mark_auth_response_no_store(response)
        response = RedirectResponse(
            f"{TELEGRAM_PATH}?error={_telegram_error_key(exc.error_code)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.headers["X-Error-Code"] = exc.error_code.value
        return mark_auth_response_no_store(response)
    except TelegramLinkLifecycleInternalError:
        raise

    response = RedirectResponse(
        f"{TELEGRAM_PATH}?notice=unlinked",
        status_code=status.HTTP_303_SEE_OTHER,
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


def _render_telegram_page(
    *,
    request: Request,
    db: DatabaseSession,
    user,
    settings: Settings,
    context: CurrentSessionContext,
    error_key: str | None = None,
    notice_key: str | None = None,
) -> Response:
    link_status = get_link_status(db, user)
    language = _telegram_web_language(request)
    copy = get_telegram_web_copy(language)
    notice_message = _telegram_notice_message(
        notice_key,
        language=language,
    )
    if link_status is TelegramLinkStatus.LINKED and notice_key in {
        "unlinked",
        "already_unlinked",
    }:
        notice_message = None
    return templates.TemplateResponse(
        request,
        "auth/telegram.html",
        with_csrf_context(
            {
                "bot_configured": settings.telegram_bot_username is not None,
                "copy": copy,
                "error_message": _telegram_error_message(
                    error_key,
                    language=language,
                ),
                "is_linked": link_status is TelegramLinkStatus.LINKED,
                "notice_message": notice_message,
                "page_language": language.value,
                "password_max_length": settings.password_max_length,
                "status_label": _telegram_status_label(
                    link_status,
                    language=language,
                ),
            },
            context.get_session_row(),
        ),
    )


def _render_telegram_status_fragment(
    link_status: TelegramLinkStatus,
    *,
    language: TelegramWebLanguage,
) -> str:
    status_value = escape(link_status.value)
    copy = get_telegram_web_copy(language)
    status_label = escape(_telegram_status_label(link_status, language=language))
    return (
        f'<div role="status" aria-live="polite" '
        f'data-telegram-link-status="{status_value}">'
        f"{escape(copy['status_heading'])}: {status_label}"
        "</div>"
    )


def _render_telegram_reveal_response(
    request: Request,
    start_link_url: str,
    *,
    attempt_id: UUID,
    action: str,
) -> Response:
    language = _telegram_web_language(request)
    qr_data_uri = _render_telegram_qr_data_uri(start_link_url)
    fragment = _render_telegram_reveal_fragment(
        start_link_url,
        attempt_id=attempt_id,
        action=action,
        language=language,
        qr_data_uri=qr_data_uri,
    )
    response = HTMLResponse(fragment)
    response.headers["X-Telegram-Link-Reveal"] = "one-time"
    response.headers["HX-Push-Url"] = "false"
    return mark_auth_response_no_store(response)


def _render_telegram_reveal_fragment(
    start_link_url: str,
    *,
    attempt_id: UUID,
    action: str,
    language: TelegramWebLanguage,
    qr_data_uri: str | None,
) -> str:
    copy = get_telegram_web_copy(language)
    escaped_url = escape(start_link_url, quote=True)
    escaped_attempt_id = escape(str(attempt_id), quote=True)
    status_url = f"{TELEGRAM_PATH}/attempts/{escaped_attempt_id}/status"
    hint_key = "relink_hint" if action == "relink" else "link_hint"
    qr_fragment = ""
    if qr_data_uri is not None:
        qr_fragment = (
            '<figure class="telegram-qr">'
            f'<img src="{escape(qr_data_uri, quote=True)}" '
            f'alt="{escape(copy["qr_alt"], quote=True)}" '
            'width="240" height="240">'
            f"<figcaption>{escape(copy['qr_help'])}</figcaption>"
            "</figure>"
        )
    return (
        '<section aria-labelledby="telegram-reveal-heading" '
        'data-telegram-link-reveal="one-time" hx-history="false">'
        f'<h2 id="telegram-reveal-heading">{escape(copy["reveal_heading"])}</h2>'
        '<p><a class="telegram-open-link" href="/auth/telegram" '
        f'data-telegram-external-link="{escaped_url}" '
        'rel="noopener noreferrer">'
        f"{escape(copy['open_telegram'])}"
        "</a></p>"
        f"{qr_fragment}"
        f"<p>{escape(copy[hint_key])}</p>"
        f'<div id="telegram-attempt-status-{escaped_attempt_id}" '
        'role="status" aria-live="polite" '
        f'data-attempt-id="{escaped_attempt_id}" '
        'data-telegram-attempt-status="WAITING" '
        f'hx-get="{status_url}" '
        f'hx-trigger="load delay:{TELEGRAM_ATTEMPT_POLL_INTERVAL_SECONDS}s" '
        'hx-swap="outerHTML">'
        f"{escape(copy['waiting'])}"
        f'<span class="telegram-loading" aria-hidden="true">'
        f"{escape(copy['loading'])}</span>"
        "</div>"
        "</section>"
    )


def _render_telegram_qr_data_uri(start_link_url: str) -> str | None:
    try:
        png_bytes = render_telegram_start_link_qr_png(start_link_url)
    except TelegramQrRenderError:
        return None
    encoded_png = b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded_png}"


def _verify_telegram_account_control_password(
    *,
    request: Request,
    db: DatabaseSession,
    settings: Settings,
    user,
    raw_password: str | None,
    now: datetime,
) -> ResolvedClientIp | Response:
    try:
        client_ip = resolve_client_ip(request, settings)
    except ClientIpResolutionError:
        return _render_telegram_public_error(
            request,
            ErrorCode.VALIDATION_ERROR,
            error_key="client_ip",
        )

    rate_limit_policy = TelegramReauthRateLimitPolicy(db, settings)
    if not rate_limit_policy.check(user, client_ip, now).allowed:
        return _render_telegram_public_error(
            request,
            ErrorCode.RATE_LIMITED,
            error_key="reauth_rate_limit",
        )

    submitted_password = raw_password or ""
    if not check_current_password(
        db,
        user,
        submitted_password,
        settings,
    ):
        failure_result = rate_limit_policy.record_failure(
            user,
            client_ip,
            now,
        )
        if not failure_result.allowed:
            return _render_telegram_public_error(
                request,
                ErrorCode.RATE_LIMITED,
                error_key="reauth_rate_limit",
            )
        return _render_telegram_public_error(
            request,
            ErrorCode.FORBIDDEN,
            error_key="current_password",
        )

    rate_limit_policy.clear_user_failures_after_success(user)
    return client_ip


def _render_telegram_attempt_status_fragment(
    attempt_id: UUID | None,
    presentation: TelegramLinkAttemptPresentation,
    *,
    language: TelegramWebLanguage,
) -> str:
    copy = get_telegram_web_copy(language)
    status_copy_key = {
        TelegramLinkAttemptPresentation.WAITING: "waiting",
        TelegramLinkAttemptPresentation.LINKED: "attempt_linked",
        TelegramLinkAttemptPresentation.SUPERSEDED: "superseded",
        TelegramLinkAttemptPresentation.EXPIRED: "expired",
        TelegramLinkAttemptPresentation.UNAVAILABLE: "attempt_unavailable",
    }[presentation]
    escaped_status = escape(presentation.value, quote=True)
    attributes = (
        'role="status" aria-live="polite" '
        f'data-telegram-attempt-status="{escaped_status}"'
    )
    if presentation is TelegramLinkAttemptPresentation.WAITING:
        if attempt_id is None:
            raise ValueError("WAITING presentation requires an owned attempt UUID")
        escaped_attempt_id = escape(str(attempt_id), quote=True)
        status_url = f"{TELEGRAM_PATH}/attempts/{escaped_attempt_id}/status"
        attributes = (
            f'id="telegram-attempt-status-{escaped_attempt_id}" '
            f"{attributes} "
            f'data-attempt-id="{escaped_attempt_id}"'
            f' hx-get="{status_url}"'
            f' hx-trigger="every {TELEGRAM_ATTEMPT_POLL_INTERVAL_SECONDS}s"'
            ' hx-swap="outerHTML"'
        )
    fragment = f"<div {attributes}>{escape(copy[status_copy_key])}</div>"
    if presentation is TelegramLinkAttemptPresentation.LINKED:
        fragment += (
            '<p id="telegram-account-status" class="telegram-account-status" '
            'role="status" hx-swap-oob="outerHTML">'
            f"{escape(copy['linked'])}"
            "</p>"
            '<p id="telegram-notice" hx-swap-oob="delete"></p>'
        )
    return fragment


def _render_telegram_issue_error(
    request: Request,
    exc: TelegramLinkTokenIssueError,
) -> Response:
    return _render_telegram_public_error(
        request,
        exc.error_code,
        error_key=_telegram_error_key(exc.error_code),
    )


def _render_telegram_public_error(
    request: Request,
    error_code: ErrorCode,
    *,
    error_key: str,
) -> Response:
    if _is_htmx_request(request):
        language = _telegram_web_language(request)
        message = _telegram_error_message(error_key, language=language)
        if message is None:
            public_error = get_public_error_body(error_code)
            message = public_error["message"]
        response = HTMLResponse(
            _render_telegram_error_fragment(message),
            status_code=get_error_http_status(error_code),
            headers={"X-Error-Code": error_code.value},
        )
        return mark_auth_response_no_store(response)

    response = RedirectResponse(
        f"{TELEGRAM_PATH}?error={error_key}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.headers["X-Error-Code"] = error_code.value
    return mark_auth_response_no_store(response)


def _render_telegram_error_fragment(message: str) -> str:
    return f'<div role="alert">{escape(message)}</div>'


def _telegram_status_label(
    link_status: TelegramLinkStatus,
    *,
    language: TelegramWebLanguage,
) -> str:
    copy = get_telegram_web_copy(language)
    return {
        TelegramLinkStatus.LINKED: copy["linked"],
        TelegramLinkStatus.UNLINKED: copy["unlinked"],
    }[link_status]


def _telegram_error_key(error_code: ErrorCode) -> str:
    return error_code.value.casefold()


def _telegram_error_message(
    error_key: str | None,
    *,
    language: TelegramWebLanguage,
) -> str | None:
    if error_key is None:
        return None
    copy = get_telegram_web_copy(language)
    if error_key == "bot_unavailable":
        return copy["bot_config_error"]
    if error_key == "client_ip":
        return copy["client_ip_error"]
    if error_key == "current_password":
        return copy["current_password_error"]
    if error_key == "reauth_rate_limit":
        return copy["rate_limited"]
    mapped_key = {
        ErrorCode.RATE_LIMITED.value.casefold(): "rate_limited",
        ErrorCode.TELEGRAM_ALREADY_LINKED.value.casefold(): "already_linked",
        ErrorCode.TELEGRAM_NOT_LINKED.value.casefold(): "not_linked",
    }.get(error_key)
    if mapped_key is not None:
        return copy[mapped_key]
    try:
        return get_public_error_body(ErrorCode(error_key.upper()))["message"]
    except ValueError:
        return copy["request_failed"]


def _telegram_notice_message(
    notice_key: str | None,
    *,
    language: TelegramWebLanguage,
) -> str | None:
    copy = get_telegram_web_copy(language)
    if notice_key == "unlinked":
        return copy["unlinked_notice"]
    if notice_key == "javascript_required":
        return copy["javascript_required"]
    if notice_key == "already_unlinked":
        return copy["already_unlinked_notice"]
    return None


def _telegram_web_language(request: Request) -> TelegramWebLanguage:
    return resolve_telegram_web_language(request.headers.get("accept-language"))


def _otp_web_language(request: Request) -> OtpWebLanguage:
    return resolve_otp_web_language(
        request.cookies.get(OTP_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )


def _get_safe_next_or_none(next_url: str | None) -> str | None:
    if next_url is None:
        return None
    safe_target = get_safe_redirect_target(next_url)
    if safe_target != next_url:
        return None
    return safe_target


def _redirect_otp_verify(next_url: str | None, *, error: str | None = None) -> Response:
    response = RedirectResponse(
        _otp_verify_location(next_url, error=error),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    return mark_auth_response_no_store(response)


def _otp_verify_location(next_url: str | None, *, error: str | None = None) -> str:
    query_params = {}
    safe_next = _get_safe_next_or_none(next_url)
    if safe_next is not None:
        query_params["next"] = safe_next
    if error is not None:
        query_params["error"] = error
    if not query_params:
        return f"{OTP_PATH}/verify"
    return f"{OTP_PATH}/verify?{urlencode(query_params)}"


def _redirect_telegram_javascript_required() -> Response:
    response = RedirectResponse(
        f"{TELEGRAM_PATH}?notice=javascript_required",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    return mark_auth_response_no_store(response)


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").casefold() == "true"


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
    return get_safe_redirect_target(next_url)
