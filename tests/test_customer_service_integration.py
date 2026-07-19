from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from inspect import signature
from threading import Barrier, BrokenBarrierError, Lock, get_ident
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer.repository import get_customer_by_user_id
from app.customer.service import (
    CustomerDraftStartError,
    get_current_customer_draft_state,
    start_customer_draft,
)
from app.db import create_database_session_factory

CONCURRENCY_BARRIER_TIMEOUT_SECONDS = 10
CONCURRENCY_RESULT_TIMEOUT_SECONDS = 20
POSTGRES_STATEMENT_TIMEOUT_MILLISECONDS = 10_000


@dataclass(frozen=True, slots=True)
class ParallelStartResult:
    customer_id: UUID
    user_id: UUID
    status: str
    row_count: int
    continuation_user_id: UUID


class CallerOwnedSession:
    def commit(self) -> None:
        raise AssertionError("service must not commit")

    def rollback(self) -> None:
        raise AssertionError("service must not rollback")

    def close(self) -> None:
        raise AssertionError("service must not close the session")


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def count_customers(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Customer)) or 0


def count_users(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User)) or 0


def count_customers_by_user_id(session: Session, user_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Customer)
            .where(Customer.user_id == user_id),
        )
        or 0
    )


def add_customer(session: Session, user: User) -> Customer:
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )
    session.add(customer)
    session.flush()
    return customer


def _is_customer_insert_statement(statement: str) -> bool:
    return statement.lstrip().upper().startswith("INSERT INTO CUSTOMERS")


def _wait_at_barrier(barrier: Barrier, message: str) -> None:
    try:
        barrier.wait()
    except BrokenBarrierError as exc:
        raise AssertionError(message) from exc


