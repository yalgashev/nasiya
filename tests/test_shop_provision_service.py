from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.shop import service as shop_service
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent
from app.shop.service import (
    ProvisionActiveShopError,
    provision_active_shop,
)
from app.shop.values import ShopId, UserId

NOW = datetime(2026, 7, 27, 18, 0, tzinfo=UTC)


class ValidationOnlySession:
    def __init__(self, owner: User | None) -> None:
        self.owner = owner
        self.begin_nested_called = False

    def get(self, model, identity):
        assert model is User
        _ = identity
        return self.owner

    def begin_nested(self):
        self.begin_nested_called = True
        raise AssertionError("invalid input must not open a savepoint")


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


def test_shop_service_public_function_surface_is_limited() -> None:
    assert shop_service.__all__ == (
        "add_staff",
        "change_staff_role",
        "provision_active_shop",
        "revoke_staff",
        "reactivate_shop",
        "suspend_shop",
    )


def test_provision_active_shop_docstring_and_transaction_boundary() -> None:
    source = Path("app/shop/service.py").read_text()

    assert "owner_application" in source
    assert "development/admin" in source
    assert ".commit(" not in source
    assert ".rollback(" not in source


@pytest.mark.parametrize(
    "raw_name",
    [
        "",
        "   ",
        " A ",
        "x" * 121,
    ],
)
def test_provision_active_shop_rejects_invalid_name_before_mutation(
    raw_name: str,
) -> None:
    owner = User(phone="+998901234567")
    session = ValidationOnlySession(owner)

    result = provision_active_shop(
        session,
        shop_id=ShopId(uuid4()),
        name=raw_name,
        phone="+998901234567",
        address_text=None,
        owner_user_id=UserId(uuid4()),
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ProvisionActiveShopError.INVALID_NAME
    assert session.begin_nested_called is False


def test_provision_active_shop_rejects_invalid_phone_before_mutation() -> None:
    owner = User(phone="+998901234567")
    session = ValidationOnlySession(owner)

    result = provision_active_shop(
        session,
        shop_id=ShopId(uuid4()),
        name="Valid Shop",
        phone="not a phone",
        address_text=None,
        owner_user_id=UserId(uuid4()),
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ProvisionActiveShopError.INVALID_PHONE
    assert session.begin_nested_called is False


@pytest.mark.integration
def test_provision_active_shop_creates_safe_active_shop_bundle(
    db_session: Session,
) -> None:
    owner = add_user(db_session)
    shop_id = ShopId(uuid4())

    result = provision_active_shop(
        db_session,
        shop_id=shop_id,
        name="  Demo    Main\tShop  ",
        phone="90 123-45-67",
        address_text="Tashkent",
        owner_user_id=UserId(owner.id),
        actor_user_id=UserId(owner.id),
        now=NOW,
    )

    assert result.succeeded is True
    assert result.error is None
    assert result.shop is not None
    assert result.shop.shop_id == shop_id
    assert result.shop.name == "Demo Main Shop"
    assert result.shop.status is ShopStatus.ACTIVE
    assert not hasattr(result.shop, "phone")

    shop = db_session.get(Shop, shop_id)
    assert shop is not None
    assert shop.name == "Demo Main Shop"
    assert shop.phone == "+998901234567"
    assert shop.address_text == "Tashkent"
    assert shop.status == ShopStatus.ACTIVE.value
    assert shop.created_at == NOW
    assert shop.updated_at == NOW


@pytest.mark.integration
def test_provision_active_shop_returns_owner_not_found_without_rows(
    db_session: Session,
) -> None:
    result = provision_active_shop(
        db_session,
        shop_id=ShopId(uuid4()),
        name="Owner Missing Shop",
        phone="+998901234567",
        address_text=None,
        owner_user_id=UserId(uuid4()),
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ProvisionActiveShopError.OWNER_NOT_FOUND
    assert count_rows(db_session, Shop) == 0
    assert count_rows(db_session, ShopStaff) == 0
    assert count_rows(db_session, ShopStatusEvent) == 0
    assert count_rows(db_session, ShopStaffEvent) == 0


@pytest.mark.integration
def test_provision_active_shop_duplicate_shop_id_is_expected_conflict(
    db_session: Session,
) -> None:
    owner = add_user(db_session)
    shop_id = uuid4()
    existing_shop = Shop(
        id=shop_id,
        name="Existing Shop",
        phone="+998901111111",
        status=ShopStatus.ACTIVE.value,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(existing_shop)
    db_session.flush()
    db_session.expunge_all()

    result = provision_active_shop(
        db_session,
        shop_id=ShopId(shop_id),
        name="Duplicate Shop",
        phone="+998902222222",
        address_text=None,
        owner_user_id=UserId(owner.id),
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ProvisionActiveShopError.DUPLICATE_SHOP_ID
    assert "IntegrityError" not in str(result)
    assert count_rows(db_session, Shop) == 1
    assert count_rows(db_session, ShopStaff) == 0
    assert count_rows(db_session, ShopStatusEvent) == 0
    assert count_rows(db_session, ShopStaffEvent) == 0

    add_user(db_session)
    assert count_rows(db_session, User) == 2


@pytest.mark.integration
def test_provision_active_shop_events_are_inserted_atomically(
    db_session: Session,
) -> None:
    owner = add_user(db_session)
    shop_id = ShopId(uuid4())

    result = provision_active_shop(
        db_session,
        shop_id=shop_id,
        name="Atomic Event Shop",
        phone="+998901234567",
        address_text=None,
        owner_user_id=UserId(owner.id),
        actor_user_id=UserId(owner.id),
        now=NOW,
    )

    assert result.succeeded is True

    staff = db_session.scalar(select(ShopStaff).where(ShopStaff.shop_id == shop_id))
    status_event = db_session.scalar(
        select(ShopStatusEvent).where(ShopStatusEvent.shop_id == shop_id)
    )
    staff_event = db_session.scalar(
        select(ShopStaffEvent).where(ShopStaffEvent.shop_id == shop_id)
    )

    assert staff is not None
    assert staff.user_id == owner.id
    assert staff.role == ShopRole.OWNER.value
    assert staff.is_active is True
    assert staff.revoked_at is None

    assert status_event is not None
    assert status_event.action == ShopStatusAction.ACTIVATED.value
    assert status_event.actor_user_id == owner.id
    assert status_event.reason is None
    assert status_event.created_at == NOW

    assert staff_event is not None
    assert staff_event.subject_user_id == owner.id
    assert staff_event.action == ShopStaffAction.ADDED.value
    assert staff_event.old_role is None
    assert staff_event.new_role == ShopRole.OWNER.value
    assert staff_event.actor_user_id == owner.id
    assert staff_event.created_at == NOW


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0
