import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.shop import repository
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId, ShopStaffId, UserId

NOW = datetime(2026, 7, 27, 13, 0, tzinfo=UTC)


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
    role: str = "cashier",
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


def test_read_repository_identifiers_are_keyword_only() -> None:
    functions = (
        repository.get_shop,
        repository.get_active_staff,
        repository.get_active_staff_by_id,
        repository.list_user_active_staff,
        repository.get_shop_for_staff,
        repository.list_active_shop_staff,
        repository.count_active_owners,
    )

    for function in functions:
        parameters = list(inspect.signature(function).parameters.values())

        assert parameters[0].name == "session"
        assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in parameters[1:]
        )


def test_read_repository_has_no_write_http_or_lock_boundary() -> None:
    source = Path(repository.__file__).read_text()

    forbidden_fragments = {
        ".commit(",
        ".rollback(",
        ".flush(",
        ".add(",
        "HTTPException",
        "with_for_update",
        " update(",
        " delete(",
    }
    assert forbidden_fragments.isdisjoint(source)


@pytest.mark.integration
def test_get_shop_returns_shop_by_id_or_none(db_session: Session) -> None:
    shop = add_shop(db_session, "Main Shop")

    assert repository.get_shop(db_session, shop_id=ShopId(shop.id)) is shop
    assert repository.get_shop(db_session, shop_id=ShopId(uuid4())) is None


@pytest.mark.integration
def test_get_active_staff_is_shop_scoped_and_active_only(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    first_shop = add_shop(db_session, "First Shop")
    second_shop = add_shop(db_session, "Second Shop")
    first_staff = add_staff(db_session, first_shop, user)
    second_staff = add_staff(db_session, second_shop, user, role="owner")
    inactive_user = add_user(db_session)
    add_staff(db_session, first_shop, inactive_user, is_active=False)

    assert (
        repository.get_active_staff(
            db_session,
            shop_id=ShopId(first_shop.id),
            user_id=UserId(user.id),
        )
        is first_staff
    )
    assert (
        repository.get_active_staff(
            db_session,
            shop_id=ShopId(second_shop.id),
            user_id=UserId(user.id),
        )
        is second_staff
    )
    assert (
        repository.get_active_staff(
            db_session,
            shop_id=ShopId(first_shop.id),
            user_id=UserId(inactive_user.id),
        )
        is None
    )


@pytest.mark.integration
def test_get_active_staff_by_id_cannot_cross_shop_boundary(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    first_shop = add_shop(db_session, "First Shop")
    second_shop = add_shop(db_session, "Second Shop")
    first_staff = add_staff(db_session, first_shop, user)
    inactive_staff = add_staff(db_session, second_shop, user, is_active=False)

    assert (
        repository.get_active_staff_by_id(
            db_session,
            shop_id=ShopId(first_shop.id),
            staff_id=ShopStaffId(first_staff.id),
        )
        is first_staff
    )
    assert (
        repository.get_active_staff_by_id(
            db_session,
            shop_id=ShopId(second_shop.id),
            staff_id=ShopStaffId(first_staff.id),
        )
        is None
    )
    assert (
        repository.get_active_staff_by_id(
            db_session,
            shop_id=ShopId(second_shop.id),
            staff_id=ShopStaffId(inactive_staff.id),
        )
        is None
    )


@pytest.mark.integration
def test_list_user_active_staff_is_user_scoped_without_shop_filter(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    other_user = add_user(db_session)
    first_shop = add_shop(db_session, "First Shop")
    second_shop = add_shop(db_session, "Second Shop")
    inactive_shop = add_shop(db_session, "Inactive Shop")
    first_staff = add_staff(db_session, first_shop, user)
    second_staff = add_staff(db_session, second_shop, user, role="owner")
    add_staff(db_session, inactive_shop, user, is_active=False)
    add_staff(db_session, first_shop, other_user, role="owner")

    rows = repository.list_user_active_staff(db_session, user_id=UserId(user.id))

    assert rows == [(first_staff, first_shop), (second_staff, second_shop)]


@pytest.mark.integration
def test_get_shop_for_staff_requires_active_staff_in_requested_shop(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    inactive_user = add_user(db_session)
    first_shop = add_shop(db_session, "First Shop")
    second_shop = add_shop(db_session, "Second Shop")
    add_staff(db_session, first_shop, user)
    add_staff(db_session, second_shop, inactive_user, is_active=False)

    assert (
        repository.get_shop_for_staff(
            db_session,
            shop_id=ShopId(first_shop.id),
            user_id=UserId(user.id),
        )
        is first_shop
    )
    assert (
        repository.get_shop_for_staff(
            db_session,
            shop_id=ShopId(second_shop.id),
            user_id=UserId(user.id),
        )
        is None
    )
    assert (
        repository.get_shop_for_staff(
            db_session,
            shop_id=ShopId(second_shop.id),
            user_id=UserId(inactive_user.id),
        )
        is None
    )


@pytest.mark.integration
def test_list_active_shop_staff_is_shop_scoped_and_active_only(
    db_session: Session,
) -> None:
    first_user = add_user(db_session)
    second_user = add_user(db_session)
    inactive_user = add_user(db_session)
    other_user = add_user(db_session)
    first_shop = add_shop(db_session, "First Shop")
    second_shop = add_shop(db_session, "Second Shop")
    first_staff = add_staff(db_session, first_shop, first_user, role="owner")
    second_staff = add_staff(db_session, first_shop, second_user)
    add_staff(db_session, first_shop, inactive_user, is_active=False)
    add_staff(db_session, second_shop, other_user, role="owner")

    rows = repository.list_active_shop_staff(
        db_session,
        shop_id=ShopId(first_shop.id),
    )

    assert rows == [(first_staff, first_user), (second_staff, second_user)]


@pytest.mark.integration
def test_count_active_owners_is_shop_scoped_and_active_only(
    db_session: Session,
) -> None:
    first_shop = add_shop(db_session, "First Shop")
    second_shop = add_shop(db_session, "Second Shop")
    owner = add_user(db_session)
    cashier = add_user(db_session)
    inactive_owner = add_user(db_session)
    other_shop_owner = add_user(db_session)
    add_staff(db_session, first_shop, owner, role="owner")
    add_staff(db_session, first_shop, cashier, role="cashier")
    add_staff(db_session, first_shop, inactive_owner, role="owner", is_active=False)
    add_staff(db_session, second_shop, other_shop_owner, role="owner")

    assert (
        repository.count_active_owners(db_session, shop_id=ShopId(first_shop.id)) == 1
    )
    assert (
        repository.count_active_owners(db_session, shop_id=ShopId(second_shop.id)) == 1
    )
