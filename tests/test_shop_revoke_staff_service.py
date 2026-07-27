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
from app.shop.service import RevokeStaffOutcome, revoke_staff
from app.shop.values import ShopId, ShopStaffId, UserId

NOW = datetime(2026, 7, 27, 21, 0, tzinfo=UTC)
PAST = datetime(2026, 7, 27, 20, 0, tzinfo=UTC)


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
        name="Revoke Service Shop",
        phone=unique_phone(),
        status=status.value,
        created_at=PAST,
        updated_at=PAST,
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
    is_active: bool = True,
) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=role.value,
        is_active=is_active,
        created_at=PAST,
        updated_at=PAST,
        revoked_at=None if is_active else PAST,
    )
    session.add(staff)
    session.flush()
    return staff


def add_owner_actor(session: Session, shop: Shop) -> tuple[User, ShopStaff]:
    owner = add_user(session)
    staff = add_staff_row(session, shop, owner, role=ShopRole.OWNER)
    return owner, staff


@pytest.mark.integration
def test_revoke_staff_revokes_active_staff_and_writes_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    target_user = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_user, role=ShopRole.CASHIER)

    result = revoke_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        now=NOW,
    )

    assert result.succeeded is True
    assert result.error is None
    assert result.revocation is not None
    assert result.revocation.staff_id == target_staff.id
    assert result.revocation.old_role is ShopRole.CASHIER
    assert result.revocation.outcome is RevokeStaffOutcome.REVOKED

    db_session.refresh(target_staff)
    assert target_staff.is_active is False
    assert target_staff.revoked_at == NOW
    assert target_staff.role == ShopRole.CASHIER.value

    event = only_staff_event(db_session)
    assert event.shop_id == shop.id
    assert event.subject_user_id == target_user.id
    assert event.action == ShopStaffAction.REVOKED.value
    assert event.old_role == ShopRole.CASHIER.value
    assert event.new_role is None
    assert event.actor_user_id == actor.id
    assert event.created_at == NOW


@pytest.mark.integration
def test_revoke_staff_owner_allowed_when_second_owner_exists(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    second_owner = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, second_owner, role=ShopRole.OWNER)

    result = revoke_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        now=NOW,
    )

    assert result.succeeded is True
    assert result.revocation is not None
    assert result.revocation.old_role is ShopRole.OWNER
    assert result.revocation.outcome is RevokeStaffOutcome.REVOKED
    db_session.refresh(target_staff)
    assert target_staff.is_active is False


@pytest.mark.integration
def test_revoke_staff_last_owner_is_blocked(db_session: Session) -> None:
    shop = add_shop_row(db_session)
    actor, target_staff = add_owner_actor(db_session, shop)

    result = revoke_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.LAST_OWNER
    db_session.refresh(target_staff)
    assert target_staff.is_active is True
    assert target_staff.revoked_at is None
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.parametrize("target_case", ["already_revoked", "foreign", "nonexistent"])
@pytest.mark.integration
def test_revoke_staff_not_found_cases_share_safe_noop_outcome(
    db_session: Session,
    target_case: str,
) -> None:
    shop = add_shop_row(db_session)
    actor, _actor_staff = add_owner_actor(db_session, shop)

    if target_case == "already_revoked":
        target_user = add_user(db_session)
        target_staff = add_staff_row(
            db_session,
            shop,
            target_user,
            role=ShopRole.CASHIER,
            is_active=False,
        )
        target_staff_id = target_staff.id
    elif target_case == "foreign":
        foreign_shop = add_shop_row(db_session)
        foreign_user = add_user(db_session)
        target_staff = add_staff_row(
            db_session,
            foreign_shop,
            foreign_user,
            role=ShopRole.CASHIER,
        )
        target_staff_id = target_staff.id
    else:
        target_staff_id = uuid4()

    result = revoke_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff_id),
        now=NOW,
    )

    assert result.succeeded is True
    assert result.error is None
    assert result.revocation is not None
    assert result.revocation.staff_id is None
    assert result.revocation.old_role is None
    assert result.revocation.outcome is RevokeStaffOutcome.NOT_FOUND
    assert str(target_staff_id) not in str(result)
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_revoke_staff_requires_owner_actor(db_session: Session) -> None:
    shop = add_shop_row(db_session)
    actor_user = add_user(db_session)
    add_staff_row(db_session, shop, actor_user, role=ShopRole.CASHIER)
    target_user = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_user, role=ShopRole.MANAGER)

    result = revoke_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor_user.id),
        target_staff_id=ShopStaffId(target_staff.id),
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.FORBIDDEN
    db_session.refresh(target_staff)
    assert target_staff.is_active is True
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_revoke_staff_rejects_suspended_shop_mutation(db_session: Session) -> None:
    shop = add_shop_row(db_session, status=ShopStatus.SUSPENDED)
    actor, _actor_staff = add_owner_actor(db_session, shop)
    target_user = add_user(db_session)
    target_staff = add_staff_row(db_session, shop, target_user, role=ShopRole.CASHIER)

    result = revoke_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(target_staff.id),
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.SHOP_SUSPENDED
    db_session.refresh(target_staff)
    assert target_staff.is_active is True
    assert count_rows(db_session, ShopStaffEvent) == 0


def test_revoke_staff_last_owner_check_uses_shop_lock_before_owner_count() -> None:
    source = Path("app/shop/service.py").read_text()
    revoke_source = source[source.index("def revoke_staff") :]

    lock_shop_index = revoke_source.index("repository.lock_shop_for_update")
    lock_target_index = revoke_source.index(
        "repository._lock_active_staff_by_id_for_update"
    )
    count_owner_index = revoke_source.index("repository.count_active_owners")

    assert lock_shop_index < lock_target_index < count_owner_index


def only_staff_event(session: Session) -> ShopStaffEvent:
    event = session.scalar(select(ShopStaffEvent))
    assert event is not None
    return event


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0
