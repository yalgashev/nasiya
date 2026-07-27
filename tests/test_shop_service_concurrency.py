from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Barrier, BrokenBarrierError
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent
from app.shop.service import (
    AddStaffOutcome,
    ChangeStaffRoleOutcome,
    RevokeStaffOutcome,
    ShopStatusTransitionOutcome,
    add_staff,
    change_staff_role,
    revoke_staff,
    suspend_shop,
)
from app.shop.values import ShopId, ShopStaffId, UserId

_BARRIER_TIMEOUT_SECONDS = 5
_FUTURE_TIMEOUT_SECONDS = 15
_LOCK_ORDER_DIAGNOSTIC = "lock order shop -> staff"
NOW = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, repr=False)
class ParallelShopOutcome:
    label: str
    kind: str
    error_code: ErrorCode | None = None
    staff_outcome: AddStaffOutcome | None = None
    role_outcome: ChangeStaffRoleOutcome | None = None
    revoke_outcome: RevokeStaffOutcome | None = None
    status_outcome: ShopStatusTransitionOutcome | None = None
    session_usable: bool = False
    exception_class: str | None = None

    def __repr__(self) -> str:
        return (
            "ParallelShopOutcome("
            f"label={self.label!r}, kind={self.kind!r}, "
            f"error_code={self.error_code}, staff_outcome={self.staff_outcome}, "
            f"role_outcome={self.role_outcome}, "
            f"revoke_outcome={self.revoke_outcome}, "
            f"status_outcome={self.status_outcome}, "
            f"session_usable={self.session_usable}, "
            f"exception_class={self.exception_class!r})"
        )


