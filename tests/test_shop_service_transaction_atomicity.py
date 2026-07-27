from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop import repository
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent
from app.shop.service import (
    AddStaffOutcome,
    ProvisionActiveShopError,
    add_staff,
    change_staff_role,
    provision_active_shop,
    reactivate_shop,
    revoke_staff,
    suspend_shop,
)
from app.shop.values import ShopId, ShopStaffId, UserId

NOW = datetime(2026, 7, 27, 23, 0, tzinfo=UTC)


class EventAppendFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceScenario:
    name: str
    arrange: Callable[[Session], dict[str, UUID]]
    act: Callable[[Session, dict[str, UUID]], object]
    assert_rolled_back: Callable[[Engine, dict[str, UUID]], None]
    assert_committed: Callable[[Engine, dict[str, UUID]], None]


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_m5_services_do_not_own_transaction_or_session_lifecycle() -> None:
    source = Path("app/shop/service.py").read_text()

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source


@pytest.mark.integration
def test_expected_unique_conflicts_keep_caller_session_usable_and_safe(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = add_user(db_session)
    duplicate_shop_id = uuid4()
    add_shop_row(db_session, shop_id=duplicate_shop_id)

    provision_result = provision_active_shop(
        db_session,
        shop_id=ShopId(duplicate_shop_id),
        name="Duplicate",
        phone="+998900020001",
        address_text=None,
        owner_user_id=UserId(owner.id),
        now=NOW,
    )

    assert provision_result.succeeded is False
    assert provision_result.error is ProvisionActiveShopError.DUPLICATE_SHOP_ID
    assert_public_result_hides_raw_database_details(
        provision_result,
        forbidden_fragments={str(duplicate_shop_id), "pk_shops"},
    )
    assert count_rows(db_session, Shop) == 1
    add_user(db_session)
    assert count_rows(db_session, User) == 2

    shop = add_shop_row(db_session)
    actor = add_user(db_session)
    add_staff_row(db_session, shop, actor, role=ShopRole.OWNER)
    target_user = add_user(db_session, phone="+998900020002")
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

    add_result = add_staff(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        phone=target_user.phone,
        role=ShopRole.MANAGER,
        now=NOW,
    )

    assert add_result.succeeded is False
    assert add_result.error is ErrorCode.VALIDATION_ERROR
    assert_public_result_hides_raw_database_details(
        add_result,
        forbidden_fragments={"uq_shop_staff_shop_id_user_id", str(target_user.id)},
    )
    assert count_rows(db_session, ShopStaff) == 2
    assert count_rows(db_session, ShopStaffEvent) == 0
    add_user(db_session)
    assert count_rows(db_session, User) == 5


@pytest.mark.integration
def test_event_exactly_once_and_noop_semantics(db_session: Session) -> None:
    owner = add_user(db_session)
    shop_id = ShopId(uuid4())
    provision_result = provision_active_shop(
        db_session,
        shop_id=shop_id,
        name="Exactly Once Shop",
        phone="+998900021001",
        address_text=None,
        owner_user_id=UserId(owner.id),
        actor_user_id=UserId(owner.id),
        now=NOW,
    )

    assert provision_result.succeeded is True
    assert status_event_count(db_session, ShopStatusAction.ACTIVATED) == 1
    assert staff_event_count(db_session, ShopStaffAction.ADDED) == 1

    cashier = add_user(db_session, phone="+998900021002")
    add_result = add_staff(
        db_session,
        shop_id=shop_id,
        actor_user_id=UserId(owner.id),
        phone=cashier.phone,
        role=ShopRole.CASHIER,
        now=NOW,
    )
    repeat_add_result = add_staff(
        db_session,
        shop_id=shop_id,
        actor_user_id=UserId(owner.id),
        phone=cashier.phone,
        role=ShopRole.MANAGER,
        now=NOW,
    )

    assert add_result.succeeded is True
    assert add_result.staff.outcome is AddStaffOutcome.ADDED
    assert repeat_add_result.succeeded is True
    assert repeat_add_result.staff.outcome is AddStaffOutcome.ALREADY_ACTIVE
    assert staff_event_count(db_session, ShopStaffAction.ADDED) == 2

    staff = repository.get_active_staff(
        db_session,
        shop_id=shop_id,
        user_id=UserId(cashier.id),
    )
    assert staff is not None
    change_result = change_staff_role(
        db_session,
        shop_id=shop_id,
        actor_user_id=UserId(owner.id),
        target_staff_id=ShopStaffId(staff.id),
        new_role=ShopRole.MANAGER,
        now=NOW,
    )
    same_role_result = change_staff_role(
        db_session,
        shop_id=shop_id,
        actor_user_id=UserId(owner.id),
        target_staff_id=ShopStaffId(staff.id),
        new_role=ShopRole.MANAGER,
        now=NOW,
    )

    assert change_result.succeeded is True
    assert same_role_result.succeeded is True
    assert staff_event_count(db_session, ShopStaffAction.ROLE_CHANGED) == 1

    revoke_result = revoke_staff(
        db_session,
        shop_id=shop_id,
        actor_user_id=UserId(owner.id),
        target_staff_id=ShopStaffId(staff.id),
        now=NOW,
    )
    repeat_revoke_result = revoke_staff(
        db_session,
        shop_id=shop_id,
        actor_user_id=UserId(owner.id),
        target_staff_id=ShopStaffId(staff.id),
        now=NOW,
    )

    assert revoke_result.succeeded is True
    assert repeat_revoke_result.succeeded is True
    assert staff_event_count(db_session, ShopStaffAction.REVOKED) == 1

    suspend_result = suspend_shop(
        db_session,
        shop_id=shop_id,
        actor_user_id=None,
        reason="manual hold",
        now=NOW,
    )
    repeat_suspend_result = suspend_shop(
        db_session,
        shop_id=shop_id,
        actor_user_id=None,
        reason="manual hold",
        now=NOW,
    )
    reactivate_result = reactivate_shop(
        db_session,
        shop_id=shop_id,
        actor_user_id=None,
        reason="manual review",
        now=NOW,
    )
    repeat_reactivate_result = reactivate_shop(
        db_session,
        shop_id=shop_id,
        actor_user_id=None,
        reason="manual review",
        now=NOW,
    )

    assert suspend_result.succeeded is True
    assert repeat_suspend_result.succeeded is True
    assert reactivate_result.succeeded is True
    assert repeat_reactivate_result.succeeded is True
    assert status_event_count(db_session, ShopStatusAction.SUSPENDED) == 1
    assert status_event_count(db_session, ShopStatusAction.REACTIVATED) == 1


def arrange_provision(session: Session) -> dict[str, UUID]:
    owner = add_user(session)
    return {
        "owner_id": owner.id,
        "shop_id": uuid4(),
    }


def act_provision(session: Session, state: dict[str, UUID]):
    return provision_active_shop(
        session,
        shop_id=ShopId(state["shop_id"]),
        name="Transaction Shop",
        phone="+998900030001",
        address_text=None,
        owner_user_id=UserId(state["owner_id"]),
        actor_user_id=UserId(state["owner_id"]),
        now=NOW,
    )


def assert_provision_rolled_back(engine: Engine, state: dict[str, UUID]) -> None:
    with create_database_session_factory(engine)() as session:
        assert session.get(Shop, state["shop_id"]) is None
        assert event_count(session, ShopStatusEvent) == 0
        assert event_count(session, ShopStaffEvent) == 0
        assert staff_for_user(session, state["shop_id"], state["owner_id"]) is None


def assert_provision_committed(engine: Engine, state: dict[str, UUID]) -> None:
    with create_database_session_factory(engine)() as session:
        shop = session.get(Shop, state["shop_id"])
        assert shop is not None
        assert shop.status == ShopStatus.ACTIVE.value
        assert event_count(session, ShopStatusEvent) == 1
        assert event_count(session, ShopStaffEvent) == 1
        staff = staff_for_user(session, state["shop_id"], state["owner_id"])
        assert staff is not None
        assert staff.role == ShopRole.OWNER.value


def arrange_add_staff(session: Session) -> dict[str, UUID]:
    shop = add_shop_row(session)
    owner = add_user(session)
    target = add_user(session, phone="+998900030002")
    add_staff_row(session, shop, owner, role=ShopRole.OWNER)
    return {
        "shop_id": shop.id,
        "owner_id": owner.id,
        "target_id": target.id,
    }


def act_add_staff(session: Session, state: dict[str, UUID]):
    return add_staff(
        session,
        shop_id=ShopId(state["shop_id"]),
        actor_user_id=UserId(state["owner_id"]),
        phone="+998900030002",
        role=ShopRole.CASHIER,
        now=NOW,
    )


def assert_add_staff_rolled_back(engine: Engine, state: dict[str, UUID]) -> None:
    with create_database_session_factory(engine)() as session:
        assert staff_for_user(session, state["shop_id"], state["target_id"]) is None
        assert event_count(session, ShopStaffEvent) == 0


def assert_add_staff_committed(engine: Engine, state: dict[str, UUID]) -> None:
    with create_database_session_factory(engine)() as session:
        staff = staff_for_user(session, state["shop_id"], state["target_id"])
        assert staff is not None
        assert staff.role == ShopRole.CASHIER.value
        assert event_count(session, ShopStaffEvent) == 1


def arrange_change_staff_role(session: Session) -> dict[str, UUID]:
    shop = add_shop_row(session)
    owner = add_user(session)
    target = add_user(session)
    add_staff_row(session, shop, owner, role=ShopRole.OWNER)
    target_staff = add_staff_row(session, shop, target, role=ShopRole.CASHIER)
    return {
        "shop_id": shop.id,
        "owner_id": owner.id,
        "target_staff_id": target_staff.id,
    }


def act_change_staff_role(session: Session, state: dict[str, UUID]):
    return change_staff_role(
        session,
        shop_id=ShopId(state["shop_id"]),
        actor_user_id=UserId(state["owner_id"]),
        target_staff_id=ShopStaffId(state["target_staff_id"]),
        new_role=ShopRole.MANAGER,
        now=NOW,
    )


def assert_change_staff_role_rolled_back(
    engine: Engine,
    state: dict[str, UUID],
) -> None:
    with create_database_session_factory(engine)() as session:
        staff = session.get(ShopStaff, state["target_staff_id"])
        assert staff is not None
        assert staff.role == ShopRole.CASHIER.value
        assert event_count(session, ShopStaffEvent) == 0


def assert_change_staff_role_committed(engine: Engine, state: dict[str, UUID]) -> None:
    with create_database_session_factory(engine)() as session:
        staff = session.get(ShopStaff, state["target_staff_id"])
        assert staff is not None
        assert staff.role == ShopRole.MANAGER.value
        assert event_count(session, ShopStaffEvent) == 1


def arrange_revoke_staff(session: Session) -> dict[str, UUID]:
    shop = add_shop_row(session)
    owner = add_user(session)
    target = add_user(session)
    add_staff_row(session, shop, owner, role=ShopRole.OWNER)
    target_staff = add_staff_row(session, shop, target, role=ShopRole.CASHIER)
    return {
        "shop_id": shop.id,
        "owner_id": owner.id,
        "target_staff_id": target_staff.id,
    }


def act_revoke_staff(session: Session, state: dict[str, UUID]):
    return revoke_staff(
        session,
        shop_id=ShopId(state["shop_id"]),
        actor_user_id=UserId(state["owner_id"]),
        target_staff_id=ShopStaffId(state["target_staff_id"]),
        now=NOW,
    )


def assert_revoke_staff_rolled_back(engine: Engine, state: dict[str, UUID]) -> None:
    with create_database_session_factory(engine)() as session:
        staff = session.get(ShopStaff, state["target_staff_id"])
        assert staff is not None
        assert staff.is_active is True
        assert staff.revoked_at is None
        assert event_count(session, ShopStaffEvent) == 0


def assert_revoke_staff_committed(engine: Engine, state: dict[str, UUID]) -> None:
    with create_database_session_factory(engine)() as session:
        staff = session.get(ShopStaff, state["target_staff_id"])
        assert staff is not None
        assert staff.is_active is False
        assert staff.revoked_at is not None
        assert event_count(session, ShopStaffEvent) == 1


def arrange_suspend_shop(session: Session) -> dict[str, UUID]:
    shop = add_shop_row(session, status=ShopStatus.ACTIVE)
    return {"shop_id": shop.id}


def act_suspend_shop(session: Session, state: dict[str, UUID]):
    return suspend_shop(
        session,
        shop_id=ShopId(state["shop_id"]),
        actor_user_id=None,
        reason="manual hold",
        now=NOW,
    )


def assert_suspend_shop_rolled_back(engine: Engine, state: dict[str, UUID]) -> None:
    assert_shop_status_and_status_event_count(
        engine,
        state,
        expected_status=ShopStatus.ACTIVE,
        expected_event_count=0,
    )


def assert_suspend_shop_committed(engine: Engine, state: dict[str, UUID]) -> None:
    assert_shop_status_and_status_event_count(
        engine,
        state,
        expected_status=ShopStatus.SUSPENDED,
        expected_event_count=1,
    )


def arrange_reactivate_shop(session: Session) -> dict[str, UUID]:
    shop = add_shop_row(session, status=ShopStatus.SUSPENDED)
    return {"shop_id": shop.id}


def act_reactivate_shop(session: Session, state: dict[str, UUID]):
    return reactivate_shop(
        session,
        shop_id=ShopId(state["shop_id"]),
        actor_user_id=None,
        reason="manual review",
        now=NOW,
    )


def assert_reactivate_shop_rolled_back(engine: Engine, state: dict[str, UUID]) -> None:
    assert_shop_status_and_status_event_count(
        engine,
        state,
        expected_status=ShopStatus.SUSPENDED,
        expected_event_count=0,
    )


def assert_reactivate_shop_committed(engine: Engine, state: dict[str, UUID]) -> None:
    assert_shop_status_and_status_event_count(
        engine,
        state,
        expected_status=ShopStatus.ACTIVE,
        expected_event_count=1,
    )


def add_user(session: Session, *, phone: str | None = None) -> User:
    user = User(phone=phone or unique_phone())
    session.add(user)
    session.flush()
    return user


def add_shop_row(
    session: Session,
    *,
    shop_id: UUID | None = None,
    status: ShopStatus = ShopStatus.ACTIVE,
) -> Shop:
    shop = Shop(
        id=shop_id or uuid4(),
        name="Transaction Test Shop",
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


def unique_phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def staff_for_user(session: Session, shop_id: UUID, user_id: UUID) -> ShopStaff | None:
    return session.scalar(
        select(ShopStaff).where(
            ShopStaff.shop_id == shop_id,
            ShopStaff.user_id == user_id,
        )
    )


def event_count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def staff_event_count(session: Session, action: ShopStaffAction) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ShopStaffEvent)
            .where(ShopStaffEvent.action == action.value)
        )
        or 0
    )


