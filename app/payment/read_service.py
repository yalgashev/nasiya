"""Read-only, tenant/own-customer Payment history and receipt composition."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.debt.business_time import (
    normalize_payment_created_at,
)
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.customer_read_service import (
    CustomerDebtDetailResult,
    CustomerDebtListProjection,
    get_own_customer_debt_detail,
    list_own_customer_debts,
)
from app.debt.customer_web_read_service import (
    CustomerDebtWebDetailResult,
    CustomerDebtWebListItem,
    get_own_customer_debt_web_detail,
    list_own_customer_debt_web_items,
)
from app.debt.enums import DebtBalanceBasis, DebtStatus
from app.debt.models import Debt
from app.debt.payment_progress import DebtPaymentProgressProjection
from app.debt.repository import (
    get_customer_owned_debt_with_shop,
    get_tenant_debt,
    list_customer_owned_debts_with_shops,
    list_tenant_debts,
)
from app.debt.tenant_read_service import (
    TenantDebtDetailProjection,
    TenantDebtListProjection,
    get_tenant_debt_detail,
    list_tenant_customer_debts,
)
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    UserId,
)
from app.offers.enums import OfferLanguage
from app.payment.contracts import (
    PaymentHistoryItem,
    PaymentReceiptProjection,
    resolve_current_balance_basis,
    resolve_historical_balance_basis,
)
from app.payment.dependencies import DetachedPaymentReadActorContext
from app.payment.models import Payment
from app.payment.repository import (
    ScopedPaymentRow,
    get_customer_owned_payment,
    get_tenant_payment,
    historical_balance_after,
    list_customer_owned_debt_payments,
    list_tenant_debt_payments,
    payment_aggregate_from_row,
    posted_payment_total,
    remaining_due,
)
from app.payment.service import RecordDebtPaymentResult
from app.payment.values import (
    PaymentId,
    PostedPaymentTotalUZS,
    calculate_remaining_due_for_basis,
)
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import get_shop_staff_access
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "CustomerPaymentDebtProjection",
    "CustomerPaymentHistoryView",
    "CustomerPaymentReceiptView",
    "CustomerPaymentReadResult",
    "TenantPaymentHistoryView",
    "TenantPaymentReadAuthority",
    "TenantPaymentReadResult",
    "TenantPaymentReceiptView",
    "compose_payment_receipt",
    "get_customer_debt_detail_with_payment_progress",
    "get_customer_debt_web_detail_with_payment_progress",
    "get_own_customer_payment_receipt",
    "get_own_customer_payment_receipt_view",
    "get_own_customer_payment_history_view",
    "get_tenant_debt_detail_with_payment_progress",
    "get_tenant_payment_receipt",
    "get_tenant_payment_receipt_for_result",
    "get_tenant_payment_receipt_view",
    "get_tenant_payment_history_view",
    "list_customer_debts_with_payment_progress",
    "list_customer_debt_web_items_with_payment_progress",
    "list_own_customer_payment_history",
    "list_payment_progress_for_debts",
    "list_tenant_customer_debts_with_payment_progress",
    "list_tenant_payment_history",
    "resolve_tenant_payment_read_authority",
)


@dataclass(frozen=True, slots=True, repr=False)
class TenantPaymentReadAuthority:
    shop_id: ShopId = field(repr=False)
    actor_user_id: UserId = field(repr=False)
    role: ShopRole
    shop_status: ShopStatus

    def __repr__(self) -> str:
        return "TenantPaymentReadAuthority(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantPaymentHistoryView:
    """Scoped debt facts and revision-ordered facts for the shop read page."""

    error: ErrorCode | None
    debt: TenantDebtDetailProjection | None = field(default=None, repr=False)
    history: tuple[PaymentHistoryItem, ...] = field(default=(), repr=False)
    shop_status: ShopStatus | None = None

    def __post_init__(self) -> None:
        if self.error is None:
            if not isinstance(self.debt, TenantDebtDetailProjection) or not isinstance(
                self.shop_status, ShopStatus
            ):
                raise ValueError("Tenant payment history view is invalid")
        elif self.debt is not None or self.history or self.shop_status is not None:
            raise ValueError("Failed tenant payment history view must carry no data")

    def __repr__(self) -> str:
        return f"TenantPaymentHistoryView(error={self.error!r}, data=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantPaymentReceiptView:
    """Safe receipt plus an internal-only debt locator for a history backlink."""

    error: ErrorCode | None
    receipt: PaymentReceiptProjection | None = field(default=None, repr=False)
    debt_id: DebtId | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.error is None:
            if not isinstance(self.receipt, PaymentReceiptProjection) or not isinstance(
                self.debt_id, DebtId
            ):
                raise ValueError("Tenant payment receipt view is invalid")
        elif self.receipt is not None or self.debt_id is not None:
            raise ValueError("Failed tenant payment receipt view must carry no data")

    def __repr__(self) -> str:
        return f"TenantPaymentReceiptView(error={self.error!r}, data=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantPaymentReadResult:
    error: ErrorCode | None
    history: tuple[PaymentHistoryItem, ...] = field(default=(), repr=False)
    receipt: PaymentReceiptProjection | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.error is not None and self.error not in {
            ErrorCode.FORBIDDEN,
            ErrorCode.DEBT_UNAVAILABLE,
            ErrorCode.PAYMENT_UNAVAILABLE,
        }:
            raise ValueError("Tenant payment read error is invalid")
        if self.error is not None and (self.history or self.receipt is not None):
            raise ValueError("Failed tenant payment read must not carry data")
        if self.history and self.receipt is not None:
            raise ValueError("Tenant payment read result is ambiguous")

    def __repr__(self) -> str:
        return f"TenantPaymentReadResult(error={self.error!r}, data=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerPaymentReadResult:
    error: ErrorCode | None
    history: tuple[PaymentHistoryItem, ...] = field(default=(), repr=False)
    receipt: PaymentReceiptProjection | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.error is not None and self.error is not ErrorCode.PAYMENT_UNAVAILABLE:
            raise ValueError("Customer payment read error is invalid")
        if self.error is not None and (self.history or self.receipt is not None):
            raise ValueError("Failed customer payment read must not carry data")
        if self.history and self.receipt is not None:
            raise ValueError("Customer payment read result is ambiguous")

    def __repr__(self) -> str:
        return f"CustomerPaymentReadResult(error={self.error!r}, data=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerPaymentDebtProjection:
    """Identifier-safe own-customer debt facts for a payment-history page."""

    shop_name: str = field(repr=False)
    status: DebtStatus
    discounted_amount: DiscountedAmountUZS
    progress: DebtPaymentProgressProjection

    def __post_init__(self) -> None:
        if not isinstance(self.shop_name, str) or not self.shop_name.strip():
            raise ValueError("Payment history shop name is invalid")
        if not isinstance(self.status, DebtStatus):
            raise ValueError("Payment history debt status is invalid")
        if not isinstance(self.discounted_amount, DiscountedAmountUZS):
            raise ValueError("Payment history discounted target is invalid")
        if not isinstance(self.progress, DebtPaymentProgressProjection):
            raise ValueError("Payment history progress is invalid")

    def __repr__(self) -> str:
        return "CustomerPaymentDebtProjection(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerPaymentHistoryView:
    error: ErrorCode | None
    debt: CustomerPaymentDebtProjection | None = field(default=None, repr=False)
    history: tuple[PaymentHistoryItem, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self.error is None:
            if not isinstance(self.debt, CustomerPaymentDebtProjection):
                raise ValueError("Customer payment history view is invalid")
        elif self.debt is not None or self.history:
            raise ValueError("Failed customer payment history view must carry no data")

    def __repr__(self) -> str:
        return f"CustomerPaymentHistoryView(error={self.error!r}, data=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerPaymentReceiptView:
    error: ErrorCode | None
    receipt: PaymentReceiptProjection | None = field(default=None, repr=False)
    debt_id: DebtId | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.error is None:
            if not isinstance(self.receipt, PaymentReceiptProjection) or not isinstance(
                self.debt_id, DebtId
            ):
                raise ValueError("Customer payment receipt view is invalid")
        elif self.receipt is not None or self.debt_id is not None:
            raise ValueError("Failed customer payment receipt view must carry no data")

    def __repr__(self) -> str:
        return f"CustomerPaymentReceiptView(error={self.error!r}, data=<redacted>)"


def resolve_tenant_payment_read_authority(
    session: Session, *, actor: DetachedPaymentReadActorContext
) -> TenantPaymentReadAuthority | None:
    """Recheck live active staff without locking; suspended shops remain readable."""

    if not isinstance(actor, DetachedPaymentReadActorContext):
        raise TypeError("actor must be detached payment read context")
    access = get_shop_staff_access(
        session,
        shop_id=ShopId(actor.current_shop_id),
        user_id=UserId(actor.actor_user_id),
    )
    if (
        access is None
        or access.shop_status not in {ShopStatus.ACTIVE, ShopStatus.SUSPENDED}
        or not access.is_live
    ):
        return None
    return TenantPaymentReadAuthority(
        shop_id=access.shop_id,
        actor_user_id=actor.actor_user_id,
        role=access.role,
        shop_status=access.shop_status,
    )


def list_tenant_payment_history(
    session: Session, *, actor: DetachedPaymentReadActorContext, debt_id: DebtId
) -> TenantPaymentReadResult:
    authority = resolve_tenant_payment_read_authority(session, actor=actor)
    if authority is None:
        return TenantPaymentReadResult(error=ErrorCode.FORBIDDEN)
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    # A foreign/absent Debt locator must not disclose whether payments exist.
    debt_exists = session.scalar(
        select(Debt.id)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(
            Debt.id == debt_id.as_uuid(),
            ShopCustomer.shop_id == authority.shop_id,
        )
    )
    if debt_exists is None:
        return TenantPaymentReadResult(error=ErrorCode.DEBT_UNAVAILABLE)
    rows = list_tenant_debt_payments(
        session, shop_id=authority.shop_id, debt_id=debt_id
    )
    return TenantPaymentReadResult(error=None, history=_history(rows))


def get_tenant_payment_history_view(
    session: Session,
    *,
    actor: DetachedPaymentReadActorContext,
    debt_id: DebtId,
    server_now: datetime,
) -> TenantPaymentHistoryView:
    """One tenant-authorized source for shop debt balance and ledger display."""

    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    authority = resolve_tenant_payment_read_authority(session, actor=actor)
    if authority is None:
        return TenantPaymentHistoryView(error=ErrorCode.FORBIDDEN)
    debt = get_tenant_debt_detail_with_payment_progress(
        session,
        shop_id=authority.shop_id,
        debt_id=debt_id,
        server_now=server_now,
    )
    if debt is None:
        return TenantPaymentHistoryView(error=ErrorCode.DEBT_UNAVAILABLE)
    rows = list_tenant_debt_payments(
        session, shop_id=authority.shop_id, debt_id=debt_id
    )
    return TenantPaymentHistoryView(
        error=None,
        debt=debt,
        history=_history(rows),
        shop_status=authority.shop_status,
    )


def get_tenant_payment_receipt(
    session: Session,
    *,
    actor: DetachedPaymentReadActorContext,
    payment_id: PaymentId,
    server_now: datetime | None = None,
) -> TenantPaymentReadResult:
    if not isinstance(payment_id, PaymentId):
        raise TypeError("payment_id must be a PaymentId")
    authority = resolve_tenant_payment_read_authority(session, actor=actor)
    if authority is None:
        return TenantPaymentReadResult(error=ErrorCode.FORBIDDEN)
    row = get_tenant_payment(session, shop_id=authority.shop_id, payment_id=payment_id)
    if row is None:
        return TenantPaymentReadResult(error=ErrorCode.PAYMENT_UNAVAILABLE)
    return TenantPaymentReadResult(
        error=None,
        receipt=compose_payment_receipt(session, row=row, server_now=server_now),
    )


def get_tenant_payment_receipt_view(
    session: Session,
    *,
    actor: DetachedPaymentReadActorContext,
    payment_id: PaymentId,
    server_now: datetime | None = None,
) -> TenantPaymentReceiptView:
    """Scoped receipt plus a non-rendered route locator for the history link."""

    if not isinstance(payment_id, PaymentId):
        raise TypeError("payment_id must be a PaymentId")
    authority = resolve_tenant_payment_read_authority(session, actor=actor)
    if authority is None:
        return TenantPaymentReceiptView(error=ErrorCode.FORBIDDEN)
    row = get_tenant_payment(session, shop_id=authority.shop_id, payment_id=payment_id)
    if row is None:
        return TenantPaymentReceiptView(error=ErrorCode.PAYMENT_UNAVAILABLE)
    return TenantPaymentReceiptView(
        error=None,
        receipt=compose_payment_receipt(session, row=row, server_now=server_now),
        debt_id=DebtId(row.debt.id),
    )


def get_tenant_payment_receipt_for_result(
    session: Session,
    *,
    actor: DetachedPaymentReadActorContext,
    result: RecordDebtPaymentResult,
    server_now: datetime | None = None,
) -> TenantPaymentReadResult:
    """Carry the coordinator's typed locator into the scoped receipt reader."""

    if not isinstance(result, RecordDebtPaymentResult):
        raise TypeError("result must be a RecordDebtPaymentResult")
    return get_tenant_payment_receipt(
        session,
        actor=actor,
        payment_id=result.payment_id,
        server_now=server_now,
    )


