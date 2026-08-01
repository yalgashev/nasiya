import base64
import json

import pytest

from app.customer_identity.crypto import (
    CustomerIdentityCryptoConfigurationError,
    CustomerIdentityKeyId,
)
from app.settings import Settings


def _base_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_environment": "testing",
        "debug": False,
        "database_url": ("postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"),
        "session_cookie_secure": False,
        "rate_limit_hmac_key": "R" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def _encoded(marker: int) -> str:
    return base64.b64encode(bytes([marker]) * 32).decode("ascii")


def _valid_bundle() -> dict[str, str]:
    return {
        "customer_identity_active_key_id": "identity-v1",
        "customer_identity_encryption_keys": json.dumps(
            {
                "identity-v1": _encoded(1),
                "identity-v0": _encoded(2),
            },
            separators=(",", ":"),
        ),
        "customer_identity_blind_index_key": _encoded(3),
    }


def test_identity_crypto_settings_are_optional_at_base_startup() -> None:
    settings = _base_settings()

    assert settings.customer_identity_active_key_id is None
    assert settings.customer_identity_encryption_keys is None
    assert settings.customer_identity_blind_index_key is None
    with pytest.raises(CustomerIdentityCryptoConfigurationError) as caught:
        settings.require_customer_identity_crypto_config()
    assert str(caught.value) == "Customer identity cryptography is unavailable"
    assert caught.value.__cause__ is None


def test_valid_identity_crypto_bundle_returns_exact_redacted_snapshot() -> None:
    settings = _base_settings(**_valid_bundle())

    config = settings.require_customer_identity_crypto_config()

    active_id, active_key = config.get_active_write_key()
    assert active_id.as_persistence_value() == "identity-v1"
    assert active_key.as_aesgcm_key() == bytes([1]) * 32
    assert (
        config.get_decryption_key(CustomerIdentityKeyId("identity-v0")).as_aesgcm_key()
        == bytes([2]) * 32
    )
    assert config.get_blind_index_key().as_hmac_key() == bytes([3]) * 32
    rendered = f"{settings!r} {settings.model_dump()!r} {config!r}"
    for secret in (*_valid_bundle().values(), _encoded(1), _encoded(2), _encoded(3)):
        assert secret not in rendered


@pytest.mark.parametrize(
    "overrides",
    [
        {"customer_identity_active_key_id": None},
        {"customer_identity_encryption_keys": None},
        {"customer_identity_blind_index_key": None},
        {"customer_identity_active_key_id": "missing"},
        {"customer_identity_active_key_id": "bad/key"},
        {"customer_identity_encryption_keys": "{}"},
        {"customer_identity_encryption_keys": "[]"},
        {"customer_identity_encryption_keys": "not-json"},
        {
            "customer_identity_encryption_keys": (
                f'{{"identity-v1":"{_encoded(1)}","identity-v1":"{_encoded(2)}"}}'
            )
        },
        {
            "customer_identity_encryption_keys": json.dumps(
                {"identity-v1": _encoded(1).rstrip("=")}
            )
        },
        {
            "customer_identity_encryption_keys": json.dumps(
                {"identity-v1": base64.b64encode(b"short").decode("ascii")}
            )
        },
        {
            "customer_identity_encryption_keys": json.dumps(
                {"identity-v1": _encoded(1), "identity-v0": _encoded(1)}
            )
        },
        {"customer_identity_blind_index_key": _encoded(1)},
    ],
)
def test_invalid_identity_crypto_bundles_are_indistinguishable_and_redacted(
    overrides: dict[str, str | None],
) -> None:
    bundle = _valid_bundle()
    bundle.update(overrides)
    settings = _base_settings(**bundle)

    with pytest.raises(CustomerIdentityCryptoConfigurationError) as caught:
        settings.require_customer_identity_crypto_config()

    rendered = f"{caught.value!s} {caught.value!r}"
    assert rendered == (
        "Customer identity cryptography is unavailable "
        "CustomerIdentityCryptoConfigurationError()"
    )
    for value in bundle.values():
        if value:
            assert value not in rendered
    assert caught.value.__cause__ is None


def test_blind_key_cannot_reuse_rate_limit_or_otp_secret() -> None:
    shared = b"Q" * 32
    shared_text = "Q" * 32
    bundle = _valid_bundle()
    bundle["customer_identity_blind_index_key"] = base64.b64encode(shared).decode(
        "ascii"
    )
    settings = _base_settings(
        **bundle,
        rate_limit_hmac_key=shared_text,
        otp_hmac_key="O" * 32,
    )
    with pytest.raises(CustomerIdentityCryptoConfigurationError):
        settings.require_customer_identity_crypto_config()

    settings = _base_settings(
        **bundle,
        rate_limit_hmac_key="R" * 32,
        otp_hmac_key=shared_text,
    )
    with pytest.raises(CustomerIdentityCryptoConfigurationError):
        settings.require_customer_identity_crypto_config()
