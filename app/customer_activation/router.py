from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DatabaseSession
from starlette.exceptions import HTTPException

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
from app.auth.template_context import with_csrf_context
from app.customer_activation.contracts import (
    CustomerAlreadyActive,
    PreparedCustomerActivation,
    RegistrationOtpCooldown,
    RegistrationOtpPendingDelivery,
    RegistrationOtpPrerequisiteFailed,
    RegistrationOtpRateLimited,
    RegistrationOtpRequestResult,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    VerifyRegistrationOtp,
    mark_customer_activation_committed,
)
from app.customer_activation.presentation import (
    CUSTOMER_ACTIVATION_LOCALE_COOKIE_NAME,
    get_customer_activation_copy,
    get_customer_activation_error_message,
    present_customer_activation_readiness,
    resolve_customer_activation_language,
)
from app.customer_activation.service import (
    AuthenticatedActivationContext,
    derive_authenticated_activation_context,
    get_registration_readiness,
    verify_and_activate_registration_customer,
)
from app.customer_activation.service import (
    request_new_registration_otp as request_new_registration_otp_service,
)
from app.customer_activation.service import (
    request_registration_otp as request_registration_otp_service,
)
from app.customer_identity.crypto import CustomerIdentityCryptoConfigurationError
from app.otp.web_presentation import OtpWebLanguage
from app.request_client_ip import ClientIpResolutionError, resolve_client_ip
from app.security_headers import mark_auth_response_no_store
from app.settings import Settings

router = APIRouter(prefix="/customer/activation")
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

ACTIVATION_PATH = "/customer/activation"
LOGIN_PATH = "/auth/login"
_SAFE_QUERY_ERROR_CODES = frozenset(
    {
        ErrorCode.CSRF_FAILED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.TELEGRAM_NOT_LINKED,
        ErrorCode.OFFER_UNAVAILABLE,
        ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
        ErrorCode.CUSTOMER_DRAFT_REQUIRED,
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
        ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
    }
)
_SAFE_NOTICES = frozenset({"otp-pending"})


def get_activation_current_session_context(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> CurrentSessionContext:
    context = get_current_session_context(request, db, settings, now)
    db.flush()
    for row in (context.get_session_row(), context.get_authenticated_user()):
        if row is not None and row in db:
            db.expunge(row)
    db.commit()
    return context


async def validate_activation_csrf(
    request: Request,
    context: Annotated[
        CurrentSessionContext,
        Depends(get_activation_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
) -> None:
    await validate_csrf(request, context, now)


@router.get("", response_class=HTMLResponse, response_model=None)
def activation_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_activation_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
) -> Response:
    if _authenticated_user_or_none(context) is None:
        return _redirect_auth_login(context, settings)
    try:
        activation_context = _derive_activation_context(
            request=request,
            settings=settings,
            context=context,
            now=now,
        )
        if activation_context is None:
            return _redirect_auth_login(context, settings)
        readiness = get_registration_readiness(
            db,
            context=activation_context,
            identity_crypto_config=settings.require_customer_identity_crypto_config(),
        )
    except (
        ClientIpResolutionError,
        CustomerIdentityCryptoConfigurationError,
        RuntimeError,
    ):
        return mark_auth_response_no_store(
            Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        )

    language = _activation_language(request)
    presentation = present_customer_activation_readiness(readiness)
    template_context: dict[str, object] = {
        "page_language": language.value,
        "copy": get_customer_activation_copy(language),
        "readiness": presentation,
        "error_code": None,
        "error_message": None,
        "notice_message": None,
    }
    error_code = _query_error_code(request)
    if error_code is not None:
        template_context["error_code"] = error_code.value
        template_context["error_message"] = get_customer_activation_error_message(
            language,
            error_code,
        )
    if _query_notice(request) == "otp-pending":
        template_context["notice_message"] = get_customer_activation_copy(
            language
        ).delivery_pending_notice
    if presentation.ready_for_otp:
        template_context = with_csrf_context(
            template_context,
            context.get_session_row(),
        )
    response = templates.TemplateResponse(
        request,
        "customer/activation.html",
        template_context,
        status_code=status.HTTP_200_OK,
    )
    return mark_auth_response_no_store(response)


@router.post("/otp/request", response_model=None)
def request_registration_otp(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_activation_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_activation_csrf)],
) -> Response:
    _ = _csrf
    if _authenticated_user_or_none(context) is None:
        return _redirect_auth_login(context, settings)
    try:
        activation_context = _derive_activation_context(
            request=request,
            settings=settings,
            context=context,
            now=now,
        )
        if activation_context is None:
            return _redirect_auth_login(context, settings)
        result = request_registration_otp_service(
            request.app.state.database_session_factory,
            context=activation_context,
            settings=settings,
            identity_crypto_config=settings.require_customer_identity_crypto_config(),
            language=_activation_language(request),
            now=now,
        )
    except (
        ClientIpResolutionError,
        CustomerIdentityCryptoConfigurationError,
        RuntimeError,
    ):
        return _activation_redirect(error_code=ErrorCode.CUSTOMER_ACTIVATION_CHANGED)
    return _registration_request_redirect(result)


