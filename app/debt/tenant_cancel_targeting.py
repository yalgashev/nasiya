"""Tenant-scoped forward locks for staff cancellation of a pending Debt."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.debt.dependencies import DetachedDebtActorAuthority
from app.debt.models import Debt
from app.debt.values import DebtId, ShopId, UserId
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import (
    lock_actor_shop_staff_for_update,
    lock_shop_for_update,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import lock_shop_customer_by_tenant_locator
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "LockedTenantDebtForCancel",
    "TenantDebtCancelLockResult",
    "discover_tenant_debt_for_cancel",
    "lock_tenant_debt_for_cancel",
)


@dataclass(frozen=True, slots=True, repr=False)
class _DiscoveredTenantDebtForCancel:
    debt_id: UUID = field(repr=False)
    shop_customer_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)

    def __repr__(self) -> str:
        return "_DiscoveredTenantDebtForCancel(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedTenantDebtForCancel:
    row: Debt = field(repr=False)
    actor_user_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedTenantDebtForCancel(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantDebtCancelLockResult:
    error: ErrorCode | None
    locked: LockedTenantDebtForCancel | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(self.locked, LockedTenantDebtForCancel):
            raise ValueError("Tenant debt cancel lock result is invalid")
        allowed_errors = {
            None,
            ErrorCode.FORBIDDEN,
            ErrorCode.SHOP_SUSPENDED,
            ErrorCode.DEBT_UNAVAILABLE,
        }
        if self.error not in allowed_errors:
            raise ValueError("Tenant debt cancel error is invalid")


def discover_tenant_debt_for_cancel(
    session: Session,
    *,
    authority: DetachedDebtActorAuthority,
    debt_id: DebtId,
) -> _DiscoveredTenantDebtForCancel | None:
    """Treat Debt ID as an opaque locator under the server current Shop."""

    if not isinstance(authority, DetachedDebtActorAuthority):
        raise TypeError("authority must be detached debt authority")
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    if not authority.is_authenticated:
        return None
    assert authority.current_shop_id is not None
    row = session.execute(
        select(Debt.id, ShopCustomer.id, ShopCustomer.shop_id)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(
            Debt.id == debt_id.as_uuid(),
            ShopCustomer.shop_id == authority.current_shop_id,
        )
    ).one_or_none()
    if row is None:
        return None
    return _DiscoveredTenantDebtForCancel(
        debt_id=row[0],
        shop_customer_id=row[1],
        shop_id=row[2],
    )


def lock_tenant_debt_for_cancel(
    session: Session,
    *,
    authority: DetachedDebtActorAuthority,
    candidate: _DiscoveredTenantDebtForCancel | None,
) -> TenantDebtCancelLockResult:
    """Lock active Shop -> ShopStaff -> actor User -> ShopCustomer -> Debt."""

    if not isinstance(authority, DetachedDebtActorAuthority):
        raise TypeError("authority must be detached debt authority")
    if not authority.is_authenticated:
        return TenantDebtCancelLockResult(error=ErrorCode.FORBIDDEN)
    if candidate is None:
        return TenantDebtCancelLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    assert authority.current_shop_id is not None
    assert authority.actor_user_id is not None
    if candidate.shop_id != authority.current_shop_id:
        return TenantDebtCancelLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    locked_shop = lock_shop_for_update(session, shop_id=ShopId(candidate.shop_id))
    if locked_shop is None:
        return TenantDebtCancelLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    if locked_shop.shop.status != ShopStatus.ACTIVE.value:
        return TenantDebtCancelLockResult(error=ErrorCode.SHOP_SUSPENDED)
    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=UserId(authority.actor_user_id),
    )
    if locked_staff is None or ShopRole(locked_staff.staff.role) not in set(ShopRole):
        return TenantDebtCancelLockResult(error=ErrorCode.FORBIDDEN)
    actor = session.scalar(
        select(User).where(User.id == authority.actor_user_id).with_for_update()
    )
    if actor is None or not actor.is_active:
        return TenantDebtCancelLockResult(error=ErrorCode.FORBIDDEN)
    locked_shop_customer = lock_shop_customer_by_tenant_locator(
        session,
        locked_shop=locked_shop,
        shop_customer_id=ShopCustomerId(candidate.shop_customer_id),
    )
    if locked_shop_customer is None:
        return TenantDebtCancelLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    debt = session.scalar(
        select(Debt)
        .where(
            Debt.id == candidate.debt_id,
            Debt.shop_customer_id == locked_shop_customer.row.id,
        )
        .with_for_update()
    )
    if debt is None:
        return TenantDebtCancelLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    return TenantDebtCancelLockResult(
        error=None,
        locked=LockedTenantDebtForCancel(
            row=debt,
            actor_user_id=actor.id,
            shop_id=locked_shop.shop.id,
            _session=session,
        ),
    )


def _validate_locked_tenant_debt(
    session: Session, token: object
) -> LockedTenantDebtForCancel:
    if not isinstance(token, LockedTenantDebtForCancel):
        raise TypeError("locked debt must come from tenant cancel resolver")
    if token._session is not session:
        raise RuntimeError("locked debt belongs to another session")
    return token
