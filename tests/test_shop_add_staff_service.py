from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop import repository
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus
from app.shop.models import Shop, ShopStaff, ShopStaffEvent
from app.shop.service import AddStaffOutcome, add_staff
from app.shop.values import ShopId, UserId

NOW = datetime(2026, 7, 27, 19, 0, tzinfo=UTC)


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


def add_user(session: Session, *, phone: str | None = None) -> User:
    user = User(phone=phone or unique_phone())
    session.add(user)
    session.flush()
    return user


def add_shop_row(
    session: Session,
    *,
    status: ShopStatus = ShopStatus.ACTIVE,
) -> Shop:
    shop = Shop(
        name="Service Shop",
        phone=unique_phone(),
        status=status.value,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(shop)
    session.flush()
    return shop


def add_staff_row(
    session: Session,
    shop: Shop,
    user: User,
    *,
    role: ShopRole = ShopRole.CASHIER,
    is_active: bool = True,
) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=role.value,
        is_active=is_active,
        created_at=NOW,
        updated_at=NOW,
        revoked_at=None if is_active else NOW,
    )
    session.add(staff)
    session.flush()
    return staff


def add_owner_actor(session: Session, shop: Shop) -> User:
    owner = add_user(session)
    add_staff_row(session, shop, owner, role=ShopRole.OWNER)
    return owner


@pytest.mark.integration
def test_add_staff_creates_new_active_staff_and_added_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    owner = add_owner_actor(db_session, shop)
    target_user = add_user(db_session, phone="+998901234567")

    result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone="90 123-45-67",
        role=ShopRole.CASHIER,
        now=NOW,
    )

    assert result.succeeded is True
    assert result.error is None
    assert result.staff is not None
    assert result.staff.outcome is AddStaffOutcome.ADDED
    assert result.staff.role is ShopRole.CASHIER
    assert not hasattr(result.staff, "phone")

    staff = db_session.get(ShopStaff, result.staff.staff_id)
    assert staff is not None
    assert staff.shop_id == shop.id
    assert staff.user_id == target_user.id
    assert staff.role == ShopRole.CASHIER.value
    assert staff.is_active is True
    assert staff.revoked_at is None

    event = only_staff_event(db_session)
    assert event.shop_id == shop.id
    assert event.subject_user_id == target_user.id
    assert event.action == ShopStaffAction.ADDED.value
    assert event.old_role is None
    assert event.new_role == ShopRole.CASHIER.value
    assert event.actor_user_id == owner.id
    assert event.created_at == NOW


@pytest.mark.integration
def test_add_staff_active_existing_staff_is_no_op_without_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    owner = add_owner_actor(db_session, shop)
    target_user = add_user(db_session, phone="+998901234568")
    existing_staff = add_staff_row(
        db_session,
        shop,
        target_user,
        role=ShopRole.CASHIER,
    )

    result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone="+998901234568",
        role=ShopRole.OWNER,
        now=NOW,
    )

    assert result.succeeded is True
    assert result.staff is not None
    assert result.staff.staff_id == existing_staff.id
    assert result.staff.outcome is AddStaffOutcome.ALREADY_ACTIVE
    assert result.staff.role is ShopRole.CASHIER

    db_session.refresh(existing_staff)
    assert existing_staff.role == ShopRole.CASHIER.value
    assert count_rows(db_session, ShopStaff) == 2
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_add_staff_reactivates_revoked_staff_without_new_row(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    owner = add_owner_actor(db_session, shop)
    target_user = add_user(db_session, phone="+998901234569")
    revoked_staff = add_staff_row(
        db_session,
        shop,
        target_user,
        role=ShopRole.CASHIER,
        is_active=False,
    )

    result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone="+998901234569",
        role=ShopRole.MANAGER,
        now=NOW,
    )

    assert result.succeeded is True
    assert result.staff is not None
    assert result.staff.staff_id == revoked_staff.id
    assert result.staff.outcome is AddStaffOutcome.REACTIVATED
    assert result.staff.role is ShopRole.MANAGER

    db_session.refresh(revoked_staff)
    assert revoked_staff.is_active is True
    assert revoked_staff.revoked_at is None
    assert revoked_staff.role == ShopRole.MANAGER.value
    assert revoked_staff.updated_at.tzinfo is not None
    assert count_rows(db_session, ShopStaff) == 2

    event = only_staff_event(db_session)
    assert event.action == ShopStaffAction.ADDED.value
    assert event.subject_user_id == target_user.id
    assert event.new_role == ShopRole.MANAGER.value


