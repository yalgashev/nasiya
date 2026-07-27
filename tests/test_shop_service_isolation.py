from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop import repository
from app.shop.context import resolve_current_shop
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId, ShopStaffId, UserId

NOW = datetime(2026, 7, 27, 16, 0, tzinfo=UTC)


@dataclass(frozen=True)
class CrossShopRows:
    shop_a: Shop
    shop_b: Shop
    a_owner_user: User
    a_cashier_user: User
    b_owner_user: User
    b_cashier_user: User
    a_owner_staff: ShopStaff
    a_cashier_staff: ShopStaff
    b_owner_staff: ShopStaff
    b_cashier_staff: ShopStaff


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def unique_phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def unique_token_hash() -> str:
    return f"{uuid4().hex}{uuid4().hex}"


def add_user(session: Session) -> User:
    user = User(phone=unique_phone())
    session.add(user)
    session.flush()
    return user


def add_shop(session: Session, name: str) -> Shop:
    shop = Shop(name=name, phone=unique_phone())
    session.add(shop)
    session.flush()
    return shop


def add_staff(
    session: Session,
    shop: Shop,
    user: User,
    *,
    role: ShopRole,
) -> ShopStaff:
    staff = ShopStaff(shop_id=shop.id, user_id=user.id, role=role.value)
    session.add(staff)
    session.flush()
    return staff


def add_auth_session(
    session: Session,
    user: User,
    *,
    active_shop_id,
) -> AuthSession:
    auth_session = AuthSession(
        user_id=user.id,
        active_shop_id=active_shop_id,
        token_hash=unique_token_hash(),
        csrf_secret=uuid4().hex,
        expires_at=NOW + timedelta(hours=1),
    )
    session.add(auth_session)
    session.flush()
    return auth_session


@pytest.fixture
def cross_shop_rows(db_session: Session) -> CrossShopRows:
    shop_a = add_shop(db_session, "Shop A")
    shop_b = add_shop(db_session, "Shop B")
    a_owner_user = add_user(db_session)
    a_cashier_user = add_user(db_session)
    b_owner_user = add_user(db_session)
    b_cashier_user = add_user(db_session)

    return CrossShopRows(
        shop_a=shop_a,
        shop_b=shop_b,
        a_owner_user=a_owner_user,
        a_cashier_user=a_cashier_user,
        b_owner_user=b_owner_user,
        b_cashier_user=b_cashier_user,
        a_owner_staff=add_staff(
            db_session,
            shop_a,
            a_owner_user,
            role=ShopRole.OWNER,
        ),
        a_cashier_staff=add_staff(
            db_session,
            shop_a,
            a_cashier_user,
            role=ShopRole.CASHIER,
        ),
        b_owner_staff=add_staff(
            db_session,
            shop_b,
            b_owner_user,
            role=ShopRole.OWNER,
        ),
        b_cashier_staff=add_staff(
            db_session,
            shop_b,
            b_cashier_user,
            role=ShopRole.CASHIER,
        ),
    )


@pytest.mark.integration
def test_get_shop_for_staff_returns_none_for_cross_shop_user(
    db_session: Session,
    cross_shop_rows: CrossShopRows,
) -> None:
    assert (
        repository.get_shop_for_staff(
            db_session,
            shop_id=ShopId(cross_shop_rows.shop_b.id),
            user_id=UserId(cross_shop_rows.a_owner_user.id),
        )
        is None
    )


@pytest.mark.integration
def test_get_active_staff_returns_none_for_cross_shop_user(
    db_session: Session,
    cross_shop_rows: CrossShopRows,
) -> None:
    assert (
        repository.get_active_staff(
            db_session,
            shop_id=ShopId(cross_shop_rows.shop_b.id),
            user_id=UserId(cross_shop_rows.a_owner_user.id),
        )
        is None
    )


@pytest.mark.integration
def test_get_active_staff_by_id_returns_none_for_cross_shop_staff_id(
    db_session: Session,
    cross_shop_rows: CrossShopRows,
) -> None:
    assert (
        repository.get_active_staff_by_id(
            db_session,
            shop_id=ShopId(cross_shop_rows.shop_a.id),
            staff_id=ShopStaffId(cross_shop_rows.b_cashier_staff.id),
        )
        is None
    )


@pytest.mark.integration
def test_list_active_shop_staff_returns_only_requested_shop_staff(
    db_session: Session,
    cross_shop_rows: CrossShopRows,
) -> None:
    rows = repository.list_active_shop_staff(
        db_session,
        shop_id=ShopId(cross_shop_rows.shop_a.id),
    )

    assert rows == [
        (cross_shop_rows.a_owner_staff, cross_shop_rows.a_owner_user),
        (cross_shop_rows.a_cashier_staff, cross_shop_rows.a_cashier_user),
    ]


@pytest.mark.integration
def test_list_user_active_staff_returns_only_real_user_memberships(
    db_session: Session,
    cross_shop_rows: CrossShopRows,
) -> None:
    rows = repository.list_user_active_staff(
        db_session,
        user_id=UserId(cross_shop_rows.a_owner_user.id),
    )

    assert rows == [(cross_shop_rows.a_owner_staff, cross_shop_rows.shop_a)]


@pytest.mark.integration
def test_count_active_owners_does_not_mix_shops(
    db_session: Session,
    cross_shop_rows: CrossShopRows,
) -> None:
    assert (
        repository.count_active_owners(
            db_session,
            shop_id=ShopId(cross_shop_rows.shop_a.id),
        )
        == 1
    )
    assert (
        repository.count_active_owners(
            db_session,
            shop_id=ShopId(cross_shop_rows.shop_b.id),
        )
        == 1
    )


@pytest.mark.integration
def test_poisoned_session_active_shop_id_is_not_resolved_cross_shop(
    db_session: Session,
    cross_shop_rows: CrossShopRows,
) -> None:
    auth_session = add_auth_session(
        db_session,
        cross_shop_rows.a_owner_user,
        active_shop_id=cross_shop_rows.shop_b.id,
    )

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(cross_shop_rows.a_owner_user.id),
    )

    assert context.is_selected is False
    assert context.shop is None
    assert context.staff_id is None
    assert context.role is None
    assert context.status is None
    assert auth_session.active_shop_id is None