@router.post("/otp/verify", response_model=None)
async def verify_registration_otp(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_activation_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_activation_csrf)],
) -> Response:
    _ = _csrf
    if _authenticated_user_or_none(context) is None:
        return _redirect_auth_login(context, settings)
    try:
        activation_context = _derive_activation_context(
            request=request,
            settings=settings,
            context=context,
            now=now,
        )
        if activation_context is None:
            return _redirect_auth_login(context, settings)
        try:
            form = await request.form(max_fields=2, max_part_size=1024)
        except HTTPException:
            return _activation_redirect(error_code=ErrorCode.OTP_INVALID)
        field_names = tuple(name for name, _value in form.multi_items())
        if set(field_names) != {"csrf_token", "code"} or len(field_names) != 2:
            return _activation_redirect(error_code=ErrorCode.OTP_INVALID)
        candidate_code = form.get("code")
        if not isinstance(candidate_code, str):
            return _activation_redirect(error_code=ErrorCode.OTP_INVALID)
        result = verify_and_activate_registration_customer(
            db,
            command=VerifyRegistrationOtp(
                actor=activation_context.actor,
                browser=activation_context.browser,
                candidate_code=candidate_code,
                now=now,
            ),
            settings=settings,
            identity_crypto_config=settings.require_customer_identity_crypto_config(),
        )
    except (
        ClientIpResolutionError,
        CustomerIdentityCryptoConfigurationError,
        RuntimeError,
    ):
        raise
    return _registration_verify_response(
        result,
        context=context,
        settings=settings,
    )


@router.post("/otp/new-code", response_model=None)
def request_new_registration_otp_code(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_activation_current_session_context),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_activation_csrf)],
) -> Response:
    _ = _csrf
    if _authenticated_user_or_none(context) is None:
        return _redirect_auth_login(context, settings)
    try:
        activation_context = _derive_activation_context(
            request=request,
            settings=settings,
            context=context,
            now=now,
        )
        if activation_context is None:
            return _redirect_auth_login(context, settings)
        result = request_new_registration_otp_service(
            request.app.state.database_session_factory,
            context=activation_context,
            settings=settings,
            identity_crypto_config=settings.require_customer_identity_crypto_config(),
            language=_activation_language(request),
            now=now,
        )
    except (
        ClientIpResolutionError,
        CustomerIdentityCryptoConfigurationError,
        RuntimeError,
    ):
        return _activation_redirect(error_code=ErrorCode.CUSTOMER_ACTIVATION_CHANGED)
    return _registration_request_redirect(result)


def _authenticated_user_or_none(context: CurrentSessionContext) -> object | None:
    try:
        return require_user(context)
    except LoginRequired:
        return None


def _activation_redirect(
    *,
    error_code: ErrorCode | None = None,
    notice: str | None = None,
) -> Response:
    query: dict[str, str] = {}
    if error_code is not None:
        query["error"] = error_code.value
    if notice is not None:
        query["notice"] = notice
    location = ACTIVATION_PATH
    if query:
        location = f"{location}?{urlencode(query)}"
    response = RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)
    if error_code is not None:
        response.headers["X-Error-Code"] = error_code.value
    return mark_auth_response_no_store(response)


def _derive_activation_context(
    *,
    request: Request,
    settings: Settings,
    context: CurrentSessionContext,
    now: datetime,
) -> AuthenticatedActivationContext | None:
    return derive_authenticated_activation_context(
        current_context=context,
        trusted_client_ip=resolve_client_ip(request, settings),
        otp_hmac_key=settings.require_otp_hmac_key(),
        now=now,
    )


def _registration_request_redirect(result: RegistrationOtpRequestResult) -> Response:
    if isinstance(result, RegistrationOtpPendingDelivery):
        return _activation_redirect(notice="otp-pending")
    if isinstance(result, CustomerAlreadyActive):
        return _activation_redirect()
    if isinstance(result, RegistrationOtpPrerequisiteFailed):
        return _activation_redirect(error_code=ErrorCode(result.error.value))
    if isinstance(result, (RegistrationOtpCooldown, RegistrationOtpRateLimited)):
        return _activation_redirect(error_code=ErrorCode.RATE_LIMITED)
    raise TypeError("Registration OTP request result is invalid")


def _registration_verify_response(
    result: PreparedCustomerActivation | RegistrationOtpVerificationResult,
    *,
    context: CurrentSessionContext,
    settings: Settings,
) -> Response:
    if isinstance(result, PreparedCustomerActivation):
        response = _activation_redirect()
        committed = mark_customer_activation_committed(result)
        set_session_cookie(response, committed.release_cookie_token(), settings)
        return response
    if not isinstance(result, RegistrationOtpVerificationResult):
        raise TypeError("Registration OTP verification result is invalid")
    if result.outcome is RegistrationOtpVerificationOutcome.ALREADY_ACTIVE:
        return _activation_redirect()
    if result.outcome is RegistrationOtpVerificationOutcome.OTP_INVALID:
        return _activation_redirect(error_code=ErrorCode.OTP_INVALID)
    if result.outcome is RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED:
        return _activation_redirect(error_code=ErrorCode.CUSTOMER_ACTIVATION_CHANGED)
    if result.outcome is RegistrationOtpVerificationOutcome.RATE_LIMITED:
        return _activation_redirect(error_code=ErrorCode.RATE_LIMITED)
    if result.outcome is RegistrationOtpVerificationOutcome.SESSION_EXPIRED:
        return _redirect_auth_login(context, settings)
    return _activation_redirect(error_code=ErrorCode.CUSTOMER_ACTIVATION_CHANGED)


def _activation_language(request: Request) -> OtpWebLanguage:
    return resolve_customer_activation_language(
        request.cookies.get(CUSTOMER_ACTIVATION_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )


def _query_error_code(request: Request) -> ErrorCode | None:
    raw_code = request.query_params.get("error")
    try:
        code = ErrorCode(raw_code) if raw_code is not None else None
    except ValueError:
        return None
    return code if code in _SAFE_QUERY_ERROR_CODES else None


def _query_notice(request: Request) -> str | None:
    notice = request.query_params.get("notice")
    return notice if notice in _SAFE_NOTICES else None


def _redirect_auth_login(
    context: CurrentSessionContext,
    settings: Settings,
) -> Response:
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
