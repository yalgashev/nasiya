"""Side-effect-free own-customer debt and legal-content projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.enums import DebtStatus
from app.debt.payment_progress import DebtPaymentProgressProjection
from app.debt.repository import (
    CustomerOwnedDebtRow,
    get_customer_owned_debt_with_shop,
    list_customer_owned_debts_with_shops,
)
from app.debt.values import (
    DebtId,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
)
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.policy import OfferVersionCompletenessPolicy
from app.offers.repository import SqlAlchemyCurrentOfferResolver

__all__ = (
    "CustomerDebtDetailProjection",
    "CustomerDebtDetailResult",
    "CustomerDebtLegalOfferProjection",
    "CustomerDebtListProjection",
    "get_own_customer_debt_detail",
    "list_own_customer_debts",
)


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtListProjection:
    shop_name: str
    status: DebtStatus
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: str
    pending_expires_at: str
    payment_progress: DebtPaymentProgressProjection | None = None

    def __repr__(self) -> str:
        return "CustomerDebtListProjection(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtLegalOfferProjection:
    language: OfferLanguage
    title: str = field(repr=False)
    body: str = field(repr=False)

    def __repr__(self) -> str:
        return "CustomerDebtLegalOfferProjection(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtDetailProjection:
    shop_name: str
    status: DebtStatus
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: str
    pending_expires_at: str
    accepted_at: str | None
    rejected_at: str | None
    cancelled_at: str | None
    expired_at: str | None
    decision_reason: str | None = field(default=None, repr=False)
    legal_offer: CustomerDebtLegalOfferProjection | None = field(
        default=None, repr=False
    )
    payment_progress: DebtPaymentProgressProjection | None = None

    def __repr__(self) -> str:
        return "CustomerDebtDetailProjection(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtDetailResult:
    error: ErrorCode | None
    detail: CustomerDebtDetailProjection | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        has_detail = isinstance(self.detail, CustomerDebtDetailProjection)
        if (self.error is None) != has_detail:
            raise ValueError("Customer debt detail result is invalid")
        if self.error is not None and self.error is not ErrorCode.DEBT_UNAVAILABLE:
            raise ValueError("Customer debt detail error is invalid")

    def __repr__(self) -> str:
        return f"CustomerDebtDetailResult(error={self.error!r}, detail=<redacted>)"


def list_own_customer_debts(
    session: Session,
    *,
    authority: CustomerDebtAuthority | None,
    payment_progress_by_debt_id: Mapping[UUID, DebtPaymentProgressProjection]
    | None = None,
) -> tuple[CustomerDebtListProjection, ...]:
    """Read only the debts reached through the caller's own active Customer."""

    if authority is None:
        return ()
    _require_authority(authority)
    rows = list_customer_owned_debts_with_shops(
        session, customer_id=authority.customer_id
    )
    return tuple(
        _present_list_item(row, payment_progress_by_debt_id=payment_progress_by_debt_id)
        for row in rows
    )


def get_own_customer_debt_detail(
    session: Session,
    *,
    authority: CustomerDebtAuthority | None,
    debt_id: DebtId,
    language: OfferLanguage,
    payment_progress_by_debt_id: Mapping[UUID, DebtPaymentProgressProjection]
    | None = None,
) -> CustomerDebtDetailResult:
    """Return an own debt only; an absent or foreign locator is intentionally vague."""

    if authority is None:
        return CustomerDebtDetailResult(error=ErrorCode.DEBT_UNAVAILABLE)
    _require_authority(authority)
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    if not isinstance(language, OfferLanguage):
        raise TypeError("language must be an OfferLanguage")
    row = get_customer_owned_debt_with_shop(
        session,
        customer_id=authority.customer_id,
        debt_id=debt_id,
    )
    if row is None:
        return CustomerDebtDetailResult(error=ErrorCode.DEBT_UNAVAILABLE)
    legal_offer = _resolve_current_complete_legal_offer(session, language=language)
    return CustomerDebtDetailResult(
        error=None,
        detail=_present_detail(
            row,
            legal_offer=legal_offer,
            payment_progress_by_debt_id=payment_progress_by_debt_id,
        ),
    )


