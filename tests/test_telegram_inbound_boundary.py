import inspect
import logging
import socket
import urllib.request
from dataclasses import is_dataclass
from inspect import signature

import pytest

import app.telegram.inbound as inbound_module
import app.telegram.service as telegram_service
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.telegram.inbound import (
    FakeVerifiedPrivateTelegramAdapter,
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import issue_link_token_after_rate_limit

POSTGRES_BIGINT_MAX = 2**63 - 1


@pytest.mark.parametrize(
    "chat_id",
    [
        1,
        42,
        9_007_199_254_740_991,
        POSTGRES_BIGINT_MAX,
    ],
)
def test_verified_private_chat_identity_accepts_positive_bigint_values(
    chat_id: int,
) -> None:
    identity = VerifiedPrivateTelegramChatIdentity(chat_id)

    assert identity.as_bigint() == chat_id


@pytest.mark.parametrize(
    "chat_id",
    [
        0,
        -1,
        -100_123_456_789,
        -(2**63),
        POSTGRES_BIGINT_MAX + 1,
    ],
)
def test_verified_private_chat_identity_rejects_non_private_or_out_of_range_ids(
    chat_id: int,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        VerifiedPrivateTelegramChatIdentity(chat_id)

    assert str(chat_id) not in str(exc_info.value)
    assert "Verified private Telegram chat identity" in str(exc_info.value)


@pytest.mark.parametrize(
    "raw_value",
    [
        True,
        False,
        "123456",
        123.0,
        {"chat_id": 123456},
        None,
    ],
)
def test_verified_private_chat_identity_rejects_non_numeric_payloads(
    raw_value: object,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        VerifiedPrivateTelegramChatIdentity(raw_value)  # type: ignore[arg-type]

    assert str(raw_value) not in str(exc_info.value)
    assert "numeric" in str(exc_info.value)


def test_verified_private_chat_identity_repr_str_and_logging_are_redacted(
    caplog,
) -> None:
    identity = VerifiedPrivateTelegramChatIdentity(123_456_789)
    raw_chat_id = str(identity.as_bigint())
    logger = logging.getLogger("tests.telegram_inbound_boundary")

    with caplog.at_level(logging.INFO):
        logger.info("verified identity via percent-s %s", identity)
        logger.info("verified identity via percent-r %r", identity)
        logger.info("verified identity via f-string %s", f"{identity}")

    assert raw_chat_id not in str(identity)
    assert raw_chat_id not in repr(identity)
    assert raw_chat_id not in caplog.text
    assert "redacted" in caplog.text


def test_verified_private_chat_identity_has_narrow_explicit_accessor() -> None:
    identity = VerifiedPrivateTelegramChatIdentity(123_456_789)

    assert identity.as_bigint() == 123_456_789
    assert not is_dataclass(identity)
    assert not hasattr(identity, "__dict__")
    assert not hasattr(identity, "value")
    assert not hasattr(identity, "raw")
    assert not hasattr(identity, "id")
    assert not hasattr(identity, "chat_id")
    assert not hasattr(identity, "dict")
    assert not hasattr(identity, "model_dump")
    assert not hasattr(identity, "json")


@pytest.mark.parametrize("user_id", [1, 42, POSTGRES_BIGINT_MAX])
def test_telegram_user_identity_is_positive_bigint_and_redacted(
    user_id: int,
    caplog,
) -> None:
    identity = TelegramUserIdentity(user_id)

    with caplog.at_level(logging.INFO):
        logging.getLogger("tests.telegram_inbound_boundary").info(
            "sender %s %r", identity, identity
        )

    assert identity.as_bigint() == user_id
    assert str(user_id) not in str(identity)
    assert str(user_id) not in repr(identity)
    assert str(user_id) not in " ".join(
        record.getMessage() for record in caplog.records
    )
    assert not is_dataclass(identity)
    assert not hasattr(identity, "__dict__")


@pytest.mark.parametrize("raw_value", [0, -1, POSTGRES_BIGINT_MAX + 1, True, "1"])
def test_telegram_user_identity_rejects_invalid_values_without_echo(
    raw_value: object,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        TelegramUserIdentity(raw_value)  # type: ignore[arg-type]

    assert str(raw_value) not in str(exc_info.value)


def test_sensitive_contact_phone_is_bounded_operation_local_and_redacted(
    caplog,
) -> None:
    raw_phone = "+998 90 123 45 67"
    phone = SensitiveTelegramContactPhone(raw_phone)

    with caplog.at_level(logging.INFO):
        logging.getLogger("tests.telegram_inbound_boundary").info(
            "contact %s %r", phone, phone
        )

    assert phone.as_normalization_input() == raw_phone
    assert raw_phone not in str(phone)
    assert raw_phone not in repr(phone)
    assert raw_phone not in caplog.text
    assert not is_dataclass(phone)
    assert not hasattr(phone, "__dict__")
    assert not hasattr(phone, "value")
    assert not hasattr(phone, "raw")


@pytest.mark.parametrize("raw_phone", ["", " ", "x" * 65, None, 998901234567])
def test_sensitive_contact_phone_rejects_invalid_values_without_echo(
    raw_phone: object,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        SensitiveTelegramContactPhone(raw_phone)  # type: ignore[arg-type]

    if isinstance(raw_phone, str) and raw_phone.strip():
        assert raw_phone not in str(exc_info.value)
    elif not isinstance(raw_phone, str):
        assert str(raw_phone) not in str(exc_info.value)


@pytest.mark.parametrize(
    "raw_phone",
    [
        "+998٩٠١٢٣٤٥٦٧",
        "+998９０１２３４５６７",
    ],
)
def test_contact_phone_canonicalization_rejects_non_ascii_decimal_digits(
    raw_phone: str,
) -> None:
    contact_phone = SensitiveTelegramContactPhone(raw_phone)

    with pytest.raises(PhoneNormalizationError) as exc_info:
        normalize_uzbekistan_phone(contact_phone.as_normalization_input())

    assert raw_phone not in str(exc_info.value)
    assert raw_phone not in repr(contact_phone)


def test_fake_verified_private_adapter_explicitly_creates_typed_identity(
    caplog,
) -> None:
    adapter = FakeVerifiedPrivateTelegramAdapter()
    identity = adapter.verify_private_chat(987_654_321)

    with caplog.at_level(logging.INFO):
        logging.getLogger("tests.telegram_inbound_boundary").info(
            "fake adapter identity %s %r",
            identity,
            identity,
        )

    assert isinstance(identity, VerifiedPrivateTelegramChatIdentity)
    assert identity.as_bigint() == 987_654_321
    assert "987654321" not in caplog.text
    assert "redacted" in caplog.text


def test_fake_verified_private_adapter_accepts_only_numeric_boundary_input() -> None:
    parameters = signature(
        FakeVerifiedPrivateTelegramAdapter.verify_private_chat
    ).parameters

    assert list(parameters) == ["self", "chat_id"]
    assert "request" not in parameters
    assert "update" not in parameters
    assert "payload" not in parameters
    assert "message" not in parameters
    assert "username" not in parameters


def test_fake_adapter_exposes_only_explicit_private_test_path() -> None:
    adapter = FakeVerifiedPrivateTelegramAdapter()

    assert hasattr(adapter, "verify_private_chat")
    assert not callable(adapter)
    assert not hasattr(adapter, "verify")
    assert not hasattr(adapter, "from_update")
    assert not hasattr(adapter, "from_message")
    assert not hasattr(adapter, "parse_update")
    assert not hasattr(adapter, "parse_request")


def test_fake_verified_private_adapter_rejects_transport_payloads() -> None:
    adapter = FakeVerifiedPrivateTelegramAdapter()
    raw_payload = {"message": {"chat": {"id": 123_456_789, "type": "private"}}}

    with pytest.raises(ValueError) as exc_info:
        adapter.verify_private_chat(raw_payload)  # type: ignore[arg-type]

    assert "123456789" not in str(exc_info.value)
    assert "message" not in str(exc_info.value)
    assert "{" not in str(exc_info.value)


@pytest.mark.parametrize(
    "semantic_payload",
    [
        {"chat": {"id": -100_123_456_789, "type": "group"}},
        {"chat": {"id": -100_123_456_789, "type": "supergroup"}},
        {"chat": {"id": -100_123_456_789, "type": "channel"}},
        {"id": 123_456_789, "type": "group"},
        {"id": 123_456_789, "type": "supergroup"},
        {"id": 123_456_789, "type": "channel"},
        {"forward_from_chat": {"id": 123_456_789, "type": "private"}},
        {"username": "sensitive_user", "id": 123_456_789},
    ],
)
def test_group_channel_or_metadata_payloads_never_become_verified_identity(
    semantic_payload: dict[str, object],
) -> None:
    adapter = FakeVerifiedPrivateTelegramAdapter()

    with pytest.raises(ValueError) as exc_info:
        adapter.verify_private_chat(semantic_payload)  # type: ignore[arg-type]

    error_text = str(exc_info.value)
    assert "123456789" not in error_text
    assert "-100123456789" not in error_text
    assert "sensitive_user" not in error_text
    assert "{" not in error_text


def test_domain_issue_service_accepts_no_raw_telegram_transport_identity() -> None:
    parameters = signature(issue_link_token_after_rate_limit).parameters

    for forbidden_parameter in (
        "chat_id",
        "telegram_chat_id",
        "telegram_update",
        "update_json",
        "request",
        "payload",
        "message",
        "username",
    ):
        assert forbidden_parameter not in parameters


def test_future_domain_consume_api_must_not_accept_raw_int_chat_identity() -> None:
    consume_callables = {
        name: callable_object
        for name, callable_object in vars(telegram_service).items()
        if "consume" in name and callable(callable_object)
    }

    for callable_object in consume_callables.values():
        parameters = signature(callable_object).parameters
        for parameter in parameters.values():
            assert parameter.annotation is not int
            assert parameter.name not in {
                "chat_id",
                "telegram_chat_id",
                "raw_chat_id",
            }


def test_fake_verified_private_adapter_does_not_call_dns_http_or_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_network_call(*_args, **_kwargs) -> None:
        calls.append("network")
        raise AssertionError("fake Telegram adapter must not use network")

    monkeypatch.setattr(socket, "getaddrinfo", fail_network_call)
    monkeypatch.setattr(socket, "socket", fail_network_call)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network_call)

    identity = FakeVerifiedPrivateTelegramAdapter().verify_private_chat(321_654_987)

    assert identity.as_bigint() == 321_654_987
    assert calls == []


def test_transport_metadata_is_not_persisted_in_telegram_db_models() -> None:
    forbidden_columns = {
        "username",
        "first_name",
        "last_name",
        "message",
        "message_json",
        "update",
        "update_json",
        "payload",
        "request",
        "forward_from_chat",
        "chat_type",
    }

    for model in (TelegramLink, TelegramLinkToken, TelegramLinkEvent):
        assert forbidden_columns.isdisjoint(model.__table__.columns.keys())


def test_inbound_boundary_has_no_network_parser_or_metadata_model() -> None:
    source = inspect.getsource(inbound_module)
    forbidden_source_terms = {
        "requests",
        "httpx",
        "urllib.request",
        "socket",
        "aiogram",
        "telebot",
        "telegram.Bot",
        "Request",
        "Update",
        "os.environ",
        "getenv",
        "sqlalchemy",
        "psycopg",
        "username",
        "forward",
        "group",
        "supergroup",
        "channel",
        "metadata",
        "update_json",
        "message_json",
    }

    for forbidden_source_term in forbidden_source_terms:
        assert forbidden_source_term not in source
