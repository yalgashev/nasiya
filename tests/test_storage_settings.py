import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.main import create_app
from app.settings import ObjectStorageSettingsError, Settings, StorageConfig

TEST_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"
TEST_RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-storage-settings"
TEST_ENDPOINT = "http://storage.invalid:9000"
TEST_ACCESS_KEY = "synthetic-storage-access-key"
TEST_SECRET_KEY = "synthetic-storage-secret-key"

STORAGE_ENV_KEYS = (
    "OBJECT_STORAGE_ENDPOINT_URL",
    "OBJECT_STORAGE_REGION",
    "OBJECT_STORAGE_BUCKET",
    "OBJECT_STORAGE_ACCESS_KEY",
    "OBJECT_STORAGE_SECRET_KEY",
    "OBJECT_STORAGE_USE_SSL",
    "OBJECT_STORAGE_ADDRESSING_STYLE",
    "OBJECT_STORAGE_PRESIGNED_TTL_SECONDS",
    "OBJECT_STORAGE_MAX_UPLOAD_BYTES",
    "OBJECT_STORAGE_MAX_MULTIPART_BYTES",
    "OBJECT_STORAGE_MAX_IMAGE_PIXELS",
    "OBJECT_STORAGE_MAX_IMAGE_DIMENSION",
    "OBJECT_STORAGE_UPLOAD_RATE_LIMIT_WINDOW_SECONDS",
    "OBJECT_STORAGE_UPLOAD_RATE_LIMIT_USER_ATTEMPTS",
    "OBJECT_STORAGE_UPLOAD_RATE_LIMIT_IP_ATTEMPTS",
    "OBJECT_STORAGE_RECONCILE_STALE_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_storage_environment(monkeypatch) -> None:
    for key in STORAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def make_settings(**overrides) -> Settings:
    values = {
        "debug": False,
        "database_url": TEST_DATABASE_URL,
        "session_cookie_secure": False,
        "rate_limit_hmac_key": TEST_RATE_LIMIT_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def complete_storage_values(**overrides) -> dict[str, object]:
    values: dict[str, object] = {
        "object_storage_endpoint_url": TEST_ENDPOINT,
        "object_storage_region": "us-east-1",
        "object_storage_bucket": "nasiya-private-test",
        "object_storage_access_key": TEST_ACCESS_KEY,
        "object_storage_secret_key": TEST_SECRET_KEY,
        "object_storage_use_ssl": False,
    }
    values.update(overrides)
    return values


def test_storage_settings_default_to_optional_frozen_policy() -> None:
    settings = make_settings()

    assert settings.object_storage_endpoint_url is None
    assert settings.object_storage_region is None
    assert settings.object_storage_bucket is None
    assert settings.object_storage_access_key is None
    assert settings.object_storage_secret_key is None
    assert settings.object_storage_use_ssl is None
    assert settings.object_storage_addressing_style == "path"
    assert settings.object_storage_presigned_ttl_seconds == 300
    assert settings.object_storage_max_upload_bytes == 10_485_760
    assert settings.object_storage_max_multipart_bytes == 11_010_048
    assert settings.object_storage_max_image_pixels == 40_000_000
    assert settings.object_storage_max_image_dimension == 16_384
    assert settings.object_storage_upload_rate_limit_window_seconds == 900
    assert settings.object_storage_upload_rate_limit_user_attempts == 5
    assert settings.object_storage_upload_rate_limit_ip_attempts == 20
    assert settings.object_storage_reconcile_stale_seconds == 60

    with pytest.raises(ObjectStorageSettingsError):
        settings.require_object_storage_config()


def test_complete_storage_bundle_returns_redacted_snapshot() -> None:
    settings = make_settings(**complete_storage_values())

    config = settings.require_object_storage_config()

    assert isinstance(config, StorageConfig)
    assert isinstance(config.endpoint_url, SecretStr)
    assert config.endpoint_url.get_secret_value() == TEST_ENDPOINT
    assert config.region == "us-east-1"
    assert config.bucket == "nasiya-private-test"
    assert config.access_key.get_secret_value() == TEST_ACCESS_KEY
    assert config.secret_key.get_secret_value() == TEST_SECRET_KEY
    assert config.use_ssl is False
    assert config.addressing_style == "path"
    rendered = f"{settings!r} {settings!s} {config!r} {config!s}"
    assert TEST_ENDPOINT not in rendered
    assert "nasiya-private-test" not in rendered
    assert TEST_ACCESS_KEY not in rendered
    assert TEST_SECRET_KEY not in rendered


def test_storage_bundle_reads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("RATE_LIMIT_HMAC_KEY", TEST_RATE_LIMIT_KEY)
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT_URL", "https://storage.invalid")
    monkeypatch.setenv("OBJECT_STORAGE_REGION", "region-1")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "private-bucket")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", TEST_ACCESS_KEY)
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("OBJECT_STORAGE_USE_SSL", "true")
    monkeypatch.setenv("OBJECT_STORAGE_ADDRESSING_STYLE", "virtual")

    config = Settings(_env_file=None).require_object_storage_config()

    assert config.endpoint_url.get_secret_value() == "https://storage.invalid"
    assert config.use_ssl is True
    assert config.addressing_style == "virtual"


@pytest.mark.parametrize(
    "partial_values",
    [
        {"object_storage_endpoint_url": TEST_ENDPOINT},
        {"object_storage_region": "us-east-1"},
        {"object_storage_bucket": "private-bucket"},
        {"object_storage_access_key": TEST_ACCESS_KEY},
        {"object_storage_secret_key": TEST_SECRET_KEY},
        {"object_storage_use_ssl": False},
    ],
)
def test_partial_storage_bundle_fails_closed_only_at_operation(
    partial_values: dict[str, object],
) -> None:
    settings = make_settings(**partial_values)

    with pytest.raises(ObjectStorageSettingsError) as exc_info:
        settings.require_object_storage_config()

    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert TEST_ENDPOINT not in rendered
    assert TEST_ACCESS_KEY not in rendered
    assert TEST_SECRET_KEY not in rendered


@pytest.mark.parametrize(
    "overrides",
    [
        {"object_storage_endpoint_url": "ftp://storage.invalid"},
        {"object_storage_endpoint_url": "http://user@storage.invalid"},
        {"object_storage_endpoint_url": "http://storage.invalid/path"},
        {"object_storage_region": "invalid region"},
        {"object_storage_bucket": "Invalid-Bucket"},
        {"object_storage_bucket": "192.0.2.10"},
        {"object_storage_addressing_style": "auto"},
        {"object_storage_presigned_ttl_seconds": 59},
        {"object_storage_presigned_ttl_seconds": 901},
        {"object_storage_max_upload_bytes": 10_485_761},
        {"object_storage_max_multipart_bytes": 10_485_760},
        {"object_storage_max_multipart_bytes": 11_534_337},
        {"object_storage_max_image_pixels": 40_000_001},
        {"object_storage_max_image_dimension": 16_385},
        {"object_storage_upload_rate_limit_window_seconds": 899},
        {"object_storage_upload_rate_limit_user_attempts": 6},
        {"object_storage_upload_rate_limit_ip_attempts": 21},
        {"object_storage_reconcile_stale_seconds": 0},
    ],
)
def test_invalid_storage_policy_values_fail_fast(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**complete_storage_values(**overrides))


@pytest.mark.parametrize(
    "endpoint,use_ssl",
    [
        ("http://storage.invalid", True),
        ("https://storage.invalid", False),
    ],
)
def test_endpoint_scheme_and_use_ssl_must_agree(
    endpoint: str,
    use_ssl: bool,
) -> None:
    with pytest.raises(ValidationError):
        make_settings(
            **complete_storage_values(
                object_storage_endpoint_url=endpoint,
                object_storage_use_ssl=use_ssl,
            )
        )


def test_storage_validation_and_dumps_hide_sensitive_values() -> None:
    raw_endpoint = " https://sensitive-storage.invalid "
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            **complete_storage_values(
                object_storage_endpoint_url=raw_endpoint,
                object_storage_use_ssl=True,
            )
        )

    assert raw_endpoint not in str(exc_info.value)

    settings = make_settings(**complete_storage_values())
    rendered_dump = repr(settings.model_dump())
    assert TEST_ENDPOINT not in rendered_dump
    assert TEST_ACCESS_KEY not in rendered_dump
    assert TEST_SECRET_KEY not in rendered_dump


def test_web_health_starts_without_storage_configuration() -> None:
    settings = make_settings()
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