def _present_list_item(
    row: CustomerOwnedDebtRow,
    *,
    payment_progress_by_debt_id: Mapping[UUID, DebtPaymentProgressProjection]
    | None = None,
) -> CustomerDebtListProjection:
    debt = row.debt
    return CustomerDebtListProjection(
        shop_name=row.shop_name,
        status=DebtStatus(debt.status),
        original_amount=OriginalAmountUZS(debt.original_amount_uzs),
        discount_basis_points=DiscountBasisPoints(debt.discount_basis_points),
        discounted_amount=DiscountedAmountUZS(debt.discounted_amount_uzs),
        due_date=debt.due_date.isoformat(),
        pending_expires_at=_optional_iso(debt.pending_expires_at),
        payment_progress=_progress_for(debt.id, payment_progress_by_debt_id),
    )


def _present_detail(
    row: CustomerOwnedDebtRow,
    *,
    legal_offer: CustomerDebtLegalOfferProjection | None,
    payment_progress_by_debt_id: Mapping[UUID, DebtPaymentProgressProjection]
    | None = None,
) -> CustomerDebtDetailProjection:
    item = _present_list_item(
        row, payment_progress_by_debt_id=payment_progress_by_debt_id
    )
    debt = row.debt
    status = item.status
    decision_reason = (
        debt.rejection_reason
        if status is DebtStatus.REJECTED
        else debt.cancellation_reason
        if status is DebtStatus.CANCELLED
        else None
    )
    return CustomerDebtDetailProjection(
        shop_name=item.shop_name,
        status=status,
        original_amount=item.original_amount,
        discount_basis_points=item.discount_basis_points,
        discounted_amount=item.discounted_amount,
        due_date=item.due_date,
        pending_expires_at=item.pending_expires_at,
        accepted_at=_optional_iso(debt.accepted_at),
        rejected_at=_optional_iso(debt.rejected_at),
        cancelled_at=_optional_iso(debt.cancelled_at),
        expired_at=_optional_iso(debt.expired_at),
        decision_reason=decision_reason,
        legal_offer=legal_offer if status is DebtStatus.PENDING else None,
        payment_progress=item.payment_progress,
    )


def _resolve_current_complete_legal_offer(
    session: Session, *, language: OfferLanguage
) -> CustomerDebtLegalOfferProjection | None:
    resolver = SqlAlchemyCurrentOfferResolver(session)
    resolved = resolver.resolve_current(
        purpose=OfferPurpose.DEBT_ACCEPTANCE,
        language=language,
    )
    if resolved is None:
        return None
    current = resolver.resolve_current_version_with_all_texts(
        purpose=OfferPurpose.DEBT_ACCEPTANCE
    )
    if current is None:
        return None
    version, texts = current
    complete = (
        OfferVersionCompletenessPolicy()
        .evaluate(
            offer_version_id=version.id,
            variants=(text.variant for text in texts),
        )
        .complete
    )
    if version.id != resolved.version.id or not complete:
        return None
    variant = resolved.text.variant
    return CustomerDebtLegalOfferProjection(
        language=variant.language,
        title=variant.title,
        body=variant.body,
    )


def _require_authority(authority: CustomerDebtAuthority) -> None:
    if not isinstance(authority, CustomerDebtAuthority):
        raise TypeError("authority must be a CustomerDebtAuthority")


def _optional_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Debt projection timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _progress_for(
    debt_id: UUID,
    progress_by_debt_id: Mapping[UUID, DebtPaymentProgressProjection] | None,
) -> DebtPaymentProgressProjection | None:
    if progress_by_debt_id is None:
        return None
    progress = progress_by_debt_id.get(debt_id)
    if progress is not None and not isinstance(progress, DebtPaymentProgressProjection):
        raise TypeError("Payment progress projection is invalid")
    return progress