def list_own_customer_payment_history(
    session: Session, *, authenticated_user: User, debt_id: DebtId
) -> CustomerPaymentReadResult:
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    customer_id = _resolve_own_customer_id(
        session, authenticated_user=authenticated_user
    )
    if customer_id is None:
        return CustomerPaymentReadResult(error=ErrorCode.PAYMENT_UNAVAILABLE)
    rows = list_customer_owned_debt_payments(
        session, customer_id=customer_id, debt_id=debt_id
    )
    # Lists do not expose a debt existence oracle to the customer-facing surface.
    return CustomerPaymentReadResult(error=None, history=_history(rows))


def get_own_customer_payment_history_view(
    session: Session,
    *,
    authenticated_user: User,
    debt_id: DebtId,
    server_now: datetime,
) -> CustomerPaymentHistoryView:
    """Own-only payment view with no customer eligibility or Shop-active gate."""

    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    customer_id = _resolve_own_customer_id(
        session, authenticated_user=authenticated_user
    )
    if customer_id is None:
        return CustomerPaymentHistoryView(error=ErrorCode.PAYMENT_UNAVAILABLE)
    row = get_customer_owned_debt_with_shop(
        session, customer_id=customer_id, debt_id=debt_id
    )
    if row is None:
        return CustomerPaymentHistoryView(error=ErrorCode.PAYMENT_UNAVAILABLE)
    progress = _progress(
        row.debt,
        posted_payment_total(session, debt_id=debt_id),
        normalize_payment_created_at(server_now),
    )
    history = _history(
        list_customer_owned_debt_payments(
            session, customer_id=customer_id, debt_id=debt_id
        )
    )
    return CustomerPaymentHistoryView(
        error=None,
        debt=CustomerPaymentDebtProjection(
            shop_name=row.shop_name,
            status=DebtStatus(row.debt.status),
            discounted_amount=DiscountedAmountUZS(row.debt.discounted_amount_uzs),
            progress=progress,
        ),
        history=history,
    )


