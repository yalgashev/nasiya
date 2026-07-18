from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app import cli
from app.auth import password_service
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-cli"


def make_settings(
    database_url: str,
    app_environment: str = "development",
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment=app_environment,
        debug=False,
        database_url=database_url,
        session_cookie_secure=app_environment == "production",
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def patch_getpass(monkeypatch, *passwords: str) -> None:
    password_iterator: Iterator[str] = iter(passwords)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: next(password_iterator))


def count_users(engine: Engine) -> int:
    with create_database_session_factory(engine)() as session:
        return session.scalar(select(func.count()).select_from(User))


def get_single_user(engine: Engine) -> User:
    with create_database_session_factory(engine)() as session:
        return session.scalars(select(User)).one()


@pytest.mark.integration
def test_create_local_user_creates_once_and_second_run_does_not_replace_password(
    m2_test_database: Engine,
    test_database_url: str,
    monkeypatch,
    capsys,
) -> None:
    settings = make_settings(test_database_url)
    patch_getpass(monkeypatch, "Password123", "Password123")

    exit_code = cli.main(
        ["create-local-user", "--phone", "90 123-45-67"],
        settings=settings,
    )

    assert exit_code == 0
    created_user = get_single_user(m2_test_database)
    original_hash = created_user.password_hash
    assert created_user.phone == "+998901234567"
    assert original_hash is not None
    assert password_service.verify_password("Password123", original_hash)

    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        lambda _: pytest.fail("existing user should not prompt without reset"),
    )
    exit_code = cli.main(
        ["create-local-user", "--phone", "901234567"],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert count_users(m2_test_database) == 1
    assert get_single_user(m2_test_database).password_hash == original_hash
    assert "already exists" in captured.out
    assert "Password123" not in captured.out
    assert original_hash not in captured.out


@pytest.mark.integration
def test_create_local_user_reset_password_updates_existing_user_only(
    m2_test_database: Engine,
    test_database_url: str,
    monkeypatch,
    capsys,
) -> None:
    settings = make_settings(test_database_url)
    patch_getpass(monkeypatch, "Password123", "Password123")
    assert (
        cli.main(["create-local-user", "--phone", "901234567"], settings=settings)
        == 0
    )
    original_hash = get_single_user(m2_test_database).password_hash

    patch_getpass(monkeypatch, "NewPassword123", "NewPassword123")
    exit_code = cli.main(
        ["create-local-user", "--phone", "+998901234567", "--reset-password"],
        settings=settings,
    )

    captured = capsys.readouterr()
    updated_user = get_single_user(m2_test_database)
    assert exit_code == 0
    assert count_users(m2_test_database) == 1
    assert updated_user.password_hash != original_hash
    assert updated_user.password_hash is not None
    assert password_service.verify_password(
        "NewPassword123",
        updated_user.password_hash,
    )
    assert (
        password_service.verify_password("Password123", updated_user.password_hash)
        is False
    )
    assert "NewPassword123" not in captured.out
    assert updated_user.password_hash not in captured.out


@pytest.mark.integration
def test_create_local_user_fails_closed_in_production(
    m2_test_database: Engine,
    test_database_url: str,
    monkeypatch,
    capsys,
) -> None:
    settings = make_settings(test_database_url, app_environment="production")
    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        lambda _: pytest.fail("production command should fail before prompting"),
    )

    exit_code = cli.main(
        ["create-local-user", "--phone", "901234567"],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "local development" in captured.err
    assert count_users(m2_test_database) == 0


def test_create_local_user_does_not_accept_raw_password_argument(
    test_database_url: str,
) -> None:
    settings = make_settings(test_database_url)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "create-local-user",
                "--phone",
                "901234567",
                "--password",
                "Password123",
            ],
            settings=settings,
        )

    assert exc_info.value.code == 2
