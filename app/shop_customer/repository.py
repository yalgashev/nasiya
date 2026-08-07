"""Tenant-scoped persistence primitives for the bounded M12 relationship."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.customer.repository import (
    _LockedActiveTargetCustomer,
    _validate_locked_active_target_customer,
)
from app.shop.repository import _LockedShop, _validate_locked_shop_token
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    ShopCustomerCreationSnapshot,
    ShopCustomerPolicy,
    ShopCustomerRevision,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import CustomerId, ShopCustomerId

__all__ = (
    "get_shop_customer_by_shop",
    "insert_shop_customer",
    "list_customer_own_shop_customers",
    "list_shop_customers_by_shop",
    "lock_shop_customer_by_pair",
    "lock_shop_customer_by_tenant_locator",
    "update_locked_shop_customer",
)

SHOP_CUSTOMER_PAIR_CONSTRAINT = "uq_shop_customers_shop_id_customer_id"


@dataclass(frozen=True, slots=True, repr=False)
class _LockedShopCustomerPredecessors:
    locked_shop: _LockedShop
    locked_customer: _LockedActiveTargetCustomer
    _session: Session

    def __repr__(self) -> str:
        return (
            "_LockedShopCustomerPredecessors("
            "locked_shop=<redacted>, locked_customer=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _LockedShopCustomer:
    row: ShopCustomer
    locked_shop: _LockedShop
    _session: Session

    def __repr__(self) -> str:
        return "_LockedShopCustomer(row=<redacted>, locked_shop=<redacted>)"


def list_shop_customers_by_shop(
    session: Session,
    *,
    shop_id: ShopId,
) -> list[ShopCustomer]:
    statement = (
        select(ShopCustomer)
        .where(ShopCustomer.shop_id == shop_id)
        .order_by(ShopCustomer.created_at.asc(), ShopCustomer.id.asc())
    )
    return list(session.scalars(statement))


def get_shop_customer_by_shop(
    session: Session,
    *,
    shop_id: ShopId,
    shop_customer_id: ShopCustomerId,
) -> ShopCustomer | None:
    statement = select(ShopCustomer).where(
        ShopCustomer.shop_id == shop_id,
        ShopCustomer.id == shop_customer_id.as_uuid(),
    )
    return session.scalar(statement)


def list_customer_own_shop_customers(
    session: Session,
    *,
    customer_id: CustomerId,
) -> list[ShopCustomer]:
    statement = (
        select(ShopCustomer)
        .where(ShopCustomer.customer_id == customer_id)
        .order_by(ShopCustomer.created_at.asc(), ShopCustomer.id.asc())
    )
    return list(session.scalars(statement))


def _mark_shop_customer_predecessors_locked(
    session: Session,
    *,
    locked_shop: _LockedShop,
    locked_customer: _LockedActiveTargetCustomer,
) -> _LockedShopCustomerPredecessors:
    shop = _validate_locked_shop_token(session, locked_shop)
    customer = _validate_locked_active_target_customer(session, locked_customer)
    return _LockedShopCustomerPredecessors(
        locked_shop=shop,
        locked_customer=customer,
        _session=session,
    )


def lock_shop_customer_by_pair(
    session: Session,
    *,
    locked_predecessors: _LockedShopCustomerPredecessors,
) -> _LockedShopCustomer | None:
    predecessors = _validate_locked_predecessors(session, locked_predecessors)
    statement = (
        select(ShopCustomer)
        .where(
            ShopCustomer.shop_id == predecessors.locked_shop.shop.id,
            ShopCustomer.customer_id == predecessors.locked_customer.customer.id,
        )
        .with_for_update()
    )
    row = session.scalar(statement)
    if row is None:
        return None
    return _LockedShopCustomer(
        row=row,
        locked_shop=predecessors.locked_shop,
        _session=session,
    )


def lock_shop_customer_by_tenant_locator(
    session: Session,
    *,
    locked_shop: _LockedShop,
    shop_customer_id: ShopCustomerId,
) -> _LockedShopCustomer | None:
    shop = _validate_locked_shop_token(session, locked_shop)
    statement = (
        select(ShopCustomer)
        .where(
            ShopCustomer.shop_id == shop.shop.id,
            ShopCustomer.id == shop_customer_id.as_uuid(),
        )
        .with_for_update()
    )
    row = session.scalar(statement)
    if row is None:
        return None
    return _LockedShopCustomer(row=row, locked_shop=shop, _session=session)


def insert_shop_customer(
    session: Session,
    *,
    locked_predecessors: _LockedShopCustomerPredecessors,
    shop_customer_id: ShopCustomerId,
    snapshot: ShopCustomerCreationSnapshot,
    created_by_user_id: UserId,
    now: datetime,
) -> ShopCustomer | None:
    predecessors = _validate_locked_predecessors(session, locked_predecessors)
    if not isinstance(snapshot, ShopCustomerCreationSnapshot):
        raise TypeError("snapshot must be a ShopCustomerCreationSnapshot")
    _validate_uuid(created_by_user_id, field_name="created_by_user_id")
    _validate_aware(now)
    row = ShopCustomer(
        id=shop_customer_id.as_uuid(),
        shop_id=predecessors.locked_shop.shop.id,
        customer_id=predecessors.locked_customer.customer.id,
        credit_limit_uzs=snapshot.policy.credit_limit.value,
        max_open_debts=snapshot.policy.max_open_debts.value,
        list_status=snapshot.policy.list_status.value,
        revision=1,
        created_by_user_id=created_by_user_id,
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        if _constraint_name(exc) == SHOP_CUSTOMER_PAIR_CONSTRAINT:
            return None
        raise
    return row


def update_locked_shop_customer(
    session: Session,
    *,
    locked_shop_customer: _LockedShopCustomer,
    policy: ShopCustomerPolicy,
    revision: ShopCustomerRevision,
    now: datetime,
) -> ShopCustomer:
    token = _validate_locked_shop_customer(session, locked_shop_customer)
    if not isinstance(policy, ShopCustomerPolicy):
        raise TypeError("policy must be a ShopCustomerPolicy")
    if not isinstance(revision, ShopCustomerRevision):
        raise TypeError("revision must be a ShopCustomerRevision")
    _validate_aware(now)
    token.row.credit_limit_uzs = policy.credit_limit.value
    token.row.max_open_debts = policy.max_open_debts.value
    token.row.list_status = policy.list_status.value
    token.row.revision = revision.value
    token.row.updated_at = now
    session.add(token.row)
    return token.row


def _validate_locked_predecessors(
    session: Session,
    token: object,
) -> _LockedShopCustomerPredecessors:
    if not isinstance(token, _LockedShopCustomerPredecessors):
        raise TypeError(
            "locked_predecessors must come from _mark_shop_customer_predecessors_locked"
        )
    if token._session is not session:
        raise RuntimeError(
            "locked_predecessors was created by a different SQLAlchemy session"
        )
    _validate_locked_shop_token(session, token.locked_shop)
    _validate_locked_active_target_customer(session, token.locked_customer)
    return token


def _validate_locked_shop_customer(
    session: Session,
    token: object,
) -> _LockedShopCustomer:
    if not isinstance(token, _LockedShopCustomer):
        raise TypeError(
            "locked_shop_customer must come from a ShopCustomer lock helper"
        )
    if token._session is not session:
        raise RuntimeError(
            "locked_shop_customer was created by a different SQLAlchemy session"
        )
    _validate_locked_shop_token(session, token.locked_shop)
    return token


def _validate_uuid(value: UUID, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID")


def _validate_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Shop customer timestamp must be timezone-aware")


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
