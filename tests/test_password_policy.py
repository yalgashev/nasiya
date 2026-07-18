import pytest

from app.auth.password_policy import (
    PasswordPolicy,
    PasswordPolicyError,
    validate_new_password,
)
from app.settings import Settings

TEST_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-password-policy"


def make_settings(
    password_min_length: int = 8,
    password_max_length: int = 128,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=TEST_DATABASE_URL,
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        password_min_length=password_min_length,
        password_max_length=password_max_length,
    )


@pytest.mark.parametrize("raw_password", ["abc12345", "A1345678", "Password123"])
def test_valid_new_password_is_accepted(raw_password: str) -> None:
    validate_new_password(raw_password, make_settings())


@pytest.mark.parametrize(
    "raw_password",
    [
        "a1c",
        "a1cdefghi",
    ],
)
def test_password_length_must_be_within_settings_range(raw_password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password(
            raw_password,
            make_settings(password_min_length=4, password_max_length=8),
        )


@pytest.mark.parametrize("raw_password", ["12345678", "1234567890"])
def test_new_password_requires_at_least_one_letter(raw_password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password(raw_password, make_settings())


@pytest.mark.parametrize("raw_password", ["abcdefgh", "Password"])
def test_new_password_requires_at_least_one_number(raw_password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password(raw_password, make_settings())


@pytest.mark.parametrize("raw_password", ["", "        ", "\t \n"])
def test_blank_or_whitespace_only_password_is_rejected(raw_password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password(raw_password, make_settings())


def test_password_policy_is_only_for_new_passwords() -> None:
    assert not hasattr(PasswordPolicy, "verify_password")
    assert not hasattr(PasswordPolicy, "verify_hash")
    assert not hasattr(PasswordPolicy, "validate_existing_password")


def test_raw_password_is_not_in_error_message_or_repr() -> None:
    raw_password = "secret-without-number"

    with pytest.raises(PasswordPolicyError) as exc_info:
        validate_new_password(raw_password, make_settings())

    assert raw_password not in str(exc_info.value)
    assert raw_password not in repr(exc_info.value)
    assert raw_password not in repr(PasswordPolicy.from_settings(make_settings()))
