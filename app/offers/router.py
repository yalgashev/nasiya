from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.deps import (
    CurrentSessionContext,
    get_current_session_context,
    get_current_time,
    get_database_session,
    require_user,
    validate_csrf,
)
from app.auth.error_codes import ErrorCode, get_error_http_status
from app.auth.models import User
from app.auth.template_context import with_csrf_context
from app.offers.authorization import (
    PlatformAdminActor,
    require_platform_admin_actor,
)
from app.offers.commands import AcceptCurrentRegistrationOfferCommand
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.read_models import (
    get_offer_version_detail_for_admin,
    list_offer_versions_for_admin,
)
from app.offers.service import (
    AcceptCurrentRegistrationOfferOutcome,
    MakeOfferVersionCurrentOutcome,
    accept_current_registration_offer,
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    resolve_current_offer,
    upsert_offer_draft_text,
)
from app.offers.web_presentation import (
    OFFER_WEB_LOCALE_COOKIE_NAME,
    OfferWebLanguage,
    get_offer_web_message,
    resolve_offer_web_language,
)
from app.security_headers import mark_auth_response_no_store

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/admin/offers", response_class=HTMLResponse, response_model=None)
def admin_offer_list(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    actor: Annotated[
        PlatformAdminActor,
        Depends(require_platform_admin_actor),
    ],
) -> Response:
    offers = list_offer_versions_for_admin(db, actor=actor)
    response = templates.TemplateResponse(
        request,
        "offers/admin_list.html",
        {"offers": offers},
    )
    return mark_auth_response_no_store(response)


