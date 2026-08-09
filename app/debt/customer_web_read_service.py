"""Web-only opaque locators layered over the safe customer read projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.customer_read_service import (
    CustomerDebtDetailProjection,
    CustomerDebtDetailResult,
    CustomerDebtListProjection,
    get_own_customer_debt_detail,
    list_own_customer_debts,
)
from app.debt.payment_progress import DebtPaymentProgressProjection
from app.debt.repository import (
    get_customer_owned_debt_with_shop,
    list_customer_owned_debts_with_shops,
)
from app.debt.values import DebtId
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.repository import SqlAlchemyCurrentOfferResolver

__all__ = (
    "CustomerDebtWebDetail",
    "CustomerDebtWebDetailResult",
    "CustomerDebtWebListItem",
    "get_own_customer_debt_web_detail",
    "list_own_customer_debt_web_items",
)


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtWebListItem:
    debt_id: DebtId = field(repr=False)
    projection: CustomerDebtListProjection

    def __repr__(self) -> str:
        return "CustomerDebtWebListItem(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtWebDetail:
    debt_id: DebtId = field(repr=False)
    expected_revision: int
    displayed_offer_text_id: UUID | None = field(repr=False)
    projection: CustomerDebtDetailProjection

    def __repr__(self) -> str:
        return "CustomerDebtWebDetail(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtWebDetailResult:
    error: ErrorCode | None
    detail: CustomerDebtWebDetail | None = field(default=None, repr=False)


def list_own_customer_debt_web_items(
    session: Session,
    *,
    authority: CustomerDebtAuthority,
    payment_progress_by_debt_id: Mapping[UUID, DebtPaymentProgressProjection]
    | None = None,
) -> tuple[CustomerDebtWebListItem, ...]:
    rows = list_customer_owned_debts_with_shops(
        session, customer_id=authority.customer_id
    )
    projections = list_own_customer_debts(
        session,
        authority=authority,
        payment_progress_by_debt_id=payment_progress_by_debt_id,
    )
    if len(rows) != len(projections):
        raise RuntimeError("Customer debt web projection changed during one read")
    return tuple(
        CustomerDebtWebListItem(debt_id=DebtId(row.debt.id), projection=projection)
        for row, projection in zip(rows, projections, strict=True)
    )


def get_own_customer_debt_web_detail(
    session: Session,
    *,
    authority: CustomerDebtAuthority,
    debt_id: DebtId,
    language: OfferLanguage,
    payment_progress_by_debt_id: Mapping[UUID, DebtPaymentProgressProjection]
    | None = None,
) -> CustomerDebtWebDetailResult:
    core: CustomerDebtDetailResult = get_own_customer_debt_detail(
        session,
        authority=authority,
        debt_id=debt_id,
        language=language,
        payment_progress_by_debt_id=payment_progress_by_debt_id,
    )
    if core.error is not None:
        return CustomerDebtWebDetailResult(error=core.error)
    assert core.detail is not None
    row = get_customer_owned_debt_with_shop(
        session,
        customer_id=authority.customer_id,
        debt_id=debt_id,
    )
    if row is None:
        return CustomerDebtWebDetailResult(error=ErrorCode.DEBT_UNAVAILABLE)
    displayed_offer_text_id = None
    if core.detail.legal_offer is not None:
        resolved = SqlAlchemyCurrentOfferResolver(session).resolve_current(
            purpose=OfferPurpose.DEBT_ACCEPTANCE,
            language=language,
        )
        if resolved is None:
            return CustomerDebtWebDetailResult(error=ErrorCode.DEBT_UNAVAILABLE)
        variant = resolved.text.variant
        legal = core.detail.legal_offer
        if (
            variant.language is not legal.language
            or variant.title != legal.title
            or variant.body != legal.body
        ):
            return CustomerDebtWebDetailResult(error=ErrorCode.DEBT_UNAVAILABLE)
        displayed_offer_text_id = resolved.text.id
    return CustomerDebtWebDetailResult(
        error=None,
        detail=CustomerDebtWebDetail(
            debt_id=debt_id,
            expected_revision=row.debt.revision,
            displayed_offer_text_id=displayed_offer_text_id,
            projection=core.detail,
        ),
    )