def status_event_count(session: Session, action: ShopStatusAction) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ShopStatusEvent)
            .where(ShopStatusEvent.action == action.value)
        )
        or 0
    )


def assert_shop_status_and_status_event_count(
    engine: Engine,
    state: dict[str, UUID],
    *,
    expected_status: ShopStatus,
    expected_event_count: int,
) -> None:
    with create_database_session_factory(engine)() as session:
        shop = session.get(Shop, state["shop_id"])
        assert shop is not None
        assert shop.status == expected_status.value
        assert event_count(session, ShopStatusEvent) == expected_event_count


def assert_public_result_hides_raw_database_details(
    result,
    *,
    forbidden_fragments: set[str],
) -> None:
    public_text = str(result)
    for fragment in {
        "IntegrityError",
        "psycopg",
        "duplicate key",
        "violates",
        "INSERT INTO",
        "DETAIL:",
        *forbidden_fragments,
    }:
        assert fragment not in public_text


def m5_service_scenarios() -> tuple[ServiceScenario, ...]:
    return (
        ServiceScenario(
            name="provision_active_shop",
            arrange=arrange_provision,
            act=act_provision,
            assert_rolled_back=assert_provision_rolled_back,
            assert_committed=assert_provision_committed,
        ),
        ServiceScenario(
            name="add_staff",
            arrange=arrange_add_staff,
            act=act_add_staff,
            assert_rolled_back=assert_add_staff_rolled_back,
            assert_committed=assert_add_staff_committed,
        ),
        ServiceScenario(
            name="change_staff_role",
            arrange=arrange_change_staff_role,
            act=act_change_staff_role,
            assert_rolled_back=assert_change_staff_role_rolled_back,
            assert_committed=assert_change_staff_role_committed,
        ),
        ServiceScenario(
            name="revoke_staff",
            arrange=arrange_revoke_staff,
            act=act_revoke_staff,
            assert_rolled_back=assert_revoke_staff_rolled_back,
            assert_committed=assert_revoke_staff_committed,
        ),
        ServiceScenario(
            name="suspend_shop",
            arrange=arrange_suspend_shop,
            act=act_suspend_shop,
            assert_rolled_back=assert_suspend_shop_rolled_back,
            assert_committed=assert_suspend_shop_committed,
        ),
        ServiceScenario(
            name="reactivate_shop",
            arrange=arrange_reactivate_shop,
            act=act_reactivate_shop,
            assert_rolled_back=assert_reactivate_shop_rolled_back,
            assert_committed=assert_reactivate_shop_committed,
        ),
    )


