from unittest.mock import Mock

import pytest

from app.auth import password_service
from app.auth.password_service import (
    PasswordHashingError,
    hash_password,
    verify_and_update,
    verify_missing_user_password,
    verify_password,
)


def test_hash_password_returns_argon2_hash_not_raw_password() -> None:
    raw_password = "Password123"

    stored_hash = hash_password(raw_password)

    assert stored_hash != raw_password
    assert raw_password not in stored_hash
    assert stored_hash.startswith("$argon2")


def test_verify_password_accepts_correct_password() -> None:
    raw_password = "Password123"
    stored_hash = hash_password(raw_password)

    assert verify_password(raw_password, stored_hash) is True


def test_verify_password_rejects_wrong_password() -> None:
    stored_hash = hash_password("Password123")

    assert verify_password("Password124", stored_hash) is False


def test_same_raw_password_hashes_do_not_have_to_match() -> None:
    raw_password = "Password123"

    first_hash = hash_password(raw_password)
    second_hash = hash_password(raw_password)

    assert first_hash != second_hash
    assert verify_password(raw_password, first_hash) is True
    assert verify_password(raw_password, second_hash) is True


def test_verify_and_update_contract_for_valid_password() -> None:
    raw_password = "Password123"
    stored_hash = hash_password(raw_password)

    valid, new_hash = verify_and_update(raw_password, stored_hash)

    assert valid is True
    assert new_hash is None or isinstance(new_hash, str)
    if new_hash is not None:
        assert new_hash != raw_password
        assert verify_password(raw_password, new_hash) is True


def test_verify_and_update_contract_for_invalid_password() -> None:
    stored_hash = hash_password("Password123")

    valid, new_hash = verify_and_update("Password124", stored_hash)

    assert valid is False
    assert new_hash is None


def test_dummy_verify_for_missing_user_runs_without_error() -> None:
    assert verify_missing_user_password("Password123") is None


def test_invalid_stored_hash_returns_false_without_password_in_error() -> None:
    raw_password = "Password123"

    assert verify_password(raw_password, "not-a-password-hash") is False
    assert verify_and_update(raw_password, "not-a-password-hash") == (False, None)


def test_hash_exception_message_does_not_include_raw_password(monkeypatch) -> None:
    raw_password = "Password123"
    hasher = Mock()
    hasher.hash.side_effect = RuntimeError(raw_password)
    monkeypatch.setattr(password_service, "_PASSWORD_HASH", hasher)

    with pytest.raises(PasswordHashingError) as exc_info:
        password_service.hash_password(raw_password)

    assert raw_password not in str(exc_info.value)
    assert raw_password not in repr(exc_info.value)
