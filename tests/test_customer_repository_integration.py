from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer.repository import (
    create_customer_draft_if_missing,
    get_customer_by_user_id,
)
from app.db import create_database_session_factory


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


def add_customer(session: Session, user: User) -> Customer:
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )
    session.add(customer)
    session.flush()
    return customer


def count_customers(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Customer)) or 0


@pytest.mark.integration
def test_get_customer_by_user_id_returns_existing_customer(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000101")
    customer = add_customer(db_session, user)

    found_customer = get_customer_by_user_id(db_session, user.id)

    assert found_customer is customer


@pytest.mark.integration
def test_get_customer_by_user_id_does_not_return_other_users_customer(
    db_session: Session,
) -> None:
    first_user = add_user(db_session, "+998900000102")
    second_user = add_user(db_session, "+998900000103")
    first_customer = add_customer(db_session, first_user)

    found_customer = get_customer_by_user_id(db_session, second_user.id)

    assert found_customer is None
    assert get_customer_by_user_id(db_session, first_user.id) is first_customer


@pytest.mark.integration
def test_create_customer_draft_if_missing_creates_new_draft(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900000105")

    customer = create_customer_draft_if_missing(db_session, user.id, now)

    assert customer.user_id == user.id
    assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    assert customer.created_at == now
    assert customer.updated_at == now
    assert count_customers(db_session) == 1


@pytest.mark.integration
def test_create_customer_draft_if_missing_returns_existing_customer(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900000106")
    existing_customer = add_customer(db_session, user)

    customer = create_customer_draft_if_missing(db_session, user.id, now)

    assert customer is existing_customer
    assert count_customers(db_session) == 1


@pytest.mark.integration
def test_create_customer_draft_if_missing_is_sequentially_idempotent(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900000107")

    first_customer = create_customer_draft_if_missing(db_session, user.id, now)
    second_customer = create_customer_draft_if_missing(db_session, user.id, now)

    assert second_customer is first_customer
    assert count_customers(db_session) == 1


@pytest.mark.integration
def test_repeated_customer_draft_insert_keeps_outer_transaction_usable(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    first_user = add_user(db_session, "+998900000108")
    second_user = add_user(db_session, "+998900000109")

    create_customer_draft_if_missing(db_session, first_user.id, now)
    create_customer_draft_if_missing(db_session, first_user.id, now)
    second_customer = create_customer_draft_if_missing(db_session, second_user.id, now)

    assert second_customer.user_id == second_user.id
    assert count_customers(db_session) == 2


@pytest.mark.integration
def test_create_customer_draft_if_missing_does_not_commit(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = add_user(first_session, "+998900000110")

        create_customer_draft_if_missing(first_session, user.id, now)

        assert count_customers(second_session) == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_get_customer_by_user_id_returns_none_when_missing(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000104")

    found_customer = get_customer_by_user_id(db_session, user.id)

    assert found_customer is None
