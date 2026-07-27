from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


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
    *,
    name: str = "M5 Shop",
    phone: str | None = None,
    status: str = "active",
) -> Shop:
    shop = Shop(
        name=name,
        phone=unique_phone() if phone is None else phone,
        status=status,
    )
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
    revoked_at: datetime | None = None,
) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=role,
        is_active=is_active,
        revoked_at=revoked_at,
    )
    session.add(staff)
    session.flush()
    return staff


def add_status_event(
    session: Session,
    shop: Shop,
    *,
    action: str,
    reason: str | None = None,
    actor: User | None = None,
) -> ShopStatusEvent:
    event = ShopStatusEvent(
        shop_id=shop.id,
        action=action,
        reason=reason,
        actor_user_id=None if actor is None else actor.id,
    )
    session.add(event)
    session.flush()
    return event


def add_staff_event(
    session: Session,
    shop: Shop,
    subject: User,
    *,
    action: str,
    old_role: str | None = None,
    new_role: str | None = None,
    actor: User | None = None,
) -> ShopStaffEvent:
    event = ShopStaffEvent(
        shop_id=shop.id,
        subject_user_id=subject.id,
        action=action,
        old_role=old_role,
        new_role=new_role,
        actor_user_id=None if actor is None else actor.id,
    )
    session.add(event)
    session.flush()
    return event


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


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.integration
def test_valid_shop_insert_works(db_session: Session) -> None:
    shop = add_shop(db_session, name="AB")

    assert shop.id is not None
    assert shop.name == "AB"
    assert shop.status == "active"
    assert count_rows(db_session, Shop) == 1


@pytest.mark.integration
def test_shop_invalid_status_is_rejected(db_session: Session) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_shop(db_session, status="pending")

    assert count_rows(db_session, Shop) == 0


@pytest.mark.integration
@pytest.mark.parametrize("name", ["A", " A ", ""])
def test_shop_name_shorter_than_two_after_btrim_is_rejected(
    db_session: Session,
    name: str,
) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_shop(db_session, name=name)

    assert count_rows(db_session, Shop) == 0


@pytest.mark.integration
def test_shop_name_longer_than_120_after_btrim_is_rejected(
    db_session: Session,
) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_shop(db_session, name=f" {'A' * 121} ")

    assert count_rows(db_session, Shop) == 0


@pytest.mark.integration
@pytest.mark.parametrize("phone", ["", "   "])
def test_shop_blank_or_whitespace_phone_is_rejected(
    db_session: Session,
    phone: str,
) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_shop(db_session, phone=phone)

    assert count_rows(db_session, Shop) == 0


@pytest.mark.integration
def test_shop_delete_is_restricted_when_staff_row_exists(
    db_session: Session,
) -> None:
    shop = add_shop(db_session)
    user = add_user(db_session)
    staff = add_staff(db_session, shop, user)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(shop)
            db_session.flush()

    assert db_session.get(Shop, shop.id) is not None
    assert db_session.get(ShopStaff, staff.id) is not None


@pytest.mark.integration
def test_shop_staff_duplicate_shop_user_is_rejected(db_session: Session) -> None:
    shop = add_shop(db_session)
    user = add_user(db_session)
    add_staff(db_session, shop, user)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_staff(db_session, shop, user, role="owner")

    assert count_rows(db_session, ShopStaff) == 1


@pytest.mark.integration
def test_same_user_can_work_in_multiple_shops(db_session: Session) -> None:
    user = add_user(db_session)
    first_shop = add_shop(db_session, name="First Shop")
    second_shop = add_shop(db_session, name="Second Shop")

    first_staff = add_staff(db_session, first_shop, user, role="cashier")
    second_staff = add_staff(db_session, second_shop, user, role="owner")

    assert first_staff.user_id == second_staff.user_id
    assert first_staff.shop_id != second_staff.shop_id
    assert count_rows(db_session, ShopStaff) == 2


