from collections.abc import Generator

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth import password_service
from app.auth.models import User
from app.auth.service import (
    CreateUserError,
    authenticate,
    create_user,
    get_by_phone,
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


def count_users(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(User)).scalar_one()


def commit_user(
    session: Session,
    phone: str,
    raw_password: str = "Password123",
    is_active: bool = True,
) -> User:
    result = create_user(session, phone, raw_password, is_active=is_active)
    assert result.succeeded is True
    assert result.user is not None
    session.commit()
    return result.user


@pytest.mark.integration
def test_create_user_normalizes_phone_hashes_password_and_does_not_commit(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    result = create_user(db_session, "90 123-45-67", "Password123")

    assert result.succeeded is True
    assert result.error is None
    assert result.user is not None
    assert result.user.phone == "+998901234567"
    assert result.user.password_hash is not None
    assert result.user.password_hash != "Password123"
    assert password_service.verify_password("Password123", result.user.password_hash)
    assert count_users(m2_test_database) == 0

    db_session.commit()

    assert count_users(m2_test_database) == 1


@pytest.mark.integration
def test_create_user_duplicate_phone_returns_domain_result(
    db_session: Session,
) -> None:
    commit_user(db_session, "+998901234567")

    result = create_user(db_session, "901234567", "Password123")

    assert result.user is None
    assert result.error == CreateUserError.DUPLICATE_PHONE


@pytest.mark.integration
def test_get_by_phone_returns_user_by_normalized_phone(db_session: Session) -> None:
    created_user = commit_user(db_session, "901234567")

    found_user = get_by_phone(db_session, "+998901234567")

    assert found_user is created_user


@pytest.mark.integration
@pytest.mark.parametrize(
    ("phone_input", "raw_password"),
    [
        ("not-a-phone", "Password123"),
        ("+998901234568", "Password123"),
        ("+998901234567", "WrongPassword123"),
    ],
)
def test_authenticate_returns_none_for_invalid_missing_or_wrong_password(
    db_session: Session,
    phone_input: str,
    raw_password: str,
) -> None:
    commit_user(db_session, "+998901234567")

    assert authenticate(db_session, phone_input, raw_password) is None


@pytest.mark.integration
def test_authenticate_returns_none_when_user_has_no_password_hash(
    db_session: Session,
) -> None:
    db_session.add(User(phone="+998901234567", password_hash=None, is_active=True))
    db_session.commit()

    assert authenticate(db_session, "+998901234567", "Password123") is None


@pytest.mark.integration
def test_authenticate_returns_none_for_inactive_user(db_session: Session) -> None:
    commit_user(db_session, "+998901234567", is_active=False)

    assert authenticate(db_session, "+998901234567", "Password123") is None


@pytest.mark.integration
def test_authenticate_returns_user_for_valid_login(db_session: Session) -> None:
    created_user = commit_user(db_session, "+998901234567")

    authenticated_user = authenticate(db_session, "901234567", "Password123")

    assert authenticated_user is created_user


@pytest.mark.integration
def test_authenticate_saves_new_hash_without_committing(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch,
) -> None:
    created_user = commit_user(db_session, "+998901234567")
    old_hash = created_user.password_hash
    new_hash = password_service.hash_password("Password123")
    monkeypatch.setattr(
        password_service,
        "verify_and_update",
        lambda raw_password, stored_hash: (True, new_hash),
    )

    authenticated_user = authenticate(db_session, "+998901234567", "Password123")

    assert authenticated_user is created_user
    assert created_user.password_hash == new_hash
    with create_database_session_factory(m2_test_database)() as reader_session:
        stored_hash_before_commit = reader_session.scalar(
            select(User.password_hash).where(User.id == created_user.id)
        )
    assert stored_hash_before_commit == old_hash

    db_session.commit()

    with create_database_session_factory(m2_test_database)() as reader_session:
        stored_hash_after_commit = reader_session.scalar(
            select(User.password_hash).where(User.id == created_user.id)
        )
    assert stored_hash_after_commit == new_hash


@pytest.mark.integration
def test_authenticate_uses_dummy_verify_for_missing_user(
    db_session: Session,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def record_dummy_verify(raw_password: str) -> None:
        calls.append(raw_password)

    monkeypatch.setattr(
        password_service,
        "verify_missing_user_password",
        record_dummy_verify,
    )

    assert authenticate(db_session, "+998901234567", "Password123") is None
    assert calls == ["Password123"]
