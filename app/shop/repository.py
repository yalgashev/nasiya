"""Shop persistence primitives.

The _LockedShop marker strengthens the correct call order at the API level:
existing-shop mutations first lock the Shop row, then lock staff rows through
that marker. It does not prove that callers avoid commit/rollback between
steps; caller-owned transactions and PostgreSQL concurrency tests are the final
protection.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent
from app.shop.values import ShopId, ShopStaffId, UserId

__all__ = (
    "add_shop",
    "add_shop_staff",
    "add_shop_staff_event",
    "add_shop_status_event",
    "count_active_owners",
    "get_active_staff",
    "get_active_staff_by_id",
    "get_shop",
    "get_shop_for_staff",
    "list_active_shop_staff",
    "list_user_active_staff",
    "lock_shop_for_update",
)


@dataclass(frozen=True, slots=True)
class _LockedShop:
    shop: Shop
    _session: Session


def get_shop(session: Session, *, shop_id: ShopId) -> Shop | None:
    statement = select(Shop).where(Shop.id == shop_id)
    return session.scalar(statement)


def get_active_staff(
    session: Session,
    *,
    shop_id: ShopId,
    user_id: UserId,
) -> ShopStaff | None:
    statement = select(ShopStaff).where(
        ShopStaff.shop_id == shop_id,
        ShopStaff.user_id == user_id,
        ShopStaff.is_active.is_(True),
    )
    return session.scalar(statement)


def get_active_staff_by_id(
    session: Session,
    *,
    shop_id: ShopId,
    staff_id: ShopStaffId,
) -> ShopStaff | None:
    statement = select(ShopStaff).where(
        ShopStaff.shop_id == shop_id,
        ShopStaff.id == staff_id,
        ShopStaff.is_active.is_(True),
    )
    return session.scalar(statement)


def list_user_active_staff(
    session: Session,
    *,
    user_id: UserId,
) -> list[tuple[ShopStaff, Shop]]:
    statement = (
        select(ShopStaff, Shop)
        .join(Shop, Shop.id == ShopStaff.shop_id)
        .where(
            ShopStaff.user_id == user_id,
            ShopStaff.is_active.is_(True),
        )
        .order_by(ShopStaff.created_at.asc(), ShopStaff.id.asc())
    )
    return [(staff, shop) for staff, shop in session.execute(statement).all()]


def get_shop_for_staff(
    session: Session,
    *,
    shop_id: ShopId,
    user_id: UserId,
) -> Shop | None:
    statement = (
        select(Shop)
        .join(ShopStaff, ShopStaff.shop_id == Shop.id)
        .where(
            Shop.id == shop_id,
            ShopStaff.shop_id == shop_id,
            ShopStaff.user_id == user_id,
            ShopStaff.is_active.is_(True),
        )
    )
    return session.scalar(statement)


def list_active_shop_staff(
    session: Session,
    *,
    shop_id: ShopId,
) -> list[tuple[ShopStaff, User]]:
    statement = (
        select(ShopStaff, User)
        .join(User, User.id == ShopStaff.user_id)
        .where(
            ShopStaff.shop_id == shop_id,
            ShopStaff.is_active.is_(True),
        )
        .order_by(ShopStaff.created_at.asc(), ShopStaff.id.asc())
    )
    return [(staff, user) for staff, user in session.execute(statement).all()]


def count_active_owners(session: Session, *, shop_id: ShopId) -> int:
    statement = (
        select(func.count())
        .select_from(ShopStaff)
        .where(
            ShopStaff.shop_id == shop_id,
            ShopStaff.role == ShopRole.OWNER.value,
            ShopStaff.is_active.is_(True),
        )
    )
    return session.scalar(statement) or 0


def add_shop(
    session: Session,
    *,
    shop_id: ShopId,
    name: str,
    phone: str,
    address_text: str | None,
    status: ShopStatus,
    now: datetime,
) -> Shop:
    shop = Shop(
        id=shop_id,
        name=name,
        phone=phone,
        address_text=address_text,
        status=status.value,
        created_at=now,
        updated_at=now,
    )
    session.add(shop)
    return shop


def add_shop_staff(
    session: Session,
    *,
    shop_id: ShopId,
    user_id: UserId,
    role: ShopRole,
    now: datetime,
) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop_id,
        user_id=user_id,
        role=role.value,
        is_active=True,
        created_at=now,
        updated_at=now,
        revoked_at=None,
    )
    session.add(staff)
    return staff


def add_shop_status_event(
    session: Session,
    *,
    shop_id: ShopId,
    action: ShopStatusAction,
    actor_user_id: UserId | None,
    reason: str | None,
    now: datetime,
) -> ShopStatusEvent:
    event = ShopStatusEvent(
        shop_id=shop_id,
        action=action.value,
        actor_user_id=actor_user_id,
        reason=reason,
        created_at=now,
    )
    session.add(event)
    return event


def add_shop_staff_event(
    session: Session,
    *,
    shop_id: ShopId,
    subject_user_id: UserId,
    action: ShopStaffAction,
    old_role: ShopRole | None,
    new_role: ShopRole | None,
    actor_user_id: UserId | None,
    now: datetime,
) -> ShopStaffEvent:
    event = ShopStaffEvent(
        shop_id=shop_id,
        subject_user_id=subject_user_id,
        action=action.value,
        old_role=old_role.value if old_role is not None else None,
        new_role=new_role.value if new_role is not None else None,
        actor_user_id=actor_user_id,
        created_at=now,
    )
    session.add(event)
    return event


def lock_shop_for_update(
    session: Session,
    *,
    shop_id: ShopId,
) -> _LockedShop | None:
    statement = select(Shop).where(Shop.id == shop_id).with_for_update()
    shop = session.scalar(statement)
    if shop is None:
        return None
    return _LockedShop(shop=shop, _session=session)


def _lock_active_staff_by_id_for_update(
    session: Session,
    *,
    locked_shop: _LockedShop,
    staff_id: ShopStaffId,
) -> ShopStaff | None:
    token = _validate_locked_shop_token(session, locked_shop)
    statement = (
        select(ShopStaff)
        .where(
            ShopStaff.shop_id == token.shop.id,
            ShopStaff.id == staff_id,
            ShopStaff.is_active.is_(True),
        )
        .with_for_update()
    )
    return session.scalar(statement)


def _lock_staff_for_user_for_update(
    session: Session,
    *,
    locked_shop: _LockedShop,
    user_id: UserId,
) -> ShopStaff | None:
    token = _validate_locked_shop_token(session, locked_shop)
    statement = (
        select(ShopStaff)
        .where(
            ShopStaff.shop_id == token.shop.id,
            ShopStaff.user_id == user_id,
        )
        .with_for_update()
    )
    return session.scalar(statement)


def _validate_locked_shop_token(
    session: Session,
    locked_shop: object,
) -> _LockedShop:
    if not isinstance(locked_shop, _LockedShop):
        raise TypeError("locked_shop must come from lock_shop_for_update")
    if locked_shop._session is not session:
        raise RuntimeError("locked_shop was created by a different SQLAlchemy session")
    return locked_shop
