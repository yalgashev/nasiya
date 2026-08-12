"""Exact SSR/PRG routes for private, band-only risk disclosure."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage
from app.debt.web_presentation import resolve_debt_web_language
from app.rating.dependencies import (
    DetachedDisclosureReadContext,
    DisclosureClock,
    get_detached_current_shop_disclosure_actor_context,
    get_detached_current_shop_disclosure_read_actor_context,
    get_risk_band_disclosure_clock,
)
from app.rating.disclosure import (
    RiskBandDisclosureRawForm,
    assemble_risk_band_disclosure_command,
)
from app.rating.disclosure_service import (
    DisclosureMutationRejected,
    DisclosurePersistenceError,
    read_risk_band_disclosure_page_context,
    record_risk_band_disclosure,
)
from app.rating.enums import RiskBand, RiskBandDisclosurePurpose
from app.rating.presentation import (
    disclosure_snapshot_path,
    get_risk_band_web_copy,
)
from app.rating.targeting import DetachedDisclosureActorContext
from app.rating.values import DisclosureViewId
from app.security_headers import mark_auth_response_no_store
from app.shop_customer.values import ShopCustomerId

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
_ROSTER_PATH = "/shop/customers"


@router.post(
    "/shop/customers/{shop_customer_id}/risk-band-disclosures",
    name="shop_risk_band_disclosure_create",
    response_model=None,
)
def create_risk_band_disclosure(
    shop_customer_id: str,
    request: Request,
    actor: Annotated[
        DetachedDisclosureActorContext,
        Depends(get_detached_current_shop_disclosure_actor_context),
    ],
    disclosure_clock: Annotated[
        DisclosureClock, Depends(get_risk_band_disclosure_clock)
    ],
    purpose: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Form()] = None,
) -> Response:
    locator = _parse_shop_customer_id(shop_customer_id)
    if locator is None:
        return _redirect_roster(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)
    assembled = assemble_risk_band_disclosure_command(
        raw=RiskBandDisclosureRawForm(
            purpose=purpose,
            idempotency_key=idempotency_key,
        ),
        actor_user_id=actor.actor_user_id,
        current_shop_id=actor.current_shop_id,
        shop_customer_id=locator,
    )
    if assembled.error is not None:
        return _redirect_roster(error=assembled.error)
    assert assembled.command is not None
    try:
        with request.app.state.database_session_factory.begin() as session:
            result = record_risk_band_disclosure(
                session,
                actor=actor,
                command=assembled.command,
                disclosure_clock=disclosure_clock,
            )
    except DisclosureMutationRejected as rejected:
        return _redirect_roster(error=rejected.error)
    except DisclosurePersistenceError:
        return _redirect_roster(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)
    return _redirect_snapshot(result.disclosure_view_id)


@router.get(
    "/shop/risk-band-disclosures/{disclosure_view_id}",
    name="shop_risk_band_disclosure_view",
    response_class=HTMLResponse,
    response_model=None,
)
def risk_band_disclosure_view(
    disclosure_view_id: str,
    request: Request,
    read_context: Annotated[
        DetachedDisclosureReadContext,
        Depends(get_detached_current_shop_disclosure_read_actor_context),
    ],
) -> Response:
    locator = _parse_disclosure_view_id(disclosure_view_id)
    if locator is None:
        return _redirect_roster(generic_error=True)
    try:
        with request.app.state.database_session_factory() as session:
            page_context = read_risk_band_disclosure_page_context(
                session,
                actor=read_context.actor,
                disclosure_view_id=locator,
            )
    except RuntimeError:
        return _redirect_roster(generic_error=True)
    if page_context is None:
        return _redirect_roster(generic_error=True)
    projection = page_context.projection
    language = resolve_debt_web_language(request.headers.get("accept-language"))
    response = templates.TemplateResponse(
        request,
        "rating/disclosure_view.html",
        {
            "page_language": language.value,
            "copy": get_risk_band_web_copy(language),
            "band_label": _band_label(language, projection.band),
            "purpose_label": _purpose_label(language, projection.purpose),
            "viewed_at": projection.viewed_at,
            "disclosure_post_action": (
                None
                if page_context.action is None
                else page_context.action.same_origin_post_path()
            ),
            "disclosure_idempotency_key": str(uuid4()),
            "csrf_token": read_context.csrf_token,
            "selected_purpose": projection.purpose,
            "disclosure_purposes": tuple(RiskBandDisclosurePurpose),
        },
    )
    return mark_auth_response_no_store(response)


def _parse_shop_customer_id(value: str) -> ShopCustomerId | None:
    try:
        return ShopCustomerId(UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _parse_disclosure_view_id(value: str) -> DisclosureViewId | None:
    try:
        return DisclosureViewId(UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _redirect_roster(
    *, error: ErrorCode | None = None, generic_error: bool = False
) -> Response:
    target = _ROSTER_PATH
    if error is not None or generic_error:
        target = f"{_ROSTER_PATH}?risk_error=unavailable"
    return mark_auth_response_no_store(
        RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    )


def _redirect_snapshot(disclosure_view_id: DisclosureViewId) -> Response:
    return mark_auth_response_no_store(
        RedirectResponse(
            disclosure_snapshot_path(disclosure_view_id),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    )


def _band_label(language: DebtWebLanguage, band: RiskBand) -> str:
    return get_risk_band_web_copy(language)[f"band_{band.value}"]


def _purpose_label(
    language: DebtWebLanguage, purpose: RiskBandDisclosurePurpose
) -> str:
    return get_risk_band_web_copy(language)[f"purpose_{purpose.value}"]
