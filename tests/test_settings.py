import pytest
from pydantic import ValidationError

from app.main import create_app
from app.settings import ClientIpMode, Settings
from app.telegram.bot import TelegramBotUsername

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
    "TELEGRAM_LINK_RATE_LIMIT_WINDOW_SECONDS",
    "TELEGRAM_LINK_RATE_LIMIT_USER_ATTEMPTS",
    "TELEGRAM_LINK_RATE_LIMIT_PHONE_ATTEMPTS",
    "TELEGRAM_LINK_RATE_LIMIT_IP_ATTEMPTS",
    "RATE_LIMIT_HMAC_KEY",
    "TELEGRAM_BOT_USERNAME",
    "CLIENT_IP_MODE",
    "TRUSTED_PROXY_CIDRS",
)


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch) -> None:
    for key in SETTINGS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def make_settings(**overrides) -> Settings:
    values = {
        "database_url": TEST_DATABASE_URL,
        "session_cookie_secure": False,
        "rate_limit_hmac_key": TEST_RATE_LIMIT_HMAC_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def set_required_settings_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("RATE_LIMIT_HMAC_KEY", TEST_RATE_LIMIT_HMAC_KEY)


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
    assert settings.telegram_link_rate_limit_window_seconds == 900
    assert settings.telegram_link_rate_limit_user_attempts == 3
    assert settings.telegram_link_rate_limit_phone_attempts == 3
    assert settings.telegram_link_rate_limit_ip_attempts == 20
    assert settings.telegram_bot_username is None
    assert settings.client_ip_mode is ClientIpMode.DIRECT
    assert settings.trusted_proxy_cidrs == ()


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
        ("telegram_link_rate_limit_window_seconds", 0),
        ("telegram_link_rate_limit_window_seconds", -1),
        ("telegram_link_rate_limit_user_attempts", 0),
        ("telegram_link_rate_limit_user_attempts", -1),
        ("telegram_link_rate_limit_phone_attempts", 0),
        ("telegram_link_rate_limit_phone_attempts", -1),
        ("telegram_link_rate_limit_ip_attempts", 0),
        ("telegram_link_rate_limit_ip_attempts", -1),
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


def test_telegram_link_rate_limit_settings_use_approved_defaults() -> None:
    settings = make_settings(
        app_environment="production",
        session_cookie_secure=True,
    )

    assert settings.telegram_link_rate_limit_window_seconds == 900
    assert settings.telegram_link_rate_limit_user_attempts == 3
    assert settings.telegram_link_rate_limit_phone_attempts == 3
    assert settings.telegram_link_rate_limit_ip_attempts == 20
    assert settings.rate_limit_hmac_key.get_secret_value() == TEST_RATE_LIMIT_HMAC_KEY


def test_telegram_link_rate_limit_settings_can_be_overridden_from_env(
    monkeypatch,
) -> None:
    set_required_settings_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LINK_RATE_LIMIT_WINDOW_SECONDS", "120")
    monkeypatch.setenv("TELEGRAM_LINK_RATE_LIMIT_USER_ATTEMPTS", "4")
    monkeypatch.setenv("TELEGRAM_LINK_RATE_LIMIT_PHONE_ATTEMPTS", "5")
    monkeypatch.setenv("TELEGRAM_LINK_RATE_LIMIT_IP_ATTEMPTS", "30")

    settings = Settings(_env_file=None)

    assert settings.telegram_link_rate_limit_window_seconds == 120
    assert settings.telegram_link_rate_limit_user_attempts == 4
    assert settings.telegram_link_rate_limit_phone_attempts == 5
    assert settings.telegram_link_rate_limit_ip_attempts == 30


@pytest.mark.parametrize(
    ("env_key", "bad_value"),
    [
        ("TELEGRAM_LINK_RATE_LIMIT_WINDOW_SECONDS", ""),
        ("TELEGRAM_LINK_RATE_LIMIT_WINDOW_SECONDS", "0"),
        ("TELEGRAM_LINK_RATE_LIMIT_WINDOW_SECONDS", "-1"),
        ("TELEGRAM_LINK_RATE_LIMIT_WINDOW_SECONDS", "not-an-int"),
        ("TELEGRAM_LINK_RATE_LIMIT_USER_ATTEMPTS", ""),
        ("TELEGRAM_LINK_RATE_LIMIT_USER_ATTEMPTS", "0"),
        ("TELEGRAM_LINK_RATE_LIMIT_USER_ATTEMPTS", "-1"),
        ("TELEGRAM_LINK_RATE_LIMIT_USER_ATTEMPTS", "not-an-int"),
        ("TELEGRAM_LINK_RATE_LIMIT_PHONE_ATTEMPTS", ""),
        ("TELEGRAM_LINK_RATE_LIMIT_PHONE_ATTEMPTS", "0"),
        ("TELEGRAM_LINK_RATE_LIMIT_PHONE_ATTEMPTS", "-1"),
        ("TELEGRAM_LINK_RATE_LIMIT_PHONE_ATTEMPTS", "not-an-int"),
        ("TELEGRAM_LINK_RATE_LIMIT_IP_ATTEMPTS", ""),
        ("TELEGRAM_LINK_RATE_LIMIT_IP_ATTEMPTS", "0"),
        ("TELEGRAM_LINK_RATE_LIMIT_IP_ATTEMPTS", "-1"),
        ("TELEGRAM_LINK_RATE_LIMIT_IP_ATTEMPTS", "not-an-int"),
    ],
)
def test_invalid_telegram_link_rate_limit_env_values_fail_fast(
    monkeypatch,
    env_key: str,
    bad_value: str,
) -> None:
    set_required_settings_env(monkeypatch)
    monkeypatch.setenv(env_key, bad_value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_telegram_link_rate_limit_settings_do_not_add_out_of_scope_secrets() -> None:
    forbidden_fields = {
        "telegram_bot_token",
        "telegram_link_rate_limit_hmac_key",
        "telegram_link_token_ttl_seconds",
        "telegram_link_token_retention_days",
        "telegram_link_token_retention_seconds",
    }

    assert "rate_limit_hmac_key" in Settings.model_fields
    assert forbidden_fields.isdisjoint(Settings.model_fields)


def test_client_ip_settings_default_to_direct_without_proxy_allowlist() -> None:
    settings = make_settings()

    assert settings.client_ip_mode is ClientIpMode.DIRECT
    assert settings.trusted_proxy_cidrs == ()


def test_trusted_proxy_settings_parse_canonical_ipv4_and_ipv6_cidrs() -> None:
    settings = make_settings(
        client_ip_mode="trusted_proxy",
        trusted_proxy_cidrs=[
            "10.20.30.40/24",
            "2001:0DB8:0001::1/48",
            "10.20.30.0/24",
        ],
    )

    assert settings.client_ip_mode is ClientIpMode.TRUSTED_PROXY
    assert tuple(str(network) for network in settings.trusted_proxy_cidrs) == (
        "10.20.30.0/24",
        "2001:db8:1::/48",
    )


def test_trusted_proxy_settings_parse_comma_separated_env_value(monkeypatch) -> None:
    set_required_settings_env(monkeypatch)
    monkeypatch.setenv("CLIENT_IP_MODE", "trusted_proxy")
    monkeypatch.setenv(
        "TRUSTED_PROXY_CIDRS",
        "10.0.0.1/8, 2001:db8::1/32",
    )

    settings = Settings(_env_file=None)

    assert settings.client_ip_mode is ClientIpMode.TRUSTED_PROXY
    assert tuple(str(network) for network in settings.trusted_proxy_cidrs) == (
        "10.0.0.0/8",
        "2001:db8::/32",
    )


@pytest.mark.parametrize("mode", ["proxy", "trusted", "DIRECT", ""])
def test_client_ip_settings_reject_unknown_mode(mode: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(client_ip_mode=mode)


def test_trusted_proxy_mode_rejects_empty_allowlist() -> None:
    with pytest.raises(
        ValidationError,
        match="trusted_proxy mode requires a non-empty trusted_proxy_cidrs",
    ):
        make_settings(client_ip_mode="trusted_proxy")


def test_direct_mode_rejects_unused_proxy_allowlist() -> None:
    with pytest.raises(
        ValidationError,
        match="trusted_proxy_cidrs requires client_ip_mode=trusted_proxy",
    ):
        make_settings(
            client_ip_mode="direct",
            trusted_proxy_cidrs=["10.0.0.0/8"],
        )


@pytest.mark.parametrize(
    "cidrs",
    [
        ["0.0.0.0/0"],
        ["::/0"],
        ["not-a-cidr"],
        ["203.0.113.10"],
        [""],
        [123],
    ],
)
def test_trusted_proxy_settings_reject_invalid_or_wildcard_cidrs(
    cidrs: list[object],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(
            client_ip_mode="trusted_proxy",
            trusted_proxy_cidrs=cidrs,
        )


def test_trusted_proxy_settings_error_hides_invalid_input() -> None:
    raw_cidr = "sensitive.invalid.proxy/24"

    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            client_ip_mode="trusted_proxy",
            trusted_proxy_cidrs=[raw_cidr],
        )

    assert raw_cidr not in str(exc_info.value)


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


def test_telegram_bot_username_canonicalizes_to_lowercase_username() -> None:
    username = TelegramBotUsername("Nasiya_LinkBOT")

    assert username.as_username() == "nasiya_linkbot"
    assert str(username) == "nasiya_linkbot"
    assert repr(username) == "TelegramBotUsername('nasiya_linkbot')"


@pytest.mark.parametrize(
    "raw_username",
    [
        "",
        "@nasiya_bot",
        "https://t.me/nasiya_bot",
        "nasiya bot",
        " nasiya_bot",
        "nasiya_bot ",
        "nasi",
        "nasiya-bot",
        "насия_bot",
        "nasiya",
        "nasiya_bot_extra_name_that_is_too_long",
    ],
)
def test_telegram_bot_username_rejects_invalid_values(raw_username: str) -> None:
    with pytest.raises(ValueError):
        TelegramBotUsername(raw_username)


@pytest.mark.parametrize("raw_username", [None, ""])
def test_settings_treats_unset_or_empty_telegram_bot_username_as_none(
    raw_username: str | None,
) -> None:
    settings = (
        make_settings()
        if raw_username is None
        else make_settings(telegram_bot_username=raw_username)
    )

    assert settings.telegram_bot_username is None


def test_settings_accepts_telegram_bot_username_from_value_object() -> None:
    username = TelegramBotUsername("Nasiya_LinkBot")
    settings = make_settings(telegram_bot_username=username)

    assert settings.telegram_bot_username is username


def test_settings_reads_telegram_bot_username_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("RATE_LIMIT_HMAC_KEY", TEST_RATE_LIMIT_HMAC_KEY)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "Nasiya_LinkBot")

    settings = Settings(_env_file=None)

    assert settings.telegram_bot_username is not None
    assert settings.telegram_bot_username.as_username() == "nasiya_linkbot"


def test_settings_treats_empty_telegram_bot_username_env_as_none(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("RATE_LIMIT_HMAC_KEY", TEST_RATE_LIMIT_HMAC_KEY)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")

    settings = Settings(_env_file=None)

    assert settings.telegram_bot_username is None


def test_settings_rejects_invalid_bot_username_without_echoing_raw_value() -> None:
    raw_username = "https://t.me/SensitivePlaceholderBot"

    with pytest.raises(ValidationError) as exc_info:
        make_settings(telegram_bot_username=raw_username)

    error_text = str(exc_info.value)
    assert raw_username not in error_text
    assert "Telegram bot username" in error_text


def test_settings_has_no_default_or_token_for_telegram_bot() -> None:
    settings = make_settings(app_environment="production", session_cookie_secure=True)

    assert settings.telegram_bot_username is None
    assert "telegram_bot_token" not in Settings.model_fields