def get_own_customer_payment_receipt(
    session: Session,
    *,
    authenticated_user: User,
    payment_id: PaymentId,
    server_now: datetime | None = None,
) -> CustomerPaymentReadResult:
    if not isinstance(payment_id, PaymentId):
        raise TypeError("payment_id must be a PaymentId")
    customer_id = _resolve_own_customer_id(
        session, authenticated_user=authenticated_user
    )
    if customer_id is None:
        return CustomerPaymentReadResult(error=ErrorCode.PAYMENT_UNAVAILABLE)
    row = get_customer_owned_payment(
        session, customer_id=customer_id, payment_id=payment_id
    )
    if row is None:
        return CustomerPaymentReadResult(error=ErrorCode.PAYMENT_UNAVAILABLE)
    return CustomerPaymentReadResult(
        error=None,
        receipt=compose_payment_receipt(session, row=row, server_now=server_now),
    )


def get_own_customer_payment_receipt_view(
    session: Session,
    *,
    authenticated_user: User,
    payment_id: PaymentId,
    server_now: datetime | None = None,
) -> CustomerPaymentReceiptView:
    """Own-only receipt with an internal debt locator for its history backlink."""

    if not isinstance(payment_id, PaymentId):
        raise TypeError("payment_id must be a PaymentId")
    customer_id = _resolve_own_customer_id(
        session, authenticated_user=authenticated_user
    )
    if customer_id is None:
        return CustomerPaymentReceiptView(error=ErrorCode.PAYMENT_UNAVAILABLE)
    row = get_customer_owned_payment(
        session, customer_id=customer_id, payment_id=payment_id
    )
    if row is None:
        return CustomerPaymentReceiptView(error=ErrorCode.PAYMENT_UNAVAILABLE)
    return CustomerPaymentReceiptView(
        error=None,
        receipt=compose_payment_receipt(session, row=row, server_now=server_now),
        debt_id=DebtId(row.debt.id),
    )


