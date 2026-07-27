from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import clear_session_active_shop_id, set_session_active_shop_id
from app.db import create_database_session_factory
from app.shop.context import resolve_current_shop
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId, UserId

NOW = datetime(2026, 7, 27, 14, 0, tzinfo=UTC)


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


def add_shop(
    session: Session,
    name: str,
    *,
    status: str = ShopStatus.ACTIVE.value,
) -> Shop:
    shop = Shop(name=name, phone=unique_phone(), status=status)
    session.add(shop)
    session.flush()
    return shop


def add_staff(
    session: Session,
    shop: Shop,
    user: User,
    *,
    role: str = ShopRole.CASHIER.value,
    is_active: bool = True,
) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=role,
        is_active=is_active,
        revoked_at=None if is_active else NOW,
    )
    session.add(staff)
    session.flush()
    return staff


def add_auth_session(
    session: Session,
    user: User,
    *,
    active_shop_id=None,
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


def test_shop_context_has_no_http_or_template_imports() -> None:
    source = Path("app/shop/context.py").read_text()

    forbidden_fragments = {"fastapi", "HTTPException", "Request", "Template"}
    assert forbidden_fragments.isdisjoint(source)


@pytest.mark.integration
def test_valid_active_shop_id_resolves_current_shop(db_session: Session) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session, "Selected Shop")
    staff = add_staff(db_session, shop, user, role=ShopRole.OWNER.value)
    auth_session = add_auth_session(db_session, user, active_shop_id=shop.id)

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(user.id),
    )

    assert context.is_selected is True
    assert context.shop is shop
    assert context.staff_id == staff.id
    assert context.role is ShopRole.OWNER
    assert context.status is ShopStatus.ACTIVE
    assert auth_session.active_shop_id == shop.id


@pytest.mark.integration
def test_stale_or_foreign_active_shop_id_is_cleared(db_session: Session) -> None:
    user = add_user(db_session)
    other_user = add_user(db_session)
    foreign_shop = add_shop(db_session, "Foreign Shop")
    add_staff(db_session, foreign_shop, other_user, role=ShopRole.OWNER.value)
    auth_session = add_auth_session(db_session, user, active_shop_id=foreign_shop.id)

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(user.id),
    )

    assert context.is_selected is False
    assert context.shop is None
    assert context.staff_id is None
    assert context.role is None
    assert context.status is None
    assert auth_session.active_shop_id is None


@pytest.mark.integration
def test_revoked_active_shop_membership_is_cleared(db_session: Session) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session, "Revoked Shop")
    add_staff(db_session, shop, user, is_active=False)
    auth_session = add_auth_session(db_session, user, active_shop_id=shop.id)

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(user.id),
    )

    assert context.is_selected is False
    assert auth_session.active_shop_id is None


@pytest.mark.integration
def test_single_active_membership_is_auto_selected(db_session: Session) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session, "Only Shop")
    staff = add_staff(db_session, shop, user)
    auth_session = add_auth_session(db_session, user)

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(user.id),
    )

    assert context.is_selected is True
    assert context.shop is shop
    assert context.staff_id == staff.id
    assert context.role is ShopRole.CASHIER
    assert context.status is ShopStatus.ACTIVE
    assert auth_session.active_shop_id == shop.id


@pytest.mark.integration
def test_zero_active_memberships_remains_unselected(db_session: Session) -> None:
    user = add_user(db_session)
    auth_session = add_auth_session(db_session, user)

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(user.id),
    )

    assert context.is_selected is False
    assert auth_session.active_shop_id is None


@pytest.mark.integration
def test_multiple_active_memberships_remain_unselected(db_session: Session) -> None:
    user = add_user(db_session)
    first_shop = add_shop(db_session, "First Shop")
    second_shop = add_shop(db_session, "Second Shop")
    add_staff(db_session, first_shop, user)
    add_staff(db_session, second_shop, user, role=ShopRole.OWNER.value)
    auth_session = add_auth_session(db_session, user)

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(user.id),
    )

    assert context.is_selected is False
    assert auth_session.active_shop_id is None


@pytest.mark.integration
def test_suspended_shop_single_membership_is_auto_selected(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    shop = add_shop(
        db_session,
        "Suspended Shop",
        status=ShopStatus.SUSPENDED.value,
    )
    staff = add_staff(db_session, shop, user, role=ShopRole.OWNER.value)
    auth_session = add_auth_session(db_session, user)

    context = resolve_current_shop(
        db_session,
        auth_session=auth_session,
        user_id=UserId(user.id),
    )

    assert context.is_selected is True
    assert context.shop is shop
    assert context.staff_id == staff.id
    assert context.role is ShopRole.OWNER
    assert context.status is ShopStatus.SUSPENDED
    assert auth_session.active_shop_id == shop.id


@pytest.mark.integration
def test_active_shop_session_service_set_and_clear_use_orm_row(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session, "Manual Shop")
    auth_session = add_auth_session(db_session, user)

    set_session_active_shop_id(
        db_session,
        auth_session,
        shop_id=ShopId(shop.id),
    )
    db_session.flush()
    assert db_session.get(AuthSession, auth_session.id).active_shop_id == shop.id

    clear_session_active_shop_id(db_session, auth_session)
    db_session.flush()
    assert db_session.get(AuthSession, auth_session.id).active_shop_id is None
