import logging
import re

from app.auth.models import Session as AuthSession
from app.auth.sessions import (
    SESSION_TOKEN_ENTROPY_BYTES,
    RawSessionToken,
    constant_time_compare,
    create_session_token,
    hash_session_token,
)

SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def test_session_tokens_do_not_repeat() -> None:
    token_values = {
        create_session_token().as_cookie_value()
        for _ in range(128)
    }

    assert len(token_values) == 128


def test_session_token_has_at_least_32_bytes_of_entropy() -> None:
    token = create_session_token()

    assert SESSION_TOKEN_ENTROPY_BYTES >= 32
    assert isinstance(token, RawSessionToken)
    assert len(token.as_cookie_value()) >= 43


def test_session_token_hash_is_64_hex_characters() -> None:
    token_hash = hash_session_token(create_session_token())

    assert SHA256_HEX_PATTERN.fullmatch(token_hash)


def test_same_session_token_produces_same_hash() -> None:
    token = create_session_token()

    assert hash_session_token(token) == hash_session_token(token)


def test_session_token_hash_does_not_reveal_raw_token() -> None:
    token = create_session_token()
    raw_token = token.as_cookie_value()
    token_hash = hash_session_token(token)

    assert token_hash != raw_token
    assert raw_token not in token_hash


def test_raw_session_token_repr_and_logging_are_redacted(caplog) -> None:
    token = create_session_token()
    raw_token = token.as_cookie_value()
    logger = logging.getLogger("tests.session_primitives")

    with caplog.at_level(logging.INFO):
        logger.info("created token %s %r", token, token)

    assert raw_token not in str(token)
    assert raw_token not in repr(token)
    assert raw_token not in caplog.text
    assert "redacted" in caplog.text


def test_constant_time_compare_helper() -> None:
    token_hash = hash_session_token(create_session_token())

    assert constant_time_compare(token_hash, token_hash) is True
    assert constant_time_compare(token_hash, "0" * 64) is False


def test_session_db_model_has_no_raw_token_column() -> None:
    forbidden_columns = {
        "token",
        "raw_token",
        "session_token",
        "raw_session_token",
        "cookie",
        "cookie_value",
        "password",
    }

    assert forbidden_columns.isdisjoint(AuthSession.__table__.columns.keys())