@router.get(
    "/admin/offers/new",
    response_class=HTMLResponse,
    response_model=None,
)
def admin_offer_create_page(
    request: Request,
    _actor: Annotated[
        PlatformAdminActor,
        Depends(require_platform_admin_actor),
    ],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    error: Annotated[str | None, Query()] = None,
) -> Response:
    language = resolve_offer_web_language(
        request.cookies.get(OFFER_WEB_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )
    response = templates.TemplateResponse(
        request,
        "offers/admin_create.html",
        with_csrf_context(
            {
                "page_language": language.value,
                "purposes": tuple(OfferPurpose),
                "error_message": get_offer_web_message(language, error),
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post("/admin/offers", response_model=None)
def admin_offer_create(
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    actor: Annotated[
        PlatformAdminActor,
        Depends(require_platform_admin_actor),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    purpose: Annotated[str, Form()] = "",
) -> Response:
    _ = _csrf
    try:
        parsed_purpose = OfferPurpose(purpose)
    except ValueError:
        return _redirect_no_store(
            "/admin/offers/new?error=validation-error",
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    created = create_offer_draft_version(
        db,
        actor=actor,
        purpose=parsed_purpose,
        now=now,
    )
    return _redirect_no_store(f"/admin/offers/{created.id}?notice=draft-created")


@router.get(
    "/admin/offers/{offer_version_id}",
    response_class=HTMLResponse,
    response_model=None,
)
def admin_offer_detail(
    request: Request,
    offer_version_id: UUID,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    actor: Annotated[
        PlatformAdminActor,
        Depends(require_platform_admin_actor),
    ],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    notice: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> Response:
    offer = get_offer_version_detail_for_admin(
        db,
        actor=actor,
        offer_version_id=offer_version_id,
    )
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    language = resolve_offer_web_language(
        request.cookies.get(OFFER_WEB_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )
    texts_by_language = {text.language: text for text in offer.texts}
    purpose_versions = list_offer_versions_for_admin(
        db,
        actor=actor,
        purpose=offer.summary.purpose,
    )
    current_offer = next(
        (
            version
            for version in purpose_versions
            if version.status is OfferStatus.CURRENT
        ),
        None,
    )
    text_forms = tuple(
        {
            "language": legal_language,
            "title": (
                ""
                if legal_language not in texts_by_language
                else texts_by_language[legal_language].title
            ),
            "body": (
                ""
                if legal_language not in texts_by_language
                else texts_by_language[legal_language].body
            ),
        }
        for legal_language in OfferLanguage
    )
    response = templates.TemplateResponse(
        request,
        "offers/admin_detail.html",
        with_csrf_context(
            {
                "offer": offer,
                "page_language": language.value,
                "notice_message": get_offer_web_message(language, notice),
                "error_message": get_offer_web_message(language, error),
                "can_edit": offer.summary.status is OfferStatus.DRAFT,
                "can_make_current": offer.summary.status
                in {OfferStatus.APPROVED, OfferStatus.CURRENT},
                "current_offer": current_offer,
                "text_forms": text_forms,
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post(
    "/admin/offers/{offer_version_id}/texts/{language}",
    response_model=None,
)
def admin_offer_text_upsert(
    offer_version_id: UUID,
    language: OfferLanguage,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    actor: Annotated[
        PlatformAdminActor,
        Depends(require_platform_admin_actor),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    title: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
) -> Response:
    _ = _csrf
    try:
        result = upsert_offer_draft_text(
            db,
            actor=actor,
            offer_version_id=offer_version_id,
            language=language,
            title=title,
            body=body,
            now=now,
        )
    except ValueError:
        return _redirect_no_store(
            f"/admin/offers/{offer_version_id}?error=validation-error",
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    if result.error is ErrorCode.OFFER_NOT_DRAFT:
        return _redirect_no_store(
            f"/admin/offers/{offer_version_id}?error=offer-not-draft",
            error_code=ErrorCode.OFFER_NOT_DRAFT,
        )
    return _redirect_no_store(f"/admin/offers/{offer_version_id}?notice=text-updated")


@router.post(
    "/admin/offers/{offer_version_id}/approve",
    response_model=None,
)
def admin_offer_approve(
    offer_version_id: UUID,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    actor: Annotated[
        PlatformAdminActor,
        Depends(require_platform_admin_actor),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    legal_review_authority: Annotated[str, Form()] = "",
    legal_reviewed_at: Annotated[str, Form()] = "",
    legal_review_reference: Annotated[str, Form()] = "",
) -> Response:
    _ = _csrf
    reviewed_at, parsed = _parse_reviewed_at(legal_reviewed_at)
    if not parsed:
        return _redirect_no_store(
            f"/admin/offers/{offer_version_id}?error=validation-error",
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    result = approve_offer_version(
        db,
        actor=actor,
        offer_version_id=offer_version_id,
        legal_review_authority=legal_review_authority,
        legal_reviewed_at=reviewed_at,
        legal_review_reference=legal_review_reference,
        now=now,
    )
    if result.error is not None:
        error_slug = {
            ErrorCode.OFFER_NOT_DRAFT: "offer-not-draft",
            ErrorCode.OFFER_INCOMPLETE: "offer-incomplete",
            ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED: (
                "legal-review-evidence-required"
            ),
        }[result.error]
        return _redirect_no_store(
            f"/admin/offers/{offer_version_id}?error={error_slug}",
            error_code=result.error,
        )
    return _redirect_no_store(f"/admin/offers/{offer_version_id}?notice=offer-approved")


@router.post(
    "/admin/offers/{offer_version_id}/make-current",
    response_model=None,
)
def admin_offer_make_current(
    offer_version_id: UUID,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    actor: Annotated[
        PlatformAdminActor,
        Depends(require_platform_admin_actor),
    ],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    expected_current_version_id: Annotated[str, Form()] = "",
) -> Response:
    _ = _csrf
    expected_current_id, parsed = _parse_optional_uuid(expected_current_version_id)
    if not parsed:
        return _redirect_no_store(
            f"/admin/offers/{offer_version_id}?error=validation-error",
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    result = make_offer_version_current(
        db,
        actor=actor,
        offer_version_id=offer_version_id,
        expected_current_version_id=expected_current_id,
        now=now,
    )
    if result.error is ErrorCode.OFFER_CHANGED:
        return _redirect_no_store(
            f"/admin/offers/{offer_version_id}?error=offer-changed",
            error_code=result.error,
        )
    if result.error is ErrorCode.OFFER_NOT_APPROVED:
        return _redirect_no_store(
            f"/admin/offers/{offer_version_id}?error=offer-not-approved",
            error_code=result.error,
        )
    notice = (
        "offer-already-current"
        if result.outcome is MakeOfferVersionCurrentOutcome.ALREADY_CURRENT
        else "offer-made-current"
    )
    return _redirect_no_store(f"/admin/offers/{offer_version_id}?notice={notice}")


@router.get(
    "/auth/registration-offer",
    response_class=HTMLResponse,
    response_model=None,
)
def registration_offer_page(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    _user: Annotated[User, Depends(require_user)],
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
    language: Annotated[str | None, Query()] = None,
    notice: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> Response:
    ui_language = resolve_offer_web_language(
        request.cookies.get(OFFER_WEB_LOCALE_COOKIE_NAME),
        request.headers.get("accept-language"),
    )
    selected_language, parsed = _parse_offer_language(language)
    if not parsed:
        return _render_registration_offer_error(
            request,
            selected_language=OfferLanguage.UZ_LATN,
            ui_language=ui_language,
            error_code=ErrorCode.VALIDATION_ERROR,
            error_slug="validation-error",
        )
    resolved = resolve_current_offer(
        db,
        purpose=OfferPurpose.REGISTRATION,
        language=selected_language,
    )
    if resolved.offer is None:
        return _render_registration_offer_error(
            request,
            selected_language=selected_language,
            ui_language=ui_language,
            error_code=ErrorCode.OFFER_UNAVAILABLE,
            error_slug="offer-unavailable",
        )
    response = templates.TemplateResponse(
        request,
        "offers/registration_offer.html",
        with_csrf_context(
            {
                "page_language": ui_language.value,
                "offer": resolved.offer,
                "selected_language": selected_language,
                "legal_languages": tuple(OfferLanguage),
                "legal_language_tag": _legal_language_tag(selected_language),
                "notice_message": get_offer_web_message(ui_language, notice),
                "error_message": get_offer_web_message(ui_language, error),
            },
            context.get_session_row(),
        ),
    )
    return mark_auth_response_no_store(response)


@router.post(
    "/auth/registration-offer/accept",
    response_model=None,
)
def registration_offer_accept(
    request: Request,
    db: Annotated[
        DatabaseSession,
        Depends(get_database_session, scope="function"),
    ],
    user: Annotated[User, Depends(require_user)],
    now: Annotated[datetime, Depends(get_current_time)],
    _csrf: Annotated[None, Depends(validate_csrf)],
    language: Annotated[str, Form()] = "",
    displayed_offer_text_id: Annotated[str, Form()] = "",
) -> Response:
    _ = _csrf
    selected_language, language_parsed = _parse_offer_language(language)
    text_id, text_id_parsed = _parse_required_uuid(displayed_offer_text_id)
    if not language_parsed or not text_id_parsed or text_id is None:
        return _redirect_no_store(
            (
                "/auth/registration-offer"
                f"?language={selected_language.value}&error=validation-error"
            ),
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    result = accept_current_registration_offer(
        db,
        command=AcceptCurrentRegistrationOfferCommand(
            user_id=user.id,
            language=selected_language,
            displayed_offer_text_id=text_id,
            user_agent_source=request.headers.get("user-agent"),
        ),
        now=now,
    )
    if result.error is ErrorCode.UNAUTHORIZED:
        return _redirect_no_store(
            "/auth/login",
            error_code=ErrorCode.UNAUTHORIZED,
        )
    if result.error in {ErrorCode.OFFER_CHANGED, ErrorCode.OFFER_UNAVAILABLE}:
        error_slug = (
            "offer-changed"
            if result.error is ErrorCode.OFFER_CHANGED
            else "offer-unavailable"
        )
        return _redirect_no_store(
            (
                "/auth/registration-offer"
                f"?language={selected_language.value}&error={error_slug}"
            ),
            error_code=result.error,
        )
    notice_slug = (
        "acceptance-replayed"
        if result.outcome is AcceptCurrentRegistrationOfferOutcome.REPLAYED
        else "acceptance-recorded"
    )
    return _redirect_no_store(
        "/auth/registration-offer"
        f"?language={selected_language.value}&notice={notice_slug}"
    )


def _redirect_no_store(
    target: str,
    *,
    error_code: ErrorCode | None = None,
) -> Response:
    response = RedirectResponse(
        target,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    if error_code is not None:
        response.headers["X-Error-Code"] = error_code.value
    return mark_auth_response_no_store(response)


def _parse_reviewed_at(raw_value: str) -> tuple[datetime | None, bool]:
    normalized = raw_value.strip()
    if not normalized:
        return None, True
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC), True


def _parse_optional_uuid(raw_value: str) -> tuple[UUID | None, bool]:
    normalized = raw_value.strip()
    if not normalized:
        return None, True
    try:
        return UUID(normalized), True
    except ValueError:
        return None, False


def _parse_required_uuid(raw_value: str) -> tuple[UUID | None, bool]:
    normalized = raw_value.strip()
    if not normalized:
        return None, False
    try:
        return UUID(normalized), True
    except ValueError:
        return None, False


def _parse_offer_language(
    raw_value: str | None,
) -> tuple[OfferLanguage, bool]:
    if raw_value is None or not raw_value.strip():
        return OfferLanguage.UZ_LATN, True
    try:
        return OfferLanguage(raw_value), True
    except ValueError:
        return OfferLanguage.UZ_LATN, False


def _legal_language_tag(language: OfferLanguage) -> str:
    return {
        OfferLanguage.UZ_LATN: "uz-Latn",
        OfferLanguage.UZ_CYRL: "uz-Cyrl",
        OfferLanguage.RU: "ru",
    }[language]


def _render_registration_offer_error(
    request: Request,
    *,
    selected_language: OfferLanguage,
    ui_language: OfferWebLanguage,
    error_code: ErrorCode,
    error_slug: str,
) -> Response:
    response = templates.TemplateResponse(
        request,
        "offers/registration_offer.html",
        {
            "page_language": ui_language.value,
            "offer": None,
            "selected_language": selected_language,
            "legal_languages": tuple(OfferLanguage),
            "legal_language_tag": _legal_language_tag(selected_language),
            "notice_message": None,
            "error_message": get_offer_web_message(ui_language, error_slug),
        },
        status_code=get_error_http_status(error_code),
    )
    response.headers["X-Error-Code"] = error_code.value
    return mark_auth_response_no_store(response)