@pytest.mark.integration
def test_shop_staff_invalid_role_is_rejected(db_session: Session) -> None:
    shop = add_shop(db_session)
    user = add_user(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_staff(db_session, shop, user, role="seller")

    assert count_rows(db_session, ShopStaff) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("is_active", "revoked_at"),
    [
        (True, NOW),
        (False, None),
    ],
)
def test_shop_staff_active_revoked_at_invalid_insert_is_rejected(
    db_session: Session,
    is_active: bool,
    revoked_at: datetime | None,
) -> None:
    shop = add_shop(db_session)
    user = add_user(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_staff(
                db_session,
                shop,
                user,
                is_active=is_active,
                revoked_at=revoked_at,
            )

    assert count_rows(db_session, ShopStaff) == 0


@pytest.mark.integration
def test_shop_staff_active_revoked_at_invalid_updates_are_rejected(
    db_session: Session,
) -> None:
    shop = add_shop(db_session)
    active_user = add_user(db_session)
    inactive_user = add_user(db_session)
    active_staff = add_staff(db_session, shop, active_user, is_active=True)
    inactive_staff = add_staff(
        db_session,
        shop,
        inactive_user,
        is_active=False,
        revoked_at=NOW,
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            active_staff.revoked_at = NOW
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            inactive_staff.revoked_at = None
            db_session.flush()

    db_session.refresh(active_staff)
    db_session.refresh(inactive_staff)
    assert active_staff.is_active is True
    assert active_staff.revoked_at is None
    assert inactive_staff.is_active is False
    assert inactive_staff.revoked_at == NOW


@pytest.mark.integration
def test_shop_staff_restricts_parent_shop_and_user_delete(
    db_session: Session,
) -> None:
    shop = add_shop(db_session)
    user = add_user(db_session)
    staff = add_staff(db_session, shop, user)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(shop)
            db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(user)
            db_session.flush()

    assert db_session.get(Shop, shop.id) is not None
    assert db_session.get(User, user.id) is not None
    assert db_session.get(ShopStaff, staff.id) is not None


@pytest.mark.integration
def test_shop_status_event_activated_with_null_reason_is_valid(
    db_session: Session,
) -> None:
    shop = add_shop(db_session)

    event = add_status_event(db_session, shop, action="activated")

    assert event.id is not None
    assert event.action == "activated"
    assert event.reason is None


@pytest.mark.integration
def test_shop_status_event_activated_with_reason_is_rejected(
    db_session: Session,
) -> None:
    shop = add_shop(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_status_event(db_session, shop, action="activated", reason="manual")

    assert count_rows(db_session, ShopStatusEvent) == 0


@pytest.mark.integration
@pytest.mark.parametrize("action", ["suspended", "reactivated"])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_shop_status_event_suspend_reactivate_require_nonblank_reason(
    db_session: Session,
    action: str,
    reason: str | None,
) -> None:
    shop = add_shop(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_status_event(db_session, shop, action=action, reason=reason)

    assert count_rows(db_session, ShopStatusEvent) == 0


@pytest.mark.integration
def test_shop_status_event_invalid_action_is_rejected(
    db_session: Session,
) -> None:
    shop = add_shop(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_status_event(db_session, shop, action="deleted")

    assert count_rows(db_session, ShopStatusEvent) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("action", "old_role", "new_role"),
    [
        ("added", None, "cashier"),
        ("role_changed", "cashier", "owner"),
        ("revoked", "cashier", None),
    ],
)
def test_shop_staff_event_valid_role_transition_combinations_insert(
    db_session: Session,
    action: str,
    old_role: str | None,
    new_role: str | None,
) -> None:
    shop = add_shop(db_session)
    subject = add_user(db_session)

    event = add_staff_event(
        db_session,
        shop,
        subject,
        action=action,
        old_role=old_role,
        new_role=new_role,
    )

    assert event.id is not None
    assert event.action == action
    assert event.old_role == old_role
    assert event.new_role == new_role


@pytest.mark.integration
@pytest.mark.parametrize(
    ("action", "old_role", "new_role"),
    [
        ("added", "cashier", "owner"),
        ("role_changed", None, "owner"),
        ("role_changed", "cashier", "cashier"),
        ("revoked", None, None),
        ("revoked", "cashier", "owner"),
    ],
)
def test_shop_staff_event_invalid_role_transition_combinations_are_rejected(
    db_session: Session,
    action: str,
    old_role: str | None,
    new_role: str | None,
) -> None:
    shop = add_shop(db_session)
    subject = add_user(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_staff_event(
                db_session,
                shop,
                subject,
                action=action,
                old_role=old_role,
                new_role=new_role,
            )

    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("action", "old_role", "new_role"),
    [
        ("promoted", "cashier", "owner"),
        ("added", None, "seller"),
        ("role_changed", "seller", "owner"),
    ],
)
def test_shop_staff_event_invalid_action_or_role_is_rejected(
    db_session: Session,
    action: str,
    old_role: str | None,
    new_role: str | None,
) -> None:
    shop = add_shop(db_session)
    subject = add_user(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_staff_event(
                db_session,
                shop,
                subject,
                action=action,
                old_role=old_role,
                new_role=new_role,
            )

    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_session_active_shop_id_valid_fk_insert_works(db_session: Session) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session)

    auth_session = add_auth_session(db_session, user, active_shop_id=shop.id)

    assert auth_session.id is not None
    assert auth_session.active_shop_id == shop.id
    assert count_rows(db_session, AuthSession) == 1


@pytest.mark.integration
def test_session_active_shop_id_missing_shop_fk_is_rejected(
    db_session: Session,
) -> None:
    user = add_user(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_auth_session(db_session, user, active_shop_id=uuid4())

    assert count_rows(db_session, AuthSession) == 0


@pytest.mark.integration
def test_session_active_shop_id_restricts_shop_delete(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    shop = add_shop(db_session)
    auth_session = add_auth_session(db_session, user, active_shop_id=shop.id)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(shop)
            db_session.flush()

    assert db_session.get(Shop, shop.id) is not None
    assert db_session.get(AuthSession, auth_session.id) is not None
