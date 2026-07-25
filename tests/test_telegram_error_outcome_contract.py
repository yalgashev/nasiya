from datetime import UTC, datetime

import pytest

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.telegram.service import (
    TelegramChatAlreadyLinkedError,
    TelegramLinkOutcome,
    TelegramLinkStatus,
    TelegramLinkTokenConsumeError,
    TelegramLinkTokenIssueError,
    TelegramStartTokenConsumeOutcome,
    UnlinkedTelegramLink,
    get_valid_link_token_for_consume,
)
from app.telegram.token import (
    RawTelegramLinkToken,
    TelegramBotUsernameNotConfigured,
)

M4_STABLE_ERROR_CODES = (
    ErrorCode.RATE_LIMITED,
    ErrorCode.LINK_TOKEN_INVALID,
    ErrorCode.TELEGRAM_NOT_LINKED,
    ErrorCode.TELEGRAM_ALREADY_LINKED,
    ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED,
)
M4_STABLE_OUTCOMES = (
    "LINKED",
    "RELINKED",
    "UNLINKED",
    "ALREADY_LINKED_TO_THIS_CHAT",
)


def assert_raw_details_not_exposed(exc: BaseException) -> None:
    raw_details = (
        "telegram_link_tokens",
        "telegram_links",
        "uq_telegram_links_active_chat_id",
        "uq_telegram_link_tokens_token_hash",
        "ck_telegram_link_tokens_terminal_state_exclusive",
        "token_hash",
        "consumed_at",
        "invalidated_at",
        "expires_at",
        "23505",
        "40001",
        "16005550123",
        "raw_secret_token",
    )
    exposed_text = f"{exc!r} {exc} {getattr(exc, 'public_error', {})}"

    for raw_detail in raw_details:
        assert raw_detail not in exposed_text


def test_m4_error_codes_are_in_stable_application_catalog() -> None:
    assert tuple(code.value for code in M4_STABLE_ERROR_CODES) == (
        "RATE_LIMITED",
        "LINK_TOKEN_INVALID",
        "TELEGRAM_NOT_LINKED",
        "TELEGRAM_ALREADY_LINKED",
        "TELEGRAM_CHAT_ALREADY_LINKED",
    )

    for code in M4_STABLE_ERROR_CODES:
        public_body = get_public_error_body(
            code,
            internal_detail=(
                "telegram_link_tokens token_hash raw_secret_token "
                "uq_telegram_links_active_chat_id"
            ),
        )

        assert public_body["code"] == code.value
        assert set(public_body) == {"code", "message"}
        assert "raw_secret_token" not in str(public_body)
        assert "token_hash" not in str(public_body)
        assert "uq_telegram_links_active_chat_id" not in str(public_body)


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (
            TelegramLinkTokenIssueError(ErrorCode.RATE_LIMITED),
            ErrorCode.RATE_LIMITED,
        ),
        (
            TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_NOT_LINKED),
            ErrorCode.TELEGRAM_NOT_LINKED,
        ),
        (
            TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_ALREADY_LINKED),
            ErrorCode.TELEGRAM_ALREADY_LINKED,
        ),
        (TelegramChatAlreadyLinkedError(), ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED),
        (TelegramLinkTokenConsumeError(), ErrorCode.LINK_TOKEN_INVALID),
    ],
)
def test_m4_service_errors_expose_only_stable_codes(
    exc: Exception,
    expected_code: ErrorCode,
) -> None:
    assert exc.error_code is expected_code
    assert exc.public_error == get_public_error_body(expected_code)
    assert str(exc) == expected_code.value
    assert_raw_details_not_exposed(exc)


def test_malformed_and_unknown_tokens_map_to_uniform_invalid_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 25, 11, 0, tzinfo=UTC)

    with pytest.raises(TelegramLinkTokenConsumeError) as malformed_exc_info:
        get_valid_link_token_for_consume(
            object(),  # type: ignore[arg-type]
            "raw token with spaces",
            now,
        )

    assert malformed_exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert "raw token with spaces" not in str(malformed_exc_info.value)
    assert_raw_details_not_exposed(malformed_exc_info.value)

    monkeypatch.setattr(
        "app.telegram.service."
        "get_valid_telegram_link_token_for_consume_by_hash_for_update",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(TelegramLinkTokenConsumeError) as unknown_exc_info:
        get_valid_link_token_for_consume(
            object(),  # type: ignore[arg-type]
            RawTelegramLinkToken("raw_secret_token"),
            now,
        )

    assert unknown_exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert "raw_secret_token" not in str(unknown_exc_info.value)
    assert_raw_details_not_exposed(unknown_exc_info.value)


def test_m4_non_error_outcome_catalog_is_stable_and_backward_compatible() -> None:
    assert tuple(outcome.value for outcome in TelegramLinkOutcome) == (
        M4_STABLE_OUTCOMES
    )
    assert TelegramStartTokenConsumeOutcome is TelegramLinkOutcome
    assert TelegramStartTokenConsumeOutcome.LINKED is TelegramLinkOutcome.LINKED
    assert TelegramStartTokenConsumeOutcome.RELINKED is TelegramLinkOutcome.RELINKED
    assert (
        TelegramStartTokenConsumeOutcome.ALREADY_LINKED_TO_THIS_CHAT
        is TelegramLinkOutcome.ALREADY_LINKED_TO_THIS_CHAT
    )
    assert TelegramLinkStatus.LINKED.value == TelegramLinkOutcome.LINKED.value
    assert TelegramLinkStatus.UNLINKED.value == TelegramLinkOutcome.UNLINKED.value

    unlink_result = UnlinkedTelegramLink(
        link=object(),  # type: ignore[arg-type]
        event=object(),  # type: ignore[arg-type]
        invalidated_token_count=0,
    )

    assert unlink_result.outcome is TelegramLinkOutcome.UNLINKED
    assert "UNLINKED" in repr(unlink_result)
    assert "16005550123" not in repr(unlink_result)


def test_internal_bot_username_config_exception_is_stable_and_raw_free() -> None:
    exc = TelegramBotUsernameNotConfigured()

    assert isinstance(exc, RuntimeError)
    assert "not configured" in str(exc)
    assert not hasattr(exc, "error_code")
    assert "raw_secret_token" not in str(exc)
    assert "TELEGRAM_BOT_TOKEN" not in str(exc)
