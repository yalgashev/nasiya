import base64
import importlib.metadata
import json
import tomllib
from pathlib import Path

import pytest

from app.customer_identity.crypto import CustomerIdentityCryptoConfigurationError
from app.settings import Settings


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_environment": "testing",
        "debug": False,
        "database_url": "postgresql+psycopg://nasiya:test@127.0.0.1/nasiya_test",
        "session_cookie_secure": False,
        "rate_limit_hmac_key": "R" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def _encoded(marker: int) -> str:
    return base64.b64encode(bytes((marker,)) * 32).decode("ascii")


def test_m10_has_one_approved_direct_crypto_dependency_and_locked_resolution() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    crypto_dependencies = [
        dependency
        for dependency in dependencies
        if dependency.partition(">=")[0]
        in {"cryptography", "pycryptodome", "pycryptodomex", "pyopenssl", "cffi"}
    ]

    assert crypto_dependencies == ["cryptography>=50.0.0,<51"]
    assert importlib.metadata.version("cryptography") == "50.0.0"
    lock_source = Path("uv.lock").read_text(encoding="utf-8")
    assert 'name = "cryptography"\nversion = "50.0.0"' in lock_source


def test_m10_crypto_uses_only_pyca_high_level_aesgcm_for_aead() -> None:
    source = Path("app/customer_identity/crypto.py").read_text(encoding="utf-8")

    assert "from cryptography.hazmat.primitives.ciphers.aead import AESGCM" in source
    assert "cryptography.hazmat.primitives.ciphers import Cipher" not in source
    assert "Fernet" not in source
    assert "AESGCMSIV" not in source
    assert "modes.GCM" not in source


def test_blind_index_key_must_be_distinct_from_encryption_keys() -> None:
    encryption_key = _encoded(1)
    settings = _settings(
        customer_identity_active_key_id="identity-v1",
        customer_identity_encryption_keys=json.dumps(
            {"identity-v1": encryption_key}, separators=(",", ":")
        ),
        customer_identity_blind_index_key=encryption_key,
    )

    with pytest.raises(CustomerIdentityCryptoConfigurationError) as caught:
        settings.require_customer_identity_crypto_config()

    assert caught.value.__cause__ is None
    assert encryption_key not in repr(caught.value)


def test_missing_and_invalid_identity_keyring_configuration_fails_closed() -> None:
    for settings in (
        _settings(),
        _settings(
            customer_identity_active_key_id="identity-v1",
            customer_identity_encryption_keys="not-json",
            customer_identity_blind_index_key=_encoded(2),
        ),
    ):
        with pytest.raises(CustomerIdentityCryptoConfigurationError) as caught:
            settings.require_customer_identity_crypto_config()
        assert str(caught.value) == "Customer identity cryptography is unavailable"
        assert caught.value.__cause__ is None


def test_m10_secret_material_is_absent_from_tracked_files_and_ci_output_contract() -> (
    None
):
    env_example = Path(".env.example").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    for name in (
        "CUSTOMER_IDENTITY_ACTIVE_KEY_ID",
        "CUSTOMER_IDENTITY_ENCRYPTION_KEYS",
        "CUSTOMER_IDENTITY_BLIND_INDEX_KEY",
    ):
        assert f"{name}=\n" in env_example
    assert "CUSTOMER_IDENTITY_ENCRYPTION_KEYS:" not in workflow
    assert "CUSTOMER_IDENTITY_BLIND_INDEX_KEY:" not in workflow
    assert "uv run pytest -ra" in workflow
    assert "skipped|xfailed|xpassed" in workflow