def list_payment_progress_for_debts(
    session: Session, *, debts: Iterable[Debt], server_now: datetime
) -> dict[UUID, DebtPaymentProgressProjection]:
    """Batch all totals once; both tenant and customer callers use this calculator."""

    now = normalize_payment_created_at(server_now)
    rows = tuple(debts)
    if not rows:
        return {}
    ids = tuple(debt.id for debt in rows)
    totals = dict(
        session.execute(
            select(
                Payment.debt_id,
                func.coalesce(func.sum(Payment.amount_uzs), Decimal("0")),
            )
            .where(Payment.debt_id.in_(ids))
            .group_by(Payment.debt_id)
        ).all()
    )
    return {
        debt.id: _progress(
            debt, PostedPaymentTotalUZS(Decimal(totals.get(debt.id, 0))), now
        )
        for debt in rows
    }


def list_tenant_customer_debts_with_payment_progress(
    session: Session,
    *,
    shop_id: ShopId,
    shop_customer_id: ShopCustomerId,
    server_now: datetime,
) -> tuple[TenantDebtListProjection, ...]:
    """Payment adapter for the existing tenant list; no payment import in debt."""

    candidates = tuple(
        debt
        for debt in list_tenant_debts(session, shop_id=shop_id)
        if debt.shop_customer_id == shop_customer_id.as_uuid()
    )
    return list_tenant_customer_debts(
        session,
        shop_id=shop_id,
        shop_customer_id=shop_customer_id,
        payment_progress_by_debt_id=list_payment_progress_for_debts(
            session, debts=candidates, server_now=server_now
        ),
    )


