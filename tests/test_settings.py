import pytest
from pydantic import ValidationError

from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-settings-only"
TEST_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya"
SETTINGS_ENV_KEYS = (
    "APP_ENVIRONMENT",
    "DEBUG",
    "DATABASE_URL",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_SECURE",
    "SESSION_TTL_DAYS",
    "ANONYMOUS_SESSION_TTL_MINUTES",
    "SESSION_TOUCH_INTERVAL_MINUTES",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
    "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    "LOGIN_RATE_LIMIT_PHONE_ATTEMPTS",
    "LOGIN_RATE_LIMIT_IP_ATTEMPTS",
    "RATE_LIMIT_HMAC_KEY",
)


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch) -> None:
    for key in SETTINGS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_settings_created_with_required_values() -> None:
    settings = Settings(
        _env_file=None,
        app_environment="production",
        debug=True,
        database_url=TEST_DATABASE_URL,
        session_cookie_secure=True,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )

    assert settings.app_environment == "production"
    assert settings.debug is True
    assert settings.database_url == TEST_DATABASE_URL
    assert settings.session_cookie_name == "nasiya_session"
    assert settings.session_cookie_secure is True
    assert settings.session_ttl_days == 30
    assert settings.anonymous_session_ttl_minutes == 30
    assert settings.session_touch_interval_minutes == 5
    assert settings.password_min_length == 8
    assert settings.password_max_length == 128
    assert settings.login_rate_limit_window_seconds == 900
    assert settings.login_rate_limit_phone_attempts == 5
    assert settings.login_rate_limit_ip_attempts == 20


def test_settings_requires_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_environment="development",
            debug=False,
            session_cookie_secure=False,
            rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        )


def test_settings_requires_rate_limit_hmac_key() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=TEST_DATABASE_URL,
            session_cookie_secure=False,
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("session_ttl_days", 0),
        ("session_ttl_days", -1),
        ("anonymous_session_ttl_minutes", 0),
        ("anonymous_session_ttl_minutes", -1),
        ("session_touch_interval_minutes", 0),
        ("session_touch_interval_minutes", -1),
        ("password_min_length", 0),
        ("password_min_length", -1),
        ("password_max_length", 0),
        ("password_max_length", -1),
        ("login_rate_limit_window_seconds", 0),
        ("login_rate_limit_window_seconds", -1),
        ("login_rate_limit_phone_attempts", 0),
        ("login_rate_limit_phone_attempts", -1),
        ("login_rate_limit_ip_attempts", 0),
        ("login_rate_limit_ip_attempts", -1),
    ],
)
def test_settings_requires_positive_ttl_and_limit_values(
    field_name: str, field_value: int
) -> None:
    values = {
        "database_url": TEST_DATABASE_URL,
        "session_cookie_secure": False,
        "rate_limit_hmac_key": TEST_RATE_LIMIT_HMAC_KEY,
        field_name: field_value,
    }

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_settings_rejects_password_max_length_below_min_length() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=TEST_DATABASE_URL,
            session_cookie_secure=False,
            rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
            password_min_length=12,
            password_max_length=8,
        )


def test_settings_rejects_empty_session_cookie_name() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=TEST_DATABASE_URL,
            session_cookie_secure=False,
            rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
            session_cookie_name=" ",
        )


def test_settings_rejects_short_rate_limit_hmac_key() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=TEST_DATABASE_URL,
            session_cookie_secure=False,
            rate_limit_hmac_key="too-short",
        )


def test_settings_rejects_insecure_session_cookie_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_environment="production",
            database_url=TEST_DATABASE_URL,
            session_cookie_secure=False,
            rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        )


def test_settings_allows_insecure_session_cookie_in_development() -> None:
    settings = Settings(
        _env_file=None,
        app_environment="development",
        database_url=TEST_DATABASE_URL,
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )

    assert settings.session_cookie_secure is False


def test_settings_uses_default_session_cookie_name() -> None:
    settings = Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )

    assert settings.session_cookie_name == "nasiya_session"


def test_create_app_accepts_explicit_settings() -> None:
    settings = Settings(
        app_environment="testing",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )
    app = create_app(settings=settings)

    assert app.state.settings is settings
