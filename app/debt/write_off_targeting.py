"""Detached admin write-off discovery and exact forward-lock targeting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import Customer
from app.debt.commands import WriteOffDebtCommand
from app.debt.contracts import WriteOffReason
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.repository import (
    LockedWriteOffDebt,
    WrittenOffCandidateLocator,
    discover_written_off_candidates,
    lock_scoped_write_off_debt,
)
from app.debt.values import DebtId, DebtRevision, ShopCustomerId
from app.offers.authorization import PlatformAdminActor, assert_platform_admin_actor
from app.shop.repository import _LockedShop, lock_shop_for_update
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import (
    _LockedShopCustomer,
    lock_shop_customer_by_tenant_locator,
)

__all__ = (
    "AdminWriteOffCompletedProjection",
    "AdminWriteOffTarget",
    "LockedAdminWriteOffPredecessors",
    "discover_admin_write_off_target",
    "list_admin_write_off_candidates",
    "lock_admin_write_off_predecessors",
    "lock_admin_write_off_debt",
    "read_admin_completed_write_off",
)


@dataclass(frozen=True, slots=True, repr=False)
class AdminWriteOffTarget:
    shop_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    target_user_id: UUID = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    revision: DebtRevision

    def __repr__(self) -> str:
        return "AdminWriteOffTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedAdminWriteOffPredecessors:
    target: AdminWriteOffTarget = field(repr=False)
    locked_shop: _LockedShop = field(repr=False)
    customer: Customer = field(repr=False)
    locked_shop_customer: _LockedShopCustomer = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedAdminWriteOffPredecessors(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AdminWriteOffCompletedProjection:
    status: DebtStatus
    reason: WriteOffReason = field(repr=False)
    written_off_at: datetime

    def __repr__(self) -> str:
        return "AdminWriteOffCompletedProjection(<redacted>)"


def list_admin_write_off_candidates(
    session: Session, *, actor: PlatformAdminActor
) -> tuple[WrittenOffCandidateLocator, ...]:
    _require_live_admin(session, actor)
    return discover_written_off_candidates(session)


def discover_admin_write_off_target(
    session: Session,
    *,
    actor: PlatformAdminActor,
    debt_id: DebtId,
) -> AdminWriteOffTarget | None:
    """Read detached scalar identity only for a fresh persisted-overdue target."""

    _require_live_admin(session, actor)
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    row = session.execute(
        select(
            ShopCustomer.shop_id,
            ShopCustomer.customer_id,
            Customer.user_id,
            ShopCustomer.id,
            Debt.id,
            Debt.revision,
        )
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .where(
            Debt.id == debt_id.as_uuid(),
            Debt.status == DebtStatus.OVERDUE.value,
            Debt.overdue_at.is_not(None),
            Debt.overdue_revision.is_not(None),
        )
    ).one_or_none()
    if row is None:
        return None
    return AdminWriteOffTarget(
        shop_id=row.shop_id,
        customer_id=row.customer_id,
        target_user_id=row.user_id,
        shop_customer_id=ShopCustomerId(row.id),
        debt_id=DebtId(row[4]),
        revision=DebtRevision(row.revision),
    )


def lock_admin_write_off_predecessors(
    session: Session,
    *,
    command: WriteOffDebtCommand,
    target: AdminWriteOffTarget | None,
) -> LockedAdminWriteOffPredecessors | None:
    """Lock target Shop -> actor User -> Customer -> ShopCustomer."""

    if not isinstance(command, WriteOffDebtCommand):
        raise TypeError("command must be a WriteOffDebtCommand")
    if target is None or target.debt_id != command.debt_id:
        return None
    locked_shop = lock_shop_for_update(session, shop_id=ShopId(target.shop_id))
    if locked_shop is None:
        return None
    assert_platform_admin_actor(session, command.actor)
    customer = session.scalar(
        select(Customer)
        .where(
            Customer.id == target.customer_id,
            Customer.user_id == target.target_user_id,
        )
        .with_for_update()
    )
    if customer is None:
        return None
    locked_shop_customer = lock_shop_customer_by_tenant_locator(
        session,
        locked_shop=locked_shop,
        shop_customer_id=target.shop_customer_id,
    )
    if (
        locked_shop_customer is None
        or locked_shop_customer.row.customer_id != customer.id
    ):
        return None
    return LockedAdminWriteOffPredecessors(
        target=target,
        locked_shop=locked_shop,
        customer=customer,
        locked_shop_customer=locked_shop_customer,
        _session=session,
    )


def lock_admin_write_off_debt(
    session: Session,
    *,
    predecessors: LockedAdminWriteOffPredecessors,
) -> LockedWriteOffDebt | None:
    if not isinstance(predecessors, LockedAdminWriteOffPredecessors):
        raise TypeError("predecessors must be a locked admin write-off target")
    if predecessors._session is not session:
        raise RuntimeError("write-off predecessors belong to another session")
    target = predecessors.target
    return lock_scoped_write_off_debt(
        session,
        shop_id=target.shop_id,
        customer_id=target.customer_id,
        shop_customer_id=target.shop_customer_id,
        debt_id=target.debt_id,
    )


def read_admin_completed_write_off(
    session: Session,
    *,
    actor: PlatformAdminActor,
    debt_id: DebtId,
) -> AdminWriteOffCompletedProjection | None:
    """Read the original actor's immutable completed view without locks/clocks."""

    _require_live_admin(session, actor)
    row = session.execute(
        select(Debt.status, Debt.written_off_reason, Debt.written_off_at).where(
            Debt.id == debt_id.as_uuid(),
            Debt.status.in_(
                (DebtStatus.WRITTEN_OFF.value, DebtStatus.WRITTEN_OFF_SETTLED.value)
            ),
            Debt.written_off_actor_user_id == actor.user_id,
        )
    ).one_or_none()
    if row is None or row.written_off_reason is None or row.written_off_at is None:
        return None
    try:
        return AdminWriteOffCompletedProjection(
            status=DebtStatus(row.status),
            reason=WriteOffReason(row.written_off_reason),
            written_off_at=row.written_off_at,
        )
    except ValueError:
        return None


def _require_live_admin(session: Session, actor: PlatformAdminActor) -> None:
    if not isinstance(actor, PlatformAdminActor):
        raise TypeError("actor must be a PlatformAdminActor")
    live = session.scalar(
        select(User.id).where(
            User.id == actor.user_id,
            User.is_active.is_(True),
            User.is_platform_admin.is_(True),
        )
    )
    if live is None:
        raise PermissionError("Write-off target is unavailable")