def get_tenant_debt_detail_with_payment_progress(
    session: Session,
    *,
    shop_id: ShopId,
    debt_id: DebtId,
    server_now: datetime,
) -> TenantDebtDetailProjection | None:
    debt = get_tenant_debt(session, shop_id=shop_id, debt_id=debt_id)
    progress = list_payment_progress_for_debts(
        session, debts=() if debt is None else (debt,), server_now=server_now
    )
    return get_tenant_debt_detail(
        session,
        shop_id=shop_id,
        debt_id=debt_id,
        payment_progress_by_debt_id=progress,
    )


def list_customer_debts_with_payment_progress(
    session: Session,
    *,
    authority: CustomerDebtAuthority | None,
    server_now: datetime,
) -> tuple[CustomerDebtListProjection, ...]:
    if authority is None:
        return list_own_customer_debts(session, authority=None)
    candidates = list_customer_owned_debts_with_shops(
        session, customer_id=authority.customer_id
    )
    return list_own_customer_debts(
        session,
        authority=authority,
        payment_progress_by_debt_id=list_payment_progress_for_debts(
            session,
            debts=(candidate.debt for candidate in candidates),
            server_now=server_now,
        ),
    )


def get_customer_debt_detail_with_payment_progress(
    session: Session,
    *,
    authority: CustomerDebtAuthority | None,
    debt_id: DebtId,
    language: OfferLanguage,
    server_now: datetime,
) -> CustomerDebtDetailResult:
    candidate = (
        None
        if authority is None
        else get_customer_owned_debt_with_shop(
            session, customer_id=authority.customer_id, debt_id=debt_id
        )
    )
    progress = list_payment_progress_for_debts(
        session,
        debts=() if candidate is None else (candidate.debt,),
        server_now=server_now,
    )
    return get_own_customer_debt_detail(
        session,
        authority=authority,
        debt_id=debt_id,
        language=language,
        payment_progress_by_debt_id=progress,
    )


