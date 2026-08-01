from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DatabaseSession
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.cookies import delete_session_cookie
from app.auth.deps import (
    CsrfFailed,
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
from app.customer_document.contracts import (
    CustomerDocumentActor,
    CustomerDocumentSubmissionId,
    ExpectedCurrentCustomerDocument,
    UploadOwnCustomerDocument,
)
from app.customer_document.coordinator import (
    CustomerDocumentServiceError,
    upload_and_attach_own_customer_document,
)
from app.customer_document.dependencies import (
    CustomerDocumentStorageUnavailable,
    get_customer_document_storage_service,
)
from app.customer_document.service import create_own_current_customer_document_url
from app.customer_identity.contracts import SaveCustomerIdentity
from app.customer_identity.crypto import CustomerIdentityCryptoConfigurationError
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.request_security import (
    CustomerDocumentRequestSecurityError,
    resolve_customer_document_request_context,
)
from app.customer_identity.service import (
    CustomerIdentityServiceError,
    resolve_customer_identity_actor,
    save_own_customer_identity,
)
from app.customer_identity.web_presentation import (
    CUSTOMER_IDENTITY_LOCALE_COOKIE_NAME,
    CustomerIdentityWebLanguage,
    get_customer_identity_web_copy,
    get_customer_identity_web_message,
    resolve_customer_identity_web_language,
)
from app.customer_identity.web_service import (
    OwnCustomerIdentityPageState,
    get_own_customer_identity_page_state,
)
from app.request_client_ip import ClientIpResolutionError, resolve_client_ip
from app.security_headers import mark_auth_response_no_store
from app.settings import ObjectStorageSettingsError, Settings
from app.storage.contracts import StorageProviderError
from app.storage.errors import StorageAccessDeniedError, StorageUploadError
from app.storage.image import ImageSanitizationError
from app.storage.multipart import StorageMultipartError, bounded_multipart_upload

router = APIRouter(prefix="/customer/identity")
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
IDENTITY_PATH = "/customer/identity"
DOCUMENT_PATH = "/customer/identity/document"
LOGIN_PATH = "/auth/login"
_SAFE_QUERY_ERROR_CODES = frozenset(
    {
        ErrorCode.CSRF_FAILED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.FILE_ACCESS_DENIED,
        ErrorCode.FILE_STORAGE_ERROR,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.UNSUPPORTED_FILE_TYPE,
        ErrorCode.DUPLICATE_JSHSHIR,
        ErrorCode.CUSTOMER_DRAFT_REQUIRED,
        ErrorCode.CUSTOMER_IDENTITY_CHANGED,
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED,
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
    }
)
_SAFE_NOTICES = frozenset({"identity-saved", "document-uploaded"})


@router.get("", response_class=HTMLResponse, response_model=None)
def identity_page(
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

    language = _resolve_language(request)
    error_code = _query_error_code(request)
    page_state: OwnCustomerIdentityPageState | None = None
    try:
        crypto_config = settings.require_customer_identity_crypto_config()
        page_state = get_own_customer_identity_page_state(
            db,
            actor=resolve_customer_identity_actor(user),
            crypto_config=crypto_config,
        )
    except CustomerIdentityServiceError as exc:
        error_code = exc.code
    except (CustomerIdentityCryptoConfigurationError, RuntimeError):
        error_code = ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE

    return _render_identity_page(
        request,
        context=context,
        language=language,
        page_state=page_state,
        error_code=error_code,
        notice=_query_notice(request),
    )


@router.post("", response_model=None)
async def save_identity(
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
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> Response:
    _ = _csrf
    try:
        user = require_user(context)
    except LoginRequired:
        return _redirect_auth_login(context, settings)

    try:
        crypto_config = settings.require_customer_identity_crypto_config()
        form = await request.form(max_fields=8, max_part_size=4096)
        _validate_identity_form(form)
        command = SaveCustomerIdentity(
            actor=resolve_customer_identity_actor(user),
            expected_revision=_parse_nonnegative_integer(
                _required_form_text(form, "expected_revision")
            ),
            first_name=_required_form_text(form, "first_name"),
            last_name=_required_form_text(form, "last_name"),
            middle_name=_optional_form_text(form, "middle_name"),
            jshshir=_required_form_text(form, "jshshir"),
            document_type=_required_form_text(form, "document_type"),
            document_number=_required_form_text(form, "document_number"),
        )
        save_own_customer_identity(
            repository=SqlAlchemyCustomerIdentityRepository(db),
            audit_writer=SqlAlchemyAuditWriter(db),
            crypto_config=crypto_config,
            command=command,
            now=now,
        )
    except CustomerIdentityServiceError as exc:
        return _identity_redirect(error_code=exc.code)
    except CustomerIdentityCryptoConfigurationError:
        return _identity_redirect(error_code=ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE)
    except (StarletteHTTPException, TypeError, ValueError):
        return _identity_redirect(error_code=ErrorCode.VALIDATION_ERROR)
    return _identity_redirect(notice="identity-saved")


@router.post("/document", response_model=None)
async def upload_document(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> Response:
    session_factory = request.app.state.database_session_factory
    try:
        security_context = resolve_customer_document_request_context(
            request,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )
        async with bounded_multipart_upload(
            request,
            file_field_name="document_file",
            session_context=security_context.csrf_context,
            now=now,
        ) as upload:
            command = _document_upload_command(
                actor=security_context.actor,
                fields=upload.auxiliary_fields,
            )
            client_ip = resolve_client_ip(request, settings)
            with get_customer_document_storage_service(request) as storage:
                await upload_and_attach_own_customer_document(
                    session_factory,
                    command=command,
                    source=upload.as_upload_file(),
                    client_ip=client_ip,
                    now=now,
                    settings=settings,
                    storage=storage,
                )
    except LoginRequired:
        return _unauthorized_redirect(settings)
    except CsrfFailed:
        return _identity_redirect(error_code=ErrorCode.CSRF_FAILED)
    except StorageMultipartError as exc:
        return _identity_redirect(error_code=exc.error_code)
    except ImageSanitizationError as exc:
        return _identity_redirect(error_code=exc.public_code)
    except StorageUploadError as exc:
        return _identity_redirect(error_code=exc.code)
    except CustomerDocumentServiceError as exc:
        return _identity_redirect(error_code=exc.code)
    except (
        ClientIpResolutionError,
        CustomerDocumentRequestSecurityError,
        CustomerDocumentStorageUnavailable,
    ):
        return _identity_redirect(error_code=ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE)
    except (TypeError, ValueError):
        return _identity_redirect(error_code=ErrorCode.VALIDATION_ERROR)
    return _identity_redirect(notice="document-uploaded")


@router.get("/document", response_model=None)
def current_document(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> Response:
    session_factory = request.app.state.database_session_factory
    try:
        security_context = resolve_customer_document_request_context(
            request,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )
        with get_customer_document_storage_service(request) as storage:
            url = create_own_current_customer_document_url(
                session_factory,
                actor=security_context.actor,
                storage=storage,
                settings=settings,
                now=now,
            )
    except LoginRequired:
        return _unauthorized_redirect(settings)
    except StorageAccessDeniedError:
        return _identity_redirect(error_code=ErrorCode.FILE_ACCESS_DENIED)
    except (
        CustomerDocumentRequestSecurityError,
        CustomerDocumentStorageUnavailable,
        ObjectStorageSettingsError,
        StorageProviderError,
        StorageUploadError,
    ):
        return _identity_redirect(error_code=ErrorCode.FILE_STORAGE_ERROR)

    response = RedirectResponse(
        url.as_response_value(),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return mark_auth_response_no_store(response)


def _render_identity_page(
    request: Request,
    *,
    context: CurrentSessionContext,
    language: CustomerIdentityWebLanguage,
    page_state: OwnCustomerIdentityPageState | None,
    error_code: ErrorCode | None,
    notice: str | None,
) -> Response:
    copy = get_customer_identity_web_copy(language)
    summary = page_state.identity if page_state is not None else None
    error_message = (
        get_customer_identity_web_message(language, error_code)
        if error_code is not None
        else None
    )
    notice_message = None
    if notice == "identity-saved":
        notice_message = copy.saved_notice
    elif notice == "document-uploaded":
        notice_message = copy.uploaded_notice
    response = templates.TemplateResponse(
        request,
        "customer/identity.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "copy": copy,
                "page_state": page_state,
                "summary": summary,
                "expected_revision": summary.revision.value if summary else 0,
                "submission_id": uuid4(),
                "error_code": error_code.value if error_code else None,
                "error_message": error_message,
                "notice_message": notice_message,
            },
            context.get_session_row(),
        ),
        status_code=(status.HTTP_200_OK),
    )
    if error_code is not None:
        response.headers["X-Error-Code"] = error_code.value
    return mark_auth_response_no_store(response)


def _document_upload_command(
    *,
    actor: CustomerDocumentActor,
    fields: Mapping[str, str],
) -> UploadOwnCustomerDocument:
    if set(fields) != {"submission_id", "expected_current_document_id"}:
        raise ValueError("Customer document form fields are invalid")
    submission_id = UUID(fields["submission_id"])
    if submission_id.int == 0:
        raise ValueError("Customer document submission ID is invalid")
    raw_expected = fields["expected_current_document_id"]
    expected_id = UUID(raw_expected) if raw_expected else None
    if expected_id is not None and expected_id.int == 0:
        raise ValueError("Expected customer document ID is invalid")
    return UploadOwnCustomerDocument(
        actor=actor,
        submission_id=CustomerDocumentSubmissionId(submission_id),
        expected_current=ExpectedCurrentCustomerDocument(expected_id),
    )


def _required_form_text(form: FormData, field_name: str) -> str:
    value = form.get(field_name)
    if not isinstance(value, str):
        raise ValueError("Customer identity form field is invalid")
    return value


def _validate_identity_form(form: FormData) -> None:
    expected_fields = {
        "csrf_token",
        "expected_revision",
        "first_name",
        "last_name",
        "middle_name",
        "jshshir",
        "document_type",
        "document_number",
    }
    field_names = tuple(name for name, _value in form.multi_items())
    if set(field_names) != expected_fields or len(field_names) != len(expected_fields):
        raise ValueError("Customer identity form fields are invalid")


def _optional_form_text(form: FormData, field_name: str) -> str | None:
    value = _required_form_text(form, field_name)
    return value if value.strip() else None


def _parse_nonnegative_integer(value: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError("Expected identity revision is invalid")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("Expected identity revision is invalid")
    return parsed


def _resolve_language(request: Request) -> CustomerIdentityWebLanguage:
    return resolve_customer_identity_web_language(
        request.cookies.get(CUSTOMER_IDENTITY_LOCALE_COOKIE_NAME),
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


def _identity_redirect(
    *,
    error_code: ErrorCode | None = None,
    notice: str | None = None,
) -> Response:
    query: dict[str, str] = {}
    if error_code is not None:
        query["error"] = error_code.value
    if notice is not None:
        query["notice"] = notice
    location = IDENTITY_PATH
    if query:
        location = f"{location}?{urlencode(query)}"
    response = RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)
    if error_code is not None:
        response.headers["X-Error-Code"] = error_code.value
    return mark_auth_response_no_store(response)


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


def _unauthorized_redirect(settings: Settings) -> Response:
    response = RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)
    response.headers["X-Error-Code"] = ErrorCode.UNAUTHORIZED.value
    delete_session_cookie(response, settings)
    return mark_auth_response_no_store(response)