@pytest.mark.integration
def test_parallel_owner_self_revoke_preserves_last_owner(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        shop = add_shop_row(session)
        owner_a = add_user(session)
        owner_b = add_user(session)
        staff_a = add_staff_row(session, shop, owner_a, role=ShopRole.OWNER)
        staff_b = add_staff_row(session, shop, owner_b, role=ShopRole.OWNER)
        state = {
            "shop_id": shop.id,
            "owner_a_id": owner_a.id,
            "owner_b_id": owner_b.id,
            "staff_a_id": staff_a.id,
            "staff_b_id": staff_b.id,
        }
        session.commit()

    def worker(session: Session, label: str) -> ParallelShopOutcome:
        owner_id = state[f"owner_{label}_id"]
        staff_id = state[f"staff_{label}_id"]
        result = revoke_staff(
            session,
            shop_id=ShopId(state["shop_id"]),
            actor_user_id=UserId(owner_id),
            target_staff_id=ShopStaffId(staff_id),
            now=NOW,
        )
        if result.error is not None:
            return ParallelShopOutcome(
                label=label,
                kind="domain_error",
                error_code=result.error,
            )
        return ParallelShopOutcome(
            label=label,
            kind="revoked",
            revoke_outcome=result.revocation.outcome,
        )

    outcomes = run_parallel_shop_workers(session_factory, ("a", "b"), worker)

    assert_last_owner_race_outcomes(outcomes, success_kind="revoked")
    with session_factory() as session:
        assert_active_owner_count_is_at_least_one(session, state["shop_id"], outcomes)
        assert active_owner_count(session, state["shop_id"]) == 1
        assert event_count(session, ShopStaffEvent, ShopStaffAction.REVOKED.value) == 1


@pytest.mark.integration
def test_parallel_owner_self_role_demotions_preserve_last_owner(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        shop = add_shop_row(session)
        owner_a = add_user(session)
        owner_b = add_user(session)
        staff_a = add_staff_row(session, shop, owner_a, role=ShopRole.OWNER)
        staff_b = add_staff_row(session, shop, owner_b, role=ShopRole.OWNER)
        state = {
            "shop_id": shop.id,
            "owner_a_id": owner_a.id,
            "owner_b_id": owner_b.id,
            "staff_a_id": staff_a.id,
            "staff_b_id": staff_b.id,
        }
        session.commit()

    target_role_by_label = {
        "a": ShopRole.MANAGER,
        "b": ShopRole.CASHIER,
    }

    def worker(session: Session, label: str) -> ParallelShopOutcome:
        result = change_staff_role(
            session,
            shop_id=ShopId(state["shop_id"]),
            actor_user_id=UserId(state[f"owner_{label}_id"]),
            target_staff_id=ShopStaffId(state[f"staff_{label}_id"]),
            new_role=target_role_by_label[label],
            now=NOW,
        )
        if result.error is not None:
            return ParallelShopOutcome(
                label=label,
                kind="domain_error",
                error_code=result.error,
            )
        return ParallelShopOutcome(
            label=label,
            kind="role_changed",
            role_outcome=result.staff.outcome,
        )

    outcomes = run_parallel_shop_workers(session_factory, ("a", "b"), worker)

    assert_last_owner_race_outcomes(outcomes, success_kind="role_changed")
    with session_factory() as session:
        assert_active_owner_count_is_at_least_one(session, state["shop_id"], outcomes)
        assert active_owner_count(session, state["shop_id"]) == 1
        assert (
            event_count(
                session,
                ShopStaffEvent,
                ShopStaffAction.ROLE_CHANGED.value,
            )
            == 1
        )


@pytest.mark.integration
def test_parallel_owner_revoke_and_demote_preserve_last_owner(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        shop = add_shop_row(session)
        owner_a = add_user(session)
        owner_b = add_user(session)
        staff_a = add_staff_row(session, shop, owner_a, role=ShopRole.OWNER)
        staff_b = add_staff_row(session, shop, owner_b, role=ShopRole.OWNER)
        state = {
            "shop_id": shop.id,
            "owner_a_id": owner_a.id,
            "owner_b_id": owner_b.id,
            "staff_a_id": staff_a.id,
            "staff_b_id": staff_b.id,
        }
        session.commit()

    def worker(session: Session, label: str) -> ParallelShopOutcome:
        if label == "revoke":
            result = revoke_staff(
                session,
                shop_id=ShopId(state["shop_id"]),
                actor_user_id=UserId(state["owner_a_id"]),
                target_staff_id=ShopStaffId(state["staff_a_id"]),
                now=NOW,
            )
            if result.error is not None:
                return ParallelShopOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=result.error,
                )
            return ParallelShopOutcome(
                label=label,
                kind="revoked",
                revoke_outcome=result.revocation.outcome,
            )

        result = change_staff_role(
            session,
            shop_id=ShopId(state["shop_id"]),
            actor_user_id=UserId(state["owner_b_id"]),
            target_staff_id=ShopStaffId(state["staff_b_id"]),
            new_role=ShopRole.MANAGER,
            now=NOW,
        )
        if result.error is not None:
            return ParallelShopOutcome(
                label=label,
                kind="domain_error",
                error_code=result.error,
            )
        return ParallelShopOutcome(
            label=label,
            kind="role_changed",
            role_outcome=result.staff.outcome,
        )

    outcomes = run_parallel_shop_workers(
        session_factory,
        ("revoke", "demote"),
        worker,
    )

    assert_last_owner_race_outcomes(
        outcomes,
        success_kind={"revoked", "role_changed"},
    )
    with session_factory() as session:
        assert_active_owner_count_is_at_least_one(session, state["shop_id"], outcomes)
        assert active_owner_count(session, state["shop_id"]) == 1
        owner_reducing_event_count = event_count(
            session, ShopStaffEvent, ShopStaffAction.REVOKED.value
        ) + event_count(session, ShopStaffEvent, ShopStaffAction.ROLE_CHANGED.value)
        assert owner_reducing_event_count == 1, (
            f"{_LOCK_ORDER_DIAGNOSTIC}: expected one owner-reducing commit; "
            f"outcomes={outcomes!r}"
        )


@pytest.mark.integration
def test_parallel_add_staff_same_user_creates_one_staff_and_one_event(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        shop = add_shop_row(session)
        owner = add_user(session)
        target = add_user(session, phone="+998900040001")
        add_staff_row(session, shop, owner, role=ShopRole.OWNER)
        state = {
            "shop_id": shop.id,
            "owner_id": owner.id,
            "target_id": target.id,
            "target_phone": target.phone,
        }
        session.commit()

    def worker(session: Session, label: str) -> ParallelShopOutcome:
        result = add_staff(
            session,
            shop_id=ShopId(state["shop_id"]),
            actor_user_id=UserId(state["owner_id"]),
            phone=state["target_phone"],
            role=ShopRole.CASHIER,
            now=NOW,
        )
        if result.error is not None:
            return ParallelShopOutcome(
                label=label,
                kind="domain_error",
                error_code=result.error,
            )
        return ParallelShopOutcome(
            label=label,
            kind="added",
            staff_outcome=result.staff.outcome,
        )

    outcomes = run_parallel_shop_workers(session_factory, ("first", "second"), worker)

    assert_no_unexpected_outcomes(outcomes)
    assert all(outcome.session_usable for outcome in outcomes), (
        f"{_LOCK_ORDER_DIAGNOSTIC}: sessions were not usable after parallel add; "
        f"outcomes={outcomes!r}"
    )
    assert {outcome.kind for outcome in outcomes} == {"added"}
    assert {outcome.staff_outcome for outcome in outcomes} == {
        AddStaffOutcome.ADDED,
        AddStaffOutcome.ALREADY_ACTIVE,
    }

    with session_factory() as session:
        assert staff_count_for_user(session, state["shop_id"], state["target_id"]) == 1
        assert event_count(session, ShopStaffEvent, ShopStaffAction.ADDED.value) == 1
        assert session.scalar(select(1)) == 1


@pytest.mark.integration
def test_parallel_suspend_shop_and_add_staff_serialize_shop_mutation(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        shop = add_shop_row(session, status=ShopStatus.ACTIVE)
        owner = add_user(session)
        target = add_user(session, phone="+998900040002")
        add_staff_row(session, shop, owner, role=ShopRole.OWNER)
        state = {
            "shop_id": shop.id,
            "owner_id": owner.id,
            "target_id": target.id,
            "target_phone": target.phone,
        }
        session.commit()

    def worker(session: Session, label: str) -> ParallelShopOutcome:
        if label == "suspend":
            result = suspend_shop(
                session,
                shop_id=ShopId(state["shop_id"]),
                actor_user_id=None,
                reason="manual hold",
                now=NOW,
            )
            if result.error is not None:
                return ParallelShopOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=result.error,
                )
            return ParallelShopOutcome(
                label=label,
                kind="suspended",
                status_outcome=result.transition.outcome,
            )

        result = add_staff(
            session,
            shop_id=ShopId(state["shop_id"]),
            actor_user_id=UserId(state["owner_id"]),
            phone=state["target_phone"],
            role=ShopRole.CASHIER,
            now=NOW,
        )
        if result.error is not None:
            return ParallelShopOutcome(
                label=label,
                kind="domain_error",
                error_code=result.error,
            )
        return ParallelShopOutcome(
            label=label,
            kind="added",
            staff_outcome=result.staff.outcome,
        )

    outcomes = run_parallel_shop_workers(session_factory, ("suspend", "add"), worker)

    assert_no_unexpected_outcomes(outcomes)
    assert all(outcome.session_usable for outcome in outcomes), (
        f"{_LOCK_ORDER_DIAGNOSTIC}: sessions were not usable after suspend/add; "
        f"outcomes={outcomes!r}"
    )
    suspend_outcome = only_outcome_for_label(outcomes, "suspend")
    add_outcome = only_outcome_for_label(outcomes, "add")
    assert suspend_outcome.kind == "suspended"
    assert suspend_outcome.status_outcome is ShopStatusTransitionOutcome.TRANSITIONED

    with session_factory() as session:
        shop = session.get(Shop, state["shop_id"])
        assert shop is not None
        assert shop.status == ShopStatus.SUSPENDED.value
        assert (
            event_count(session, ShopStatusEvent, ShopStatusAction.SUSPENDED.value) == 1
        )

        target_staff_count = staff_count_for_user(
            session,
            state["shop_id"],
            state["target_id"],
        )
        if add_outcome.kind == "added":
            assert add_outcome.staff_outcome is AddStaffOutcome.ADDED
            assert target_staff_count == 1
            assert (
                event_count(session, ShopStaffEvent, ShopStaffAction.ADDED.value) == 1
            )
        else:
            assert add_outcome.kind == "domain_error"
            assert add_outcome.error_code is ErrorCode.SHOP_SUSPENDED
            assert target_staff_count == 0
            assert (
                event_count(session, ShopStaffEvent, ShopStaffAction.ADDED.value) == 0
            )


def run_parallel_shop_workers(
    session_factory,
    labels: tuple[str, str],
    worker,
) -> list[ParallelShopOutcome]:
    start_barrier = Barrier(len(labels), timeout=_BARRIER_TIMEOUT_SECONDS)

    def wrapped_worker(label: str) -> ParallelShopOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
                outcome = worker(session, label)
                session_usable = session.scalar(select(1)) == 1
                session.commit()
                return replace(outcome, session_usable=session_usable)
            except BrokenBarrierError:
                session.rollback()
                return ParallelShopOutcome(
                    label=label,
                    kind="unexpected",
                    exception_class="BrokenBarrierError",
                )
        except Exception as exc:
            session.rollback()
            return ParallelShopOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=len(labels))
    try:
        futures = [executor.submit(wrapped_worker, label) for label in labels]
        done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail(
                f"{_LOCK_ORDER_DIAGNOSTIC}: parallel shop mutation timed out",
                pytrace=False,
            )
        return [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def assert_last_owner_race_outcomes(
    outcomes: list[ParallelShopOutcome],
    *,
    success_kind: str | set[str],
) -> None:
    assert_no_unexpected_outcomes(outcomes)
    expected_success_kinds = (
        {success_kind} if isinstance(success_kind, str) else success_kind
    )
    successes = [
        outcome for outcome in outcomes if outcome.kind in expected_success_kinds
    ]
    last_owner_errors = [
        outcome
        for outcome in outcomes
        if outcome.kind == "domain_error" and outcome.error_code is ErrorCode.LAST_OWNER
    ]
    assert len(successes) == 1, (
        f"{_LOCK_ORDER_DIAGNOSTIC}: expected exactly one owner-reducing success; "
        f"outcomes={outcomes!r}"
    )
    assert len(last_owner_errors) == 1, (
        f"{_LOCK_ORDER_DIAGNOSTIC}: expected exactly one LAST_OWNER loser; "
        f"outcomes={outcomes!r}"
    )
    assert all(outcome.session_usable for outcome in outcomes), (
        f"{_LOCK_ORDER_DIAGNOSTIC}: sessions were not usable; outcomes={outcomes!r}"
    )


def assert_no_unexpected_outcomes(outcomes: list[ParallelShopOutcome]) -> None:
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    assert unexpected == [], (
        f"{_LOCK_ORDER_DIAGNOSTIC}: unexpected parallel outcomes; outcomes={outcomes!r}"
    )


def assert_active_owner_count_is_at_least_one(
    session: Session,
    shop_id: UUID,
    outcomes: list[ParallelShopOutcome],
) -> None:
    owner_count = active_owner_count(session, shop_id)
    assert owner_count >= 1, (
        f"{_LOCK_ORDER_DIAGNOSTIC}: zero-owner state reached; outcomes={outcomes!r}"
    )


def only_outcome_for_label(
    outcomes: list[ParallelShopOutcome],
    label: str,
) -> ParallelShopOutcome:
    matching = [outcome for outcome in outcomes if outcome.label == label]
    assert len(matching) == 1
    return matching[0]


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
        name="Concurrency Shop",
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


def active_owner_count(session: Session, shop_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ShopStaff)
            .where(
                ShopStaff.shop_id == shop_id,
                ShopStaff.role == ShopRole.OWNER.value,
                ShopStaff.is_active.is_(True),
            )
        )
        or 0
    )


def staff_count_for_user(session: Session, shop_id: UUID, user_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ShopStaff)
            .where(
                ShopStaff.shop_id == shop_id,
                ShopStaff.user_id == user_id,
            )
        )
        or 0
    )


def event_count(session: Session, model, action: str) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(model).where(model.action == action)
        )
        or 0
    )
