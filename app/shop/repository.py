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
from app.shop_customer.contracts import ShopDefaultCreditPolicy
from app.shop_customer.values import CreditLimitUzbekistanSom, MaxOpenDebts

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
    "get_shop_staff_access",
    "list_active_shop_staff",
    "list_shops_by_ids",
    "list_user_active_staff",
    "lock_actor_shop_staff_for_update",
    "read_locked_shop_defaults",
    "lock_shop_for_update",
    "update_locked_shop_defaults",
)


@dataclass(frozen=True, slots=True)
class _LockedShop:
    shop: Shop
    _session: Session


@dataclass(frozen=True, slots=True, repr=False)
class _LockedActorShopStaff:
    staff: ShopStaff
    locked_shop: _LockedShop
    _session: Session

    def __repr__(self) -> str:
        return "_LockedActorShopStaff(staff=<redacted>, locked_shop=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ShopStaffAccessProjection:
    shop_id: ShopId
    shop_status: ShopStatus
    role: ShopRole
    staff_is_active: bool
    staff_is_revoked: bool
    user_is_active: bool

    @property
    def is_live(self) -> bool:
        return (
            self.staff_is_active and not self.staff_is_revoked and self.user_is_active
        )

    def __repr__(self) -> str:
        return "ShopStaffAccessProjection(<redacted>)"


def get_shop(session: Session, *, shop_id: ShopId) -> Shop | None:
    statement = select(Shop).where(Shop.id == shop_id)
    return session.scalar(statement)


def list_shops_by_ids(session: Session, *, shop_ids: set[ShopId]) -> list[Shop]:
    if not shop_ids:
        return []
    statement = select(Shop).where(Shop.id.in_(shop_ids))
    return list(session.scalars(statement))


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


def get_shop_staff_access(
    session: Session,
    *,
    shop_id: ShopId,
    user_id: UserId,
) -> ShopStaffAccessProjection | None:
    """Canonical non-locking live-authority projection for bounded reads/replays."""

    row = session.execute(
        select(
            Shop.id,
            Shop.status,
            ShopStaff.role,
            ShopStaff.is_active,
            ShopStaff.revoked_at,
            User.is_active,
        )
        .join(ShopStaff, ShopStaff.shop_id == Shop.id)
        .join(User, User.id == ShopStaff.user_id)
        .where(
            Shop.id == shop_id,
            ShopStaff.user_id == user_id,
            User.id == user_id,
        )
    ).one_or_none()
    if row is None:
        return None
    try:
        status = ShopStatus(row.status)
        role = ShopRole(row.role)
    except ValueError:
        return None
    return ShopStaffAccessProjection(
        shop_id=ShopId(row.id),
        shop_status=status,
        role=role,
        staff_is_active=bool(row[3]),
        staff_is_revoked=row.revoked_at is not None,
        user_is_active=bool(row[5]),
    )


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


def read_locked_shop_defaults(
    session: Session,
    *,
    locked_shop: _LockedShop,
) -> ShopDefaultCreditPolicy:
    token = _validate_locked_shop_token(session, locked_shop)
    return ShopDefaultCreditPolicy(
        credit_limit=CreditLimitUzbekistanSom(token.shop.default_credit_limit_uzs),
        max_open_debts=MaxOpenDebts(token.shop.default_max_open_debts),
    )


def lock_actor_shop_staff_for_update(
    session: Session,
    *,
    locked_shop: _LockedShop,
    actor_user_id: UserId,
) -> _LockedActorShopStaff | None:
    shop = _validate_locked_shop_token(session, locked_shop)
    statement = (
        select(ShopStaff)
        .where(
            ShopStaff.shop_id == shop.shop.id,
            ShopStaff.user_id == actor_user_id,
            ShopStaff.is_active.is_(True),
        )
        .with_for_update()
    )
    staff = session.scalar(statement)
    if staff is None:
        return None
    return _LockedActorShopStaff(
        staff=staff,
        locked_shop=shop,
        _session=session,
    )


def update_locked_shop_defaults(
    session: Session,
    *,
    locked_shop: _LockedShop,
    defaults: ShopDefaultCreditPolicy,
    now: datetime,
) -> Shop:
    token = _validate_locked_shop_token(session, locked_shop)
    if not isinstance(defaults, ShopDefaultCreditPolicy):
        raise TypeError("defaults must be a ShopDefaultCreditPolicy")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Shop default update time must be timezone-aware")
    token.shop.default_credit_limit_uzs = defaults.credit_limit.value
    token.shop.default_max_open_debts = defaults.max_open_debts.value
    token.shop.updated_at = now
    session.add(token.shop)
    return token.shop


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


def _validate_locked_actor_shop_staff(
    session: Session,
    locked_staff: object,
) -> _LockedActorShopStaff:
    if not isinstance(locked_staff, _LockedActorShopStaff):
        raise TypeError("locked_staff must come from lock_actor_shop_staff_for_update")
    if locked_staff._session is not session:
        raise RuntimeError("locked_staff was created by a different SQLAlchemy session")
    _validate_locked_shop_token(session, locked_staff.locked_shop)
    return locked_staff
