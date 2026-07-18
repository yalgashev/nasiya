from unittest.mock import Mock, patch

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import (
    Base,
    create_database_engine,
    create_database_session_dependency,
    create_database_session_factory,
)
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-db-settings"


def test_database_engine_is_created_from_settings_without_connecting() -> None:
    settings = Settings(
        app_environment="testing",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )
    with patch.object(Engine, "connect") as connect_mock:
        engine = create_database_engine(settings)
        connect_mock.assert_not_called()

    expected_url = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"
    assert engine.url.render_as_string(hide_password=False) == expected_url


def test_database_engine_uses_settings_database_url() -> None:
    settings = Settings(
        app_environment="testing",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )
    with patch("app.db.create_engine") as create_engine_mock:
        create_database_engine(settings)
        create_engine_mock.assert_called_once_with(settings.database_url)


def test_database_session_factory_is_created_from_engine_without_connecting() -> None:
    settings = Settings(
        app_environment="testing",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )
    engine = create_database_engine(settings)

    with patch.object(Engine, "connect") as connect_mock, patch(
        "app.db.sessionmaker"
    ) as sessionmaker_mock:
        create_database_session_factory(engine)

    connect_mock.assert_not_called()
    sessionmaker_mock.assert_called_once_with(bind=engine, class_=Session)


def test_database_session_dependency_does_not_open_session_when_created() -> None:
    session_factory = Mock()

    create_database_session_dependency(session_factory)

    session_factory.assert_not_called()


def test_database_session_dependency_commits_and_closes_on_success() -> None:
    session = Mock(spec=Session)
    session_factory = Mock(return_value=session)
    dependency = create_database_session_dependency(session_factory)

    session_generator = dependency()
    assert next(session_generator) is session

    try:
        next(session_generator)
    except StopIteration:
        pass

    session_factory.assert_called_once_with()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_database_session_dependency_rolls_back_and_closes_on_error() -> None:
    session = Mock(spec=Session)
    session_factory = Mock(return_value=session)
    dependency = create_database_session_dependency(session_factory)
    error = RuntimeError("boom")

    session_generator = dependency()
    assert next(session_generator) is session

    try:
        session_generator.throw(error)
    except RuntimeError as exc:
        assert exc is error

    session_factory.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()


def test_database_session_dependency_uses_new_session_per_request() -> None:
    sessions = [Mock(spec=Session), Mock(spec=Session)]
    session_factory = Mock(side_effect=sessions)
    dependency = create_database_session_dependency(session_factory)

    first_generator = dependency()
    second_generator = dependency()

    assert next(first_generator) is sessions[0]
    assert next(second_generator) is sessions[1]


def test_declarative_base_metadata_is_initially_empty() -> None:
    assert len(Base.metadata.tables) == 0
