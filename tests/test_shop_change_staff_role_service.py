from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus
from app.shop.models import Shop, ShopStaff, ShopStaffEvent
from app.shop.service import (
    ChangeStaffRoleOutcome,
    change_staff_role,
)
from app.shop.values import ShopId, ShopStaffId, UserId

NOW = datetime(2026, 7, 27, 20, 0, tzinfo=UTC)


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


def add_shop_row(
    session: Session,
    *,
    status: ShopStatus = ShopStatus.ACTIVE,
) -> Shop:
    shop = Shop(
        name="Role Service Shop",
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
    role: ShopRole,
) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=role.value,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
        revoked_at=None,
    )
    session.add(staff)
    session.flush()
    return staff


def add_owner_actor(session: Session, shop: Shop) -> tuple[User, ShopStaff]:
    owner = add_user(session)
    staff = add_staff_row(session, shop, owner, role=ShopRole.OWNER)
    return owner, staff


@pytest.mark.parametrize("new_role", [ShopRole.MANAGER, ShopRole.CASHIER])
@pytest.mark.integration
def test_change_staff_role_owner_to_manager_or_cashier_with_second_owner(
    db_session: Session,
    new_role: ShopRole,
) -> None:
    shop = add_shop_row(db_session)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    target_owner = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_owner, role=ShopRole.OWNER)

    result = change_staff_role(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        new_role=new_role,
        now=NOW,
    )

    assert result.succeeded is True
    assert result.error is None
    assert result.staff is not None
    assert result.staff.staff_id == target_staff.id
    assert result.staff.old_role is ShopRole.OWNER
    assert result.staff.new_role is new_role
    assert result.staff.outcome is ChangeStaffRoleOutcome.ROLE_CHANGED

    db_session.refresh(target_staff)
    assert target_staff.role == new_role.value

    event = only_staff_event(db_session)
    assert event.shop_id == shop.id
    assert event.subject_user_id == target_owner.id
    assert event.action == ShopStaffAction.ROLE_CHANGED.value
    assert event.old_role == ShopRole.OWNER.value
    assert event.new_role == new_role.value
    assert event.actor_user_id == actor.id
    assert event.created_at == NOW


@pytest.mark.parametrize(
    ("old_role", "new_role"),
    [
        (ShopRole.MANAGER, ShopRole.CASHIER),
        (ShopRole.CASHIER, ShopRole.MANAGER),
    ],
)
@pytest.mark.integration
def test_change_staff_role_manager_cashier_transitions(
    db_session: Session,
    old_role: ShopRole,
    new_role: ShopRole,
) -> None:
    shop = add_shop_row(db_session)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    target_user = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_user, role=old_role)

    result = change_staff_role(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        new_role=new_role.value,
        now=NOW,
    )

    assert result.succeeded is True
    assert result.staff is not None
    assert result.staff.old_role is old_role
    assert result.staff.new_role is new_role
    assert result.staff.outcome is ChangeStaffRoleOutcome.ROLE_CHANGED

    db_session.refresh(target_staff)
    assert target_staff.role == new_role.value
    event = only_staff_event(db_session)
    assert event.old_role == old_role.value
    assert event.new_role == new_role.value


@pytest.mark.integration
def test_change_staff_role_same_role_is_no_op_without_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    target_user = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_user, role=ShopRole.CASHIER)

    result = change_staff_role(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        new_role=ShopRole.CASHIER,
        now=NOW,
    )

    assert result.succeeded is True
    assert result.staff is not None
    assert result.staff.staff_id == target_staff.id
    assert result.staff.old_role is ShopRole.CASHIER
    assert result.staff.new_role is ShopRole.CASHIER
    assert result.staff.outcome is ChangeStaffRoleOutcome.ALREADY_ROLE
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_change_staff_role_foreign_staff_is_forbidden_without_leakage(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    foreign_shop = add_shop_row(db_session)
    foreign_user = add_user(db_session)
    foreign_staff = add_staff_row(
        db_session,
        foreign_shop,
        foreign_user,
        role=ShopRole.CASHIER,
    )

    result = change_staff_role(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(foreign_staff.id),
        new_role=ShopRole.MANAGER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.FORBIDDEN
    assert str(foreign_staff.id) not in str(result)
    db_session.refresh(foreign_staff)
    assert foreign_staff.role == ShopRole.CASHIER.value
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_change_staff_role_last_owner_is_blocked(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    actor, target_staff = add_owner_actor(db_session, shop)

    result = change_staff_role(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        new_role=ShopRole.CASHIER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.LAST_OWNER
    db_session.refresh(target_staff)
    assert target_staff.role == ShopRole.OWNER.value
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_change_staff_role_requires_owner_actor(db_session: Session) -> None:
    shop = add_shop_row(db_session)
    actor_user = add_user(db_session)
    add_staff_row(db_session, shop, actor_user, role=ShopRole.CASHIER)
    target_user = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_user, role=ShopRole.MANAGER)

    result = change_staff_role(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor_user.id),
        target_staff_id=ShopStaffId(target_staff.id),
        new_role=ShopRole.CASHIER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.FORBIDDEN
    db_session.refresh(target_staff)
    assert target_staff.role == ShopRole.MANAGER.value
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_change_staff_role_rejects_suspended_shop_mutation(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session, status=ShopStatus.SUSPENDED)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    target_user = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_user, role=ShopRole.CASHIER)

    result = change_staff_role(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        new_role=ShopRole.MANAGER,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.SHOP_SUSPENDED
    db_session.refresh(target_staff)
    assert target_staff.role == ShopRole.CASHIER.value
    assert count_rows(db_session, ShopStaffEvent) == 0


def test_last_owner_check_relies_on_shop_lock_order_not_target_row_only() -> None:
    source = Path("app/shop/service.py").read_text()

    lock_shop_index = source.index("repository.lock_shop_for_update")
    count_owner_index = source.index("repository.count_active_owners")

    assert lock_shop_index < count_owner_index


def only_staff_event(session: Session) -> ShopStaffEvent:
    event = session.scalar(select(ShopStaffEvent))
    assert event is not None
    return event


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0