@pytest.mark.integration
def test_add_staff_requires_active_owner_actor(db_session: Session) -> None:
    shop = add_shop_row(db_session)
    cashier = add_user(db_session)
    add_staff_row(db_session, shop, cashier, role=ShopRole.CASHIER)
    target_user = add_user(db_session, phone="+998901234570")

    result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(cashier.id),
        phone=target_user.phone,
        role=ShopRole.CASHIER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.FORBIDDEN
    assert count_rows(db_session, ShopStaff) == 1
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_add_staff_missing_shop_is_forbidden(db_session: Session) -> None:
    actor = add_user(db_session)

    result = add_staff(
        db_session,
        shop_id=ShopId(uuid4()),
        actor_user_id=UserId(actor.id),
        phone="+998901234571",
        role=ShopRole.CASHIER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.FORBIDDEN
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_add_staff_rejects_suspended_shop_mutation(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session, status=ShopStatus.SUSPENDED)
    owner = add_owner_actor(db_session, shop)
    target_user = add_user(db_session, phone="+998901234572")

    result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone=target_user.phone,
        role=ShopRole.CASHIER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.SHOP_SUSPENDED
    assert count_rows(db_session, ShopStaff) == 1
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_add_staff_invalid_phone_and_missing_user_share_validation_error(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    owner = add_owner_actor(db_session, shop)

    invalid_phone_result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone="not a phone",
        role=ShopRole.CASHIER,
        now=NOW,
    )
    missing_user_result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone="+998901234573",
        role=ShopRole.CASHIER,
        now=NOW,
    )

    assert invalid_phone_result == missing_user_result
    assert invalid_phone_result.error is ErrorCode.VALIDATION_ERROR
    assert "not a phone" not in str(invalid_phone_result)
    assert "+998901234573" not in str(missing_user_result)
    assert count_rows(db_session, ShopStaff) == 1
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_add_staff_invalid_role_returns_validation_error(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    owner = add_owner_actor(db_session, shop)
    target_user = add_user(db_session, phone="+998901234574")

    result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone=target_user.phone,
        role="seller",
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.VALIDATION_ERROR
    assert count_rows(db_session, ShopStaff) == 1
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_add_staff_expected_unique_collision_uses_savepoint_and_keeps_session_usable(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = add_shop_row(db_session)
    owner = add_owner_actor(db_session, shop)
    target_user = add_user(db_session, phone="+998901234575")
    add_staff_row(db_session, shop, target_user, role=ShopRole.CASHIER)
    original_lock_staff = repository._lock_staff_for_user_for_update

    def hide_target_staff(session, *, locked_shop, user_id):
        if user_id == target_user.id:
            return None
        return original_lock_staff(
            session,
            locked_shop=locked_shop,
            user_id=user_id,
        )

    monkeypatch.setattr(
        repository,
        "_lock_staff_for_user_for_update",
        hide_target_staff,
    )

    result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(owner.id),
        phone=target_user.phone,
        role=ShopRole.MANAGER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.VALIDATION_ERROR
    assert "IntegrityError" not in str(result)
    assert count_rows(db_session, ShopStaff) == 2
    assert count_rows(db_session, ShopStaffEvent) == 0

    add_user(db_session)
    assert count_rows(db_session, User) == 3


def only_staff_event(session: Session) -> ShopStaffEvent:
    event = session.scalar(select(ShopStaffEvent))
    assert event is not None
    return event


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0