@pytest.mark.parametrize(
    "scenario",
    m5_service_scenarios(),
    ids=lambda scenario: scenario.name,
)
@pytest.mark.integration
def test_successful_m5_service_mutations_follow_caller_commit_or_rollback(
    m2_test_database: Engine,
    scenario: ServiceScenario,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        state = scenario.arrange(session)
        session.commit()

    with session_factory() as session:
        result = scenario.act(session, state)
        assert result.succeeded is True
        session.rollback()
    scenario.assert_rolled_back(m2_test_database, state)

    with session_factory() as session:
        result = scenario.act(session, state)
        assert result.succeeded is True
        session.commit()
    scenario.assert_committed(m2_test_database, state)


@pytest.mark.parametrize(
    "scenario",
    m5_service_scenarios(),
    ids=lambda scenario: scenario.name,
)
@pytest.mark.integration
def test_event_append_failure_rolls_back_state_with_caller_rollback(
    m2_test_database: Engine,
    scenario: ServiceScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        state = scenario.arrange(session)
        session.commit()

    event_helper_name = "add_shop_status_event"
    if scenario.name in {"add_staff", "change_staff_role", "revoke_staff"}:
        event_helper_name = "add_shop_staff_event"

    def fail_event_append(*args, **kwargs):
        _ = args, kwargs
        raise EventAppendFailure("event append failed")

    monkeypatch.setattr(repository, event_helper_name, fail_event_append)

    with session_factory() as session:
        with pytest.raises(EventAppendFailure):
            scenario.act(session, state)
        session.rollback()

    scenario.assert_rolled_back(m2_test_database, state)
