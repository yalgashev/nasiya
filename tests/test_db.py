from unittest.mock import patch

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import Base, create_database_engine, create_database_session_factory
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


def test_declarative_base_metadata_is_initially_empty() -> None:
    assert len(Base.metadata.tables) == 0
