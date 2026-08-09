import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
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


def test_repository_identifiers_are_keyword_only() -> None:
    functions = (
        repository.add_shop,
        repository.add_shop_staff,
        repository.add_shop_staff_event,
        repository.add_shop_status_event,
        repository.get_shop,
        repository.get_active_staff,
        repository.get_active_staff_by_id,
        repository.list_user_active_staff,
        repository.get_shop_for_staff,
        repository.get_shop_staff_access,
        repository.list_active_shop_staff,
        repository.count_active_owners,
        repository.lock_shop_for_update,
        repository.lock_actor_shop_staff_for_update,
        repository.read_locked_shop_defaults,
        repository.update_locked_shop_defaults,
        repository._lock_active_staff_by_id_for_update,
        repository._lock_staff_for_user_for_update,
    )

    for function in functions:
        parameters = list(inspect.signature(function).parameters.values())

        assert parameters[0].name == "session"
        assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in parameters[1:]
        )


def test_repository_has_no_transaction_or_http_boundary() -> None:
    source = Path(repository.__file__).read_text()

    forbidden_fragments = {
        ".commit(",
        ".rollback(",
        ".flush(",
        "HTTPException",
        " update(",
        " delete(",
    }
    assert forbidden_fragments.isdisjoint(source)


def test_repository_public_api_excludes_private_lock_helpers() -> None:
    assert repository.__all__ == (
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
    assert "_LockedShop" not in repository.__all__
    assert "_lock_active_staff_by_id_for_update" not in repository.__all__
    assert "_lock_staff_for_user_for_update" not in repository.__all__


def test_staff_lock_helpers_require_locked_shop_marker_not_shop_id() -> None:
    for helper in (
        repository._lock_active_staff_by_id_for_update,
        repository._lock_staff_for_user_for_update,
    ):
        signature = inspect.signature(helper)
        assert "shop_id" not in signature.parameters
        assert "locked_shop" in signature.parameters

        source = inspect.getsource(helper)
        assert "select(Shop)" not in source
        assert "with_for_update" in source


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


@pytest.mark.integration
def test_lock_shop_for_update_returns_marker_and_takes_real_row_lock(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as setup_session:
        shop = add_shop(setup_session, "Locked Shop")
        shop_id = shop.id
        setup_session.commit()

    locking_session = session_factory()
    competing_session = session_factory()
    try:
        locked_shop = repository.lock_shop_for_update(
            locking_session,
            shop_id=ShopId(shop_id),
        )

        assert locked_shop is not None
        assert locked_shop.shop.id == shop_id
        assert_shop_row_is_locked_by_other_transaction(competing_session, shop_id)
    finally:
        competing_session.rollback()
        competing_session.close()
        locking_session.rollback()
        locking_session.close()


@pytest.mark.integration
def test_staff_lock_helper_takes_real_row_lock_after_shop_lock(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as setup_session:
        user = add_user(setup_session)
        shop = add_shop(setup_session, "Staff Locked Shop")
        staff = add_staff(setup_session, shop, user)
        shop_id = shop.id
        staff_id = staff.id
        setup_session.commit()

    locking_session = session_factory()
    competing_session = session_factory()
    try:
        locked_shop = repository.lock_shop_for_update(
            locking_session,
            shop_id=ShopId(shop_id),
        )
        assert locked_shop is not None

        locked_staff = repository._lock_active_staff_by_id_for_update(
            locking_session,
            locked_shop=locked_shop,
            staff_id=ShopStaffId(staff_id),
        )

        assert locked_staff is not None
        assert locked_staff.id == staff_id
        assert_staff_row_is_locked_by_other_transaction(competing_session, staff_id)
    finally:
        competing_session.rollback()
        competing_session.close()
        locking_session.rollback()
        locking_session.close()


@pytest.mark.integration
def test_staff_lock_helpers_reject_raw_shop_id(db_session: Session) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session, "Raw ShopId Shop")
    staff = add_staff(db_session, shop, user)

    with pytest.raises(TypeError, match="lock_shop_for_update"):
        repository._lock_active_staff_by_id_for_update(
            db_session,
            locked_shop=ShopId(shop.id),
            staff_id=ShopStaffId(staff.id),
        )

    with pytest.raises(TypeError, match="lock_shop_for_update"):
        repository._lock_staff_for_user_for_update(
            db_session,
            locked_shop=ShopId(shop.id),
            user_id=UserId(user.id),
        )


@pytest.mark.integration
def test_locked_shop_token_from_other_session_is_rejected(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session, "Other Session Shop")
    staff = add_staff(db_session, shop, user)
    locked_shop = repository.lock_shop_for_update(
        db_session,
        shop_id=ShopId(shop.id),
    )
    assert locked_shop is not None

    session_factory = create_database_session_factory(m2_test_database)
    other_session = session_factory()
    try:
        with pytest.raises(RuntimeError, match="different SQLAlchemy session"):
            repository._lock_active_staff_by_id_for_update(
                other_session,
                locked_shop=locked_shop,
                staff_id=ShopStaffId(staff.id),
            )
        with pytest.raises(RuntimeError, match="different SQLAlchemy session"):
            repository._lock_staff_for_user_for_update(
                other_session,
                locked_shop=locked_shop,
                user_id=UserId(user.id),
            )
    finally:
        other_session.rollback()
        other_session.close()


@pytest.mark.integration
def test_staff_lock_for_user_returns_active_or_revoked_staff(
    db_session: Session,
) -> None:
    active_user = add_user(db_session)
    revoked_user = add_user(db_session)
    other_user = add_user(db_session)
    shop = add_shop(db_session, "Staff For User Shop")
    other_shop = add_shop(db_session, "Other Staff For User Shop")
    active_staff = add_staff(db_session, shop, active_user)
    revoked_staff = add_staff(db_session, shop, revoked_user, is_active=False)
    add_staff(db_session, other_shop, other_user)

    locked_shop = repository.lock_shop_for_update(
        db_session,
        shop_id=ShopId(shop.id),
    )
    assert locked_shop is not None

    assert (
        repository._lock_staff_for_user_for_update(
            db_session,
            locked_shop=locked_shop,
            user_id=UserId(active_user.id),
        )
        is active_staff
    )
    assert (
        repository._lock_staff_for_user_for_update(
            db_session,
            locked_shop=locked_shop,
            user_id=UserId(revoked_user.id),
        )
        is revoked_staff
    )
    assert (
        repository._lock_staff_for_user_for_update(
            db_session,
            locked_shop=locked_shop,
            user_id=UserId(other_user.id),
        )
        is None
    )
    assert (
        repository._lock_active_staff_by_id_for_update(
            db_session,
            locked_shop=locked_shop,
            staff_id=ShopStaffId(revoked_staff.id),
        )
        is None
    )


def assert_shop_row_is_locked_by_other_transaction(
    session: Session,
    shop_id,
) -> None:
    with pytest.raises(OperationalError):
        session.execute(
            select(Shop.id).where(Shop.id == shop_id).with_for_update(nowait=True)
        ).all()
    session.rollback()


def assert_staff_row_is_locked_by_other_transaction(
    session: Session,
    staff_id,
) -> None:
    with pytest.raises(OperationalError):
        session.execute(
            select(ShopStaff.id)
            .where(ShopStaff.id == staff_id)
            .with_for_update(nowait=True)
        ).all()
    session.rollback()