def list_customer_debt_web_items_with_payment_progress(
    session: Session,
    *,
    authority: CustomerDebtAuthority,
    server_now: datetime,
) -> tuple[CustomerDebtWebListItem, ...]:
    """Compose own-customer debt cards with current payment facts once."""

    candidates = list_customer_owned_debts_with_shops(
        session, customer_id=authority.customer_id
    )
    return list_own_customer_debt_web_items(
        session,
        authority=authority,
        payment_progress_by_debt_id=list_payment_progress_for_debts(
            session,
            debts=(candidate.debt for candidate in candidates),
            server_now=server_now,
        ),
    )


def get_customer_debt_web_detail_with_payment_progress(
    session: Session,
    *,
    authority: CustomerDebtAuthority,
    debt_id: DebtId,
    language: OfferLanguage,
    server_now: datetime,
) -> CustomerDebtWebDetailResult:
    """Compose an own-customer debt detail without changing its authority path."""

    candidate = get_customer_owned_debt_with_shop(
        session,
        customer_id=authority.customer_id,
        debt_id=debt_id,
    )
    return get_own_customer_debt_web_detail(
        session,
        authority=authority,
        debt_id=debt_id,
        language=language,
        payment_progress_by_debt_id=list_payment_progress_for_debts(
            session,
            debts=() if candidate is None else (candidate.debt,),
            server_now=server_now,
        ),
    )


