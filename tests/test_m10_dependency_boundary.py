import importlib.metadata
import tomllib
from pathlib import Path


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