@pytest.mark.integration
def test_start_customer_draft_creates_and_reuses_same_draft(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000201")

    first_customer = start_customer_draft(db_session, user.id)
    second_customer = start_customer_draft(db_session, user.id)

    assert second_customer is first_customer
    assert first_customer.user_id == user.id
    assert first_customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    assert count_customers(db_session) == 1


@pytest.mark.integration
def test_start_customer_draft_is_sequentially_idempotent_without_rollback(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000209")

    first_customer = start_customer_draft(db_session, user.id)
    first_customer_id = first_customer.id
    first_created_at = first_customer.created_at
    first_updated_at = first_customer.updated_at
    first_status = first_customer.onboarding_status

    for _ in range(3):
        customer = start_customer_draft(db_session, user.id)

        assert customer is first_customer
        assert customer.id == first_customer_id
        assert customer.created_at == first_created_at
        assert customer.updated_at == first_updated_at
        assert customer.onboarding_status == first_status
        assert count_customers(db_session) == 1

    db_session.expire_all()
    persisted_customer = get_customer_by_user_id(db_session, user.id)

    assert persisted_customer is not None
    assert persisted_customer.id == first_customer_id
    assert persisted_customer.created_at == first_created_at
    assert persisted_customer.updated_at == first_updated_at
    assert persisted_customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    assert count_customers(db_session) == 1

    continuation_user = add_user(db_session, "+998900000210")

    assert continuation_user.id is not None
    assert count_customers(db_session) == 1


@pytest.mark.integration
def test_start_customer_draft_does_not_commit(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = add_user(first_session, "+998900000202")

        start_customer_draft(first_session, user.id)

        assert count_customers(second_session) == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


def test_start_customer_draft_leaves_transaction_and_session_to_caller(
    monkeypatch,
) -> None:
    user_id = uuid4()
    expected_customer = Customer(
        id=uuid4(),
        user_id=user_id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )

    def fake_create_customer_draft_if_missing(
        _session: CallerOwnedSession,
        _user_id: UUID,
        _now: object,
    ) -> Customer:
        return expected_customer

    monkeypatch.setattr(
        "app.customer.service.create_customer_draft_if_missing",
        fake_create_customer_draft_if_missing,
    )

    customer = start_customer_draft(CallerOwnedSession(), user_id)  # type: ignore[arg-type]

    assert customer is expected_customer


@pytest.mark.integration
def test_start_customer_draft_respects_caller_rollback(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    try:
        user = add_user(first_session, "+998900000214")
        user_id = user.id

        start_customer_draft(first_session, user_id)

        assert count_customers_by_user_id(first_session, user_id) == 1
        first_session.rollback()
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        assert count_customers_by_user_id(second_session, user_id) == 0
    finally:
        second_session.rollback()
        second_session.close()


@pytest.mark.integration
def test_start_customer_draft_respects_caller_commit(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    try:
        user = add_user(first_session, "+998900000215")
        user_id = user.id
        customer = start_customer_draft(first_session, user_id)
        customer_id = customer.id

        first_session.commit()
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        persisted_customer = get_customer_by_user_id(second_session, user_id)

        assert persisted_customer is not None
        assert persisted_customer.id == customer_id
        assert persisted_customer.user_id == user_id
        assert persisted_customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
        assert count_customers_by_user_id(second_session, user_id) == 1
    finally:
        second_session.rollback()
        second_session.close()


@pytest.mark.integration
def test_start_customer_draft_keeps_transaction_usable_after_duplicate_conflict(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000216")

    first_customer = start_customer_draft(db_session, user.id)
    second_customer = start_customer_draft(db_session, user.id)
    current_customer_count = count_customers(db_session)
    continuation_user = add_user(db_session, "+998900000217")

    assert second_customer is first_customer
    assert current_customer_count == 1
    assert continuation_user.id is not None
    assert count_users(db_session) == 2
    assert count_customers(db_session) == 1


@pytest.mark.integration
def test_start_customer_draft_parallel_duplicate_create_is_idempotent(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    try:
        user = add_user(setup_session, "+998900000211")
        user_id = user.id
        setup_session.commit()
    finally:
        setup_session.close()

    start_barrier = Barrier(
        2,
        timeout=CONCURRENCY_BARRIER_TIMEOUT_SECONDS,
    )
    insert_barrier = Barrier(
        2,
        timeout=CONCURRENCY_BARRIER_TIMEOUT_SECONDS,
    )
    insert_thread_ids: list[int] = []
    insert_thread_ids_lock = Lock()

    def synchronize_customer_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not _is_customer_insert_statement(statement):
            return

        with insert_thread_ids_lock:
            insert_thread_ids.append(get_ident())
        _wait_at_barrier(
            insert_barrier,
            "both customer insert statements did not reach the barrier",
        )

    def run_worker(worker_index: int) -> ParallelStartResult:
        session = session_factory()
        try:
            session.execute(
                text(
                    "SET LOCAL statement_timeout = "
                    f"{POSTGRES_STATEMENT_TIMEOUT_MILLISECONDS}",
                ),
            )
            _wait_at_barrier(
                start_barrier,
                "both customer draft workers did not start together",
            )

            customer = start_customer_draft(session, user_id)
            continuation_user = add_user(
                session,
                f"+99890000022{worker_index}",
            )
            row_count = count_customers(session)
            session.commit()

            return ParallelStartResult(
                customer_id=customer.id,
                user_id=customer.user_id,
                status=customer.onboarding_status,
                row_count=row_count,
                continuation_user_id=continuation_user.id,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    event.listen(
        m2_test_database,
        "before_cursor_execute",
        synchronize_customer_insert,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run_worker, index) for index in range(2)]
            results = [
                future.result(timeout=CONCURRENCY_RESULT_TIMEOUT_SECONDS)
                for future in futures
            ]
    finally:
        event.remove(
            m2_test_database,
            "before_cursor_execute",
            synchronize_customer_insert,
        )

    assert len(insert_thread_ids) == 2
    assert len(set(insert_thread_ids)) == 2
    assert {result.user_id for result in results} == {user_id}
    assert {result.status for result in results} == {
        CUSTOMER_ONBOARDING_STATUS_DRAFT,
    }
    assert {result.row_count for result in results} == {1}
    assert len({result.customer_id for result in results}) == 1
    assert len({result.continuation_user_id for result in results}) == 2

    final_session = session_factory()
    try:
        persisted_customer = get_customer_by_user_id(final_session, user_id)

        assert persisted_customer is not None
        assert count_customers(final_session) == 1
        assert count_users(final_session) == 3
        assert persisted_customer.id == results[0].customer_id
        assert persisted_customer.id == results[1].customer_id
        assert persisted_customer.user_id == user_id
        assert persisted_customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    finally:
        final_session.rollback()
        final_session.close()


def test_start_customer_draft_wraps_raw_database_errors(monkeypatch) -> None:
    def fail_create_customer_draft_if_missing(*_args) -> Customer:
        raise SQLAlchemyError("raw database secret")

    monkeypatch.setattr(
        "app.customer.service.create_customer_draft_if_missing",
        fail_create_customer_draft_if_missing,
    )

    with pytest.raises(CustomerDraftStartError) as exc_info:
        start_customer_draft(object(), object())  # type: ignore[arg-type]

    assert "raw database secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


@pytest.mark.integration
def test_get_current_customer_draft_state_returns_none_when_not_started(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000203")

    view = get_current_customer_draft_state(db_session, user)

    assert view is None


@pytest.mark.integration
def test_get_current_customer_draft_state_returns_safe_view_for_current_user(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000204")
    customer = add_customer(db_session, user)

    view = get_current_customer_draft_state(db_session, user)

    assert view is not None
    assert not isinstance(view, Customer)
    assert view.masked_phone == mask_phone_for_display(user.phone)
    assert view.onboarding_status_display == "draft"
    assert str(customer.id) not in repr(view)
    assert str(user.id) not in repr(view)
    assert user.phone not in repr(view)


@pytest.mark.integration
def test_get_current_customer_draft_state_uses_current_user_ownership(
    db_session: Session,
) -> None:
    first_user = add_user(db_session, "+998900000205")
    second_user = add_user(db_session, "+998900000206")
    add_customer(db_session, first_user)

    view = get_current_customer_draft_state(db_session, second_user)

    assert view is None


@pytest.mark.integration
def test_get_current_customer_draft_state_isolates_two_users(
    db_session: Session,
) -> None:
    first_user = add_user(db_session, "+998900000212")
    second_user = add_user(db_session, "+998900000213")
    first_customer = start_customer_draft(db_session, first_user.id)
    second_customer = start_customer_draft(db_session, second_user.id)

    first_view = get_current_customer_draft_state(db_session, first_user)
    second_view = get_current_customer_draft_state(db_session, second_user)

    assert first_customer.user_id == first_user.id
    assert second_customer.user_id == second_user.id
    assert first_customer.user_id != second_customer.user_id
    assert first_customer.id != second_customer.id
    assert count_customers(db_session) == 2
    assert first_view is not None
    assert second_view is not None
    assert first_view.masked_phone == mask_phone_for_display(first_user.phone)
    assert second_view.masked_phone == mask_phone_for_display(second_user.phone)
    assert first_view.masked_phone != second_view.masked_phone
    assert first_view.onboarding_status_display == "draft"
    assert second_view.onboarding_status_display == "draft"
    assert second_user.phone not in repr(first_view)
    assert first_user.phone not in repr(second_view)

    for view in (first_view, second_view):
        assert str(first_customer.id) not in repr(view)
        assert str(second_customer.id) not in repr(view)
        assert str(first_user.id) not in repr(view)
        assert str(second_user.id) not in repr(view)


def test_current_customer_draft_state_accepts_no_customer_identifier() -> None:
    assert list(signature(get_current_customer_draft_state).parameters) == [
        "session",
        "current_user",
    ]


def test_get_current_customer_draft_state_leaves_transaction_and_session_to_caller(
    monkeypatch,
) -> None:
    user = User(id=uuid4(), phone="+998900000207")
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )

    def fake_get_customer_by_user_id(
        _session: CallerOwnedSession,
        _user_id: object,
    ) -> Customer:
        return customer

    monkeypatch.setattr(
        "app.customer.service.get_customer_by_user_id",
        fake_get_customer_by_user_id,
    )

    view = get_current_customer_draft_state(CallerOwnedSession(), user)  # type: ignore[arg-type]

    assert view is not None
    assert view.masked_phone == mask_phone_for_display(user.phone)


@pytest.mark.integration
def test_customer_repository_service_happy_path_persists_safe_state(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    try:
        user = add_user(first_session, "+998900000208")
        user_id = user.id
        raw_phone = user.phone

        customer = start_customer_draft(first_session, user_id)
        found_customer = get_customer_by_user_id(first_session, user_id)
        view = get_current_customer_draft_state(first_session, user)

        assert found_customer is customer
        assert customer.user_id == user_id
        assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
        assert count_customers(first_session) == 1
        assert view is not None
        assert view.masked_phone == mask_phone_for_display(raw_phone)
        assert view.onboarding_status_display == "draft"
        assert raw_phone not in repr(view)
        assert str(user_id) not in repr(view)
        assert str(customer.id) not in repr(view)

        first_session.commit()
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        persisted_customer = get_customer_by_user_id(second_session, user_id)
        persisted_user = second_session.get(User, user_id)

        assert persisted_customer is not None
        assert persisted_user is not None
        assert persisted_customer.user_id == user_id
        assert persisted_customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT

        persisted_view = get_current_customer_draft_state(
            second_session,
            persisted_user,
        )

        assert persisted_view is not None
        assert persisted_view.masked_phone == mask_phone_for_display(raw_phone)
        assert raw_phone not in repr(persisted_view)
        assert str(user_id) not in repr(persisted_view)
        assert str(persisted_customer.id) not in repr(persisted_view)
    finally:
        second_session.rollback()
        second_session.close()
