"""Forward-ordered row locks for deterministic M13 pending expiry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.values import DebtId
from app.shop.repository import lock_shop_for_update
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import lock_shop_customer_by_tenant_locator
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "MAX_DEBT_EXPIRY_BATCH_SIZE",
    "DebtExpiryCandidate",
    "LockedDebtForExpiry",
    "discover_debt_expiry_candidates",
    "lock_debt_for_expiry",
)

MAX_DEBT_EXPIRY_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True, repr=False)
class DebtExpiryCandidate:
    """Non-locking locator; every field is revalidated under forward locks."""

    debt_id: UUID = field(repr=False)
    shop_customer_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)

    def __repr__(self) -> str:
        return "DebtExpiryCandidate(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedDebtForExpiry:
    row: Debt = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedDebtForExpiry(<redacted>)"


def discover_debt_expiry_candidates(
    session: Session, *, now: datetime, batch_size: int
) -> tuple[DebtExpiryCandidate, ...]:
    """Return a bounded, stable page without retaining any ORM entities."""

    _require_aware(now)
    _require_batch_size(batch_size)
    statement = (
        select(Debt.id, ShopCustomer.id, ShopCustomer.shop_id)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(
            Debt.status == DebtStatus.PENDING.value,
            Debt.pending_expires_at <= now,
        )
        .order_by(Debt.pending_expires_at, Debt.id)
        .limit(batch_size)
    )
    return tuple(
        DebtExpiryCandidate(
            debt_id=debt_id,
            shop_customer_id=shop_customer_id,
            shop_id=shop_id,
        )
        for debt_id, shop_customer_id, shop_id in session.execute(statement)
    )


def lock_debt_for_expiry(
    session: Session, *, candidate: DebtExpiryCandidate
) -> LockedDebtForExpiry | None:
    """Lock Shop -> ShopCustomer -> Debt and revalidate the discovered chain."""

    if not isinstance(candidate, DebtExpiryCandidate):
        raise TypeError("candidate must be a DebtExpiryCandidate")
    locked_shop = lock_shop_for_update(session, shop_id=ShopId(candidate.shop_id))
    if locked_shop is None:
        return None
    locked_shop_customer = lock_shop_customer_by_tenant_locator(
        session,
        locked_shop=locked_shop,
        shop_customer_id=ShopCustomerId(candidate.shop_customer_id),
    )
    if locked_shop_customer is None:
        return None
    row = session.scalar(
        select(Debt)
        .where(
            Debt.id == DebtId(candidate.debt_id).as_uuid(),
            Debt.shop_customer_id == locked_shop_customer.row.id,
        )
        .with_for_update()
    )
    if row is None:
        return None
    return LockedDebtForExpiry(row=row, _session=session)


def _validate_locked_expiry_debt(
    session: Session, token: object
) -> LockedDebtForExpiry:
    if not isinstance(token, LockedDebtForExpiry):
        raise TypeError("locked debt must come from the expiry resolver")
    if token._session is not session:
        raise RuntimeError("locked debt belongs to another session")
    return token


def _require_batch_size(batch_size: int) -> None:
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or not 1 <= batch_size <= MAX_DEBT_EXPIRY_BATCH_SIZE
    ):
        raise ValueError("Debt expiry batch size is invalid")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Debt expiry time must be timezone-aware")