def compose_payment_receipt(
    session: Session, *, row: ScopedPaymentRow, server_now: datetime | None = None
) -> PaymentReceiptProjection:
    """Immutable receipt fact plus labelled historical/current server balances."""

    payment = row.payment
    debt = row.debt
    aggregate = payment_aggregate_from_row(payment)
    overdue_revision = (
        None if debt.overdue_revision is None else DebtRevision(debt.overdue_revision)
    )
    historical_basis = resolve_historical_balance_basis(
        payment_revision=aggregate.debt_revision_after,
        overdue_revision=overdue_revision,
    )
    current_basis = resolve_current_balance_basis(
        status=DebtStatus(debt.status),
        due_date=debt.due_date,
        server_now=normalize_payment_created_at(server_now or datetime.now(UTC)),
        overdue_revision=overdue_revision,
    )
    return PaymentReceiptProjection(
        amount=aggregate.amount,
        method=aggregate.method,
        created_at=aggregate.created_at,
        historical_balance_after=historical_balance_after(
            session,
            debt=debt,
            payment=payment,
            balance_basis=historical_basis,
        ),
        current_balance=remaining_due(session, debt=debt, balance_basis=current_basis),
        current_debt_status=DebtStatus(debt.status),
        shop_display_name=row.shop_name,
        historical_balance_basis=historical_basis,
        current_balance_basis=current_basis,
    )


def _progress(
    debt: Debt, posted: PostedPaymentTotalUZS, now: datetime
) -> DebtPaymentProgressProjection:
    status = DebtStatus(debt.status)
    overdue_revision = (
        None if debt.overdue_revision is None else DebtRevision(debt.overdue_revision)
    )
    basis = _current_progress_basis(
        status=status,
        due_date=debt.due_date,
        server_now=now,
        overdue_revision=overdue_revision,
    )
    remaining = calculate_remaining_due_for_basis(
        basis=basis,
        original_amount=OriginalAmountUZS(debt.original_amount_uzs),
        discounted_amount=DiscountedAmountUZS(debt.discounted_amount_uzs),
        posted_total=posted,
    )
    payable = status in {DebtStatus.ACTIVE, DebtStatus.OVERDUE} and remaining.value > 0
    return DebtPaymentProgressProjection(
        posted_total_uzs=posted.value,
        remaining_due_uzs=remaining.value,
        status=status,
        paid_at=None
        if debt.paid_at is None
        else debt.paid_at.astimezone(UTC).isoformat(),
        is_payable=payable,
        balance_basis=basis,
        is_effectively_overdue=(
            status is DebtStatus.OVERDUE
            or (status is DebtStatus.ACTIVE and basis is DebtBalanceBasis.ORIGINAL)
        ),
    )


def _current_progress_basis(
    *,
    status: DebtStatus,
    due_date: date,
    server_now: datetime,
    overdue_revision: DebtRevision | None,
) -> DebtBalanceBasis:
    """Use the M15 basis resolver only for statuses with a current balance."""

    if status in {DebtStatus.ACTIVE, DebtStatus.OVERDUE, DebtStatus.PAID}:
        return resolve_current_balance_basis(
            status=status,
            due_date=due_date,
            server_now=server_now,
            overdue_revision=overdue_revision,
        )
    if overdue_revision is not None:
        raise ValueError("Terminal non-paid debt cannot carry overdue revision")
    return DebtBalanceBasis.DISCOUNTED


def _history(rows: Iterable[ScopedPaymentRow]) -> tuple[PaymentHistoryItem, ...]:
    items = tuple(
        payment_aggregate_from_row(row.payment).to_history_item() for row in rows
    )
    revisions = tuple(item.debt_revision_after.value for item in items)
    if any(
        right <= left for left, right in zip(revisions, revisions[1:], strict=False)
    ):
        raise RuntimeError("Payment history revisions are not strictly increasing")
    return items


def _resolve_own_customer_id(
    session: Session, *, authenticated_user: User
) -> UUID | None:
    """Resolve ownership only; repayment history does not depend on eligibility.

    In particular, Customer onboarding, Telegram, blacklist, list and rating
    state are not authority predicates for this historical read surface.
    """

    if not isinstance(authenticated_user, User):
        raise TypeError("authenticated_user must be a User")
    if not isinstance(authenticated_user.id, UUID) or not authenticated_user.is_active:
        return None
    return session.scalar(
        select(Customer.id)
        .join(User, User.id == Customer.user_id)
        .where(User.id == authenticated_user.id, User.is_active.is_(True))
    )
