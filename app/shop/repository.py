from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId, ShopStaffId, UserId


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
