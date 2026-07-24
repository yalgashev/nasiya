from collections.abc import Generator
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
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


def add_customer(session: Session, user: User, status: str) -> Customer:
    customer = Customer(user_id=user.id, onboarding_status=status)
    session.add(customer)
    session.flush()
    return customer


def count_customers(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Customer)) or 0


def count_users_by_id(session: Session, user: User) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(User).where(User.id == user.id),
        )
        or 0
    )


@pytest.mark.integration
def test_customer_unique_user_draft_allows_one_per_user_and_separate_users(
    db_session: Session,
) -> None:
    first_user = add_user(db_session, "+998900000001")
    second_user = add_user(db_session, "+998900000002")

    first_customer = add_customer(
        db_session,
        first_user,
        CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )

    assert first_customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    assert count_customers(db_session) == 1

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_customer(db_session, first_user, CUSTOMER_ONBOARDING_STATUS_DRAFT)

    second_customer = add_customer(
        db_session,
        second_user,
        CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )

    assert second_customer.user_id == second_user.id
    assert count_customers(db_session) == 2


@pytest.mark.integration
@pytest.mark.parametrize("status", ["active", "", "pending"])
def test_customer_status_check_rejects_non_draft_values(
    db_session: Session,
    status: str,
) -> None:
    user = add_user(db_session, "+998900000003")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_customer(db_session, user, status)

    assert count_customers(db_session) == 0


@pytest.mark.integration
def test_customer_requires_existing_user(db_session: Session) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            customer = Customer(
                user_id=uuid4(),
                onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
            )
            db_session.add(customer)
            db_session.flush()

    assert count_customers(db_session) == 0


@pytest.mark.integration
def test_customer_restricts_parent_user_delete_until_customer_deleted(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000004")
    customer = add_customer(db_session, user, CUSTOMER_ONBOARDING_STATUS_DRAFT)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(user)
            db_session.flush()

    assert db_session.get(User, user.id) is user
    assert db_session.get(Customer, customer.id) is customer

    db_session.delete(customer)
    db_session.flush()
    db_session.delete(user)
    db_session.flush()

    assert db_session.get(Customer, customer.id) is None
    assert db_session.get(User, user.id) is None


@pytest.mark.integration
def test_customer_restricts_direct_parent_user_delete_and_recovers_transaction(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900000005")
    customer = add_customer(db_session, user, CUSTOMER_ONBOARDING_STATUS_DRAFT)
    customer_id = customer.id

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(delete(User).where(User.id == user.id))
            db_session.flush()

    assert count_users_by_id(db_session, user) == 1
    assert count_customers(db_session) == 1

    stored_customer = db_session.get(Customer, customer_id)
    assert stored_customer is not None
    assert stored_customer.user_id == user.id

    assert db_session.scalar(select(User.id).where(User.id == user.id)) == user.id

    db_session.delete(stored_customer)
    db_session.flush()
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.flush()

    assert db_session.get(Customer, customer_id) is None
    assert count_users_by_id(db_session, user) == 0


def test_customer_user_relationships_do_not_delete_cascade() -> None:
    for mapper in (User.__mapper__, Customer.__mapper__):
        for relationship in mapper.relationships:
            if relationship.mapper.class_ not in {User, Customer}:
                continue

            assert "delete" not in relationship.cascade
            assert "delete-orphan" not in relationship.cascade
