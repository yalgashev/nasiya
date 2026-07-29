import asyncio
import inspect
import json
import logging
from collections.abc import Awaitable, Callable

import httpx
import pytest
from pydantic import SecretStr

import app.telegram.bot_api as bot_api_module
from app import main as app_main
from app.settings import TelegramWorkerCredentials
from app.telegram.bot import TelegramBotUsername
from app.telegram.bot_api import (
    TELEGRAM_ALLOWED_UPDATES,
    TELEGRAM_BACKOFF_CAP_SECONDS,
    TELEGRAM_HTTP_CONNECT_RETRIES,
    TELEGRAM_HTTP_READ_TIMEOUT_SECONDS,
    TELEGRAM_LONG_POLL_SECONDS,
    TELEGRAM_RETRY_AFTER_CAP_SECONDS,
    TELEGRAM_UPDATE_ID_MAX,
    TelegramApiError,
    TelegramApiErrorCode,
    TelegramBackoffPolicy,
    TelegramBotApiClient,
    TelegramPreflightCode,
    TelegramPreflightStatus,
    classify_telegram_http_error,
    create_telegram_http_client,
    run_telegram_preflight,
)
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity

RAW_TOKEN = "123456789:SensitiveBotApiToken"
BOT_USERNAME = TelegramBotUsername("Nasiya_LinkBot")


def run(coroutine: Awaitable[object]):
    return asyncio.run(coroutine)


def credentials() -> TelegramWorkerCredentials:
    return TelegramWorkerCredentials(
        bot_token=SecretStr(RAW_TOKEN),
        bot_username=BOT_USERNAME,
    )


def test_default_transport_retries_connection_setup_once(monkeypatch) -> None:
    captured_retries = []
    inner_transport = httpx.MockTransport(
        lambda _request: json_response(500, {"ok": False})
    )

    def build_transport(*, retries: int):
        captured_retries.append(retries)
        return inner_transport

    monkeypatch.setattr(bot_api_module.httpx, "AsyncHTTPTransport", build_transport)

    async def scenario() -> None:
        async with create_telegram_http_client(credentials()):
            pass

    run(scenario())

    assert TELEGRAM_HTTP_CONNECT_RETRIES == 1
    assert captured_retries == [1]


async def with_client(
    handler: Callable[[httpx.Request], httpx.Response],
    operation: Callable[[TelegramBotApiClient], Awaitable[object]],
):
    async with create_telegram_http_client(
        credentials(),
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = TelegramBotApiClient(
            http_client=http_client,
        )
        return await operation(client)


def json_response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def test_bot_api_boundary_is_narrow_and_has_no_domain_or_db_import() -> None:
    public_methods = {
        name
        for name, member in inspect.getmembers(
            TelegramBotApiClient,
            predicate=inspect.iscoroutinefunction,
        )
        if not name.startswith("_")
    }
    source = inspect.getsource(bot_api_module)
    web_source = inspect.getsource(app_main)

    assert public_methods == {
        "get_me",
        "get_updates",
        "get_webhook_info",
        "send_message",
    }
    assert "app.telegram.service" not in source
    assert "sqlalchemy" not in source
    assert "psycopg" not in source
    assert "run_telegram_preflight" not in web_source
    assert "TelegramBotApiClient" not in web_source


def test_client_repr_and_errors_do_not_expose_token(caplog) -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive transport detail", request=request)

    async def scenario(client: TelegramBotApiClient) -> None:
        assert RAW_TOKEN not in repr(client)
        try:
            await client.get_me()
        except TelegramApiError as exc:
            logger = logging.getLogger("tests.telegram_bot_api")
            with caplog.at_level(logging.ERROR):
                logger.exception("telegram request failed")
            rendered = f"{exc!s} {exc!r} {caplog.text}"
            assert RAW_TOKEN not in rendered
            assert "sensitive transport detail" not in rendered
            assert exc.code is TelegramApiErrorCode.TRANSIENT_NETWORK
        else:
            pytest.fail("transport timeout was accepted")

    run(with_client(timeout_handler, scenario))


def test_successful_httpx_info_log_uses_safe_url_without_token(caplog) -> None:
    logging.disable(logging.NOTSET)
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.disabled = False
    httpx_logger.propagate = True

    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {
                "ok": True,
                "result": {
                    "id": 123456789,
                    "is_bot": True,
                    "username": "Nasiya_LinkBot",
                },
            },
        )

    async def scenario(client: TelegramBotApiClient):
        with caplog.at_level(logging.INFO, logger="httpx"):
            return await client.get_me()

    run(with_client(handler, scenario))

    assert RAW_TOKEN not in caplog.text
    assert "https://api.telegram.org/getMe" in caplog.text


def test_get_me_and_webhook_info_parse_minimal_safe_results() -> None:
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/getMe"):
            return json_response(
                200,
                {
                    "ok": True,
                    "result": {
                        "id": 123456789,
                        "is_bot": True,
                        "username": "Nasiya_LinkBot",
                        "first_name": "ignored",
                    },
                },
            )
        return json_response(
            200,
            {
                "ok": True,
                "result": {
                    "url": "",
                    "pending_update_count": 99,
                },
            },
        )

    async def scenario(client: TelegramBotApiClient):
        identity = await client.get_me()
        webhook = await client.get_webhook_info()
        return identity, webhook

    identity, webhook = run(with_client(handler, scenario))

    assert identity.bot_id == 123456789
    assert identity.is_bot is True
    assert identity.username.as_username() == "nasiya_linkbot"
    assert webhook.active is False
    assert methods == ["getMe", "getWebhookInfo"]


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "result": {"id": 1, "is_bot": True}},
        {"ok": True, "result": {"id": 1, "is_bot": "true", "username": "Bot"}},
        {"ok": True, "result": []},
    ],
)
def test_get_me_rejects_malformed_success_payload(payload: object) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(200, payload)

    async def scenario(client: TelegramBotApiClient):
        return await client.get_me()

    with pytest.raises(TelegramApiError) as exc_info:
        run(with_client(handler, scenario))

    assert exc_info.value.code is TelegramApiErrorCode.PROTOCOL


def test_malformed_json_is_protocol_failure_without_body_leak(caplog) -> None:
    raw_body = "Sensitive malformed Telegram body"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=raw_body)

    async def scenario(client: TelegramBotApiClient):
        return await client.get_me()

    with pytest.raises(TelegramApiError) as exc_info:
        run(with_client(handler, scenario))

    logger = logging.getLogger("tests.telegram_bot_api")
    with caplog.at_level(logging.ERROR):
        logger.error(
            "protocol failure",
            exc_info=(
                type(exc_info.value),
                exc_info.value,
                exc_info.value.__traceback__,
            ),
        )
    assert exc_info.value.code is TelegramApiErrorCode.PROTOCOL
    assert raw_body not in caplog.text


def test_get_updates_sends_exact_contract_and_preserves_batch_for_worker_sort() -> None:
    captured_payload = {}
    captured_timeout = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload, captured_timeout
        captured_payload = json.loads(request.content)
        captured_timeout = request.extensions["timeout"]["read"]
        return json_response(
            200,
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 12,
                        "message": {
                            "chat": {"id": 9912, "type": "private"},
                            "from": {"language_code": "ru-RU"},
                            "text": "/start safe_token",
                            "ignored": True,
                        },
                    },
                    {"update_id": 10},
                ],
            },
        )

    async def scenario(client: TelegramBotApiClient):
        return await client.get_updates(
            offset=10,
            timeout=TELEGRAM_LONG_POLL_SECONDS,
            allowed_updates=list(TELEGRAM_ALLOWED_UPDATES),
        )

    updates = run(with_client(handler, scenario))

    assert [update.update_id for update in updates] == [12, 10]
    assert updates[0].message is not None
    assert updates[0].message.chat_id == 9912
    assert updates[0].message.chat_type == "private"
    assert updates[0].message.text == "/start safe_token"
    assert updates[0].message.language_code == "ru-RU"
    assert updates[1].message is None
    assert captured_payload == {
        "offset": 10,
        "timeout": 25,
        "allowed_updates": ["message"],
    }
    assert captured_timeout == TELEGRAM_HTTP_READ_TIMEOUT_SECONDS


def test_get_updates_accepts_empty_result() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(200, {"ok": True, "result": []})

    async def scenario(client: TelegramBotApiClient):
        return await client.get_updates(
            offset=0,
            timeout=25,
            allowed_updates=["message"],
        )

    assert run(with_client(handler, scenario)) == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "result": {}},
        {"ok": True, "result": [{}]},
        {"ok": True, "result": [{"update_id": "1"}]},
        {"ok": True, "result": [{"update_id": -1}]},
        {"ok": True, "result": [{"update_id": TELEGRAM_UPDATE_ID_MAX + 1}]},
    ],
)
def test_get_updates_rejects_malformed_envelopes(payload: object) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(200, payload)

    async def scenario(client: TelegramBotApiClient):
        return await client.get_updates(
            offset=0,
            timeout=25,
            allowed_updates=["message"],
        )

    with pytest.raises(TelegramApiError) as exc_info:
        run(with_client(handler, scenario))

    assert exc_info.value.code is TelegramApiErrorCode.PROTOCOL


@pytest.mark.parametrize(
    ("offset", "timeout", "allowed_updates"),
    [
        (-1, 25, ["message"]),
        (0, 24, ["message"]),
        (0, 25, []),
        (0, 25, ["message", "callback_query"]),
    ],
)
def test_get_updates_rejects_unapproved_request_parameters(
    offset: int,
    timeout: int,
    allowed_updates: list[str],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid request reached transport")

    async def scenario(client: TelegramBotApiClient):
        return await client.get_updates(
            offset=offset,
            timeout=timeout,
            allowed_updates=allowed_updates,
        )

    with pytest.raises(ValueError):
        run(with_client(handler, scenario))


def test_send_message_posts_only_chat_and_localized_text() -> None:
    captured_payload = {}
    captured_timeout = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload, captured_timeout
        captured_payload = json.loads(request.content)
        captured_timeout = request.extensions["timeout"]
        return json_response(
            200,
            {"ok": True, "result": {"message_id": 77, "text": "ignored"}},
        )

    async def scenario(client: TelegramBotApiClient):
        return await client.send_message(
            chat_id=VerifiedPrivateTelegramChatIdentity(99887766),
            text="Telegram bog'landi.",
            timeout_seconds=4,
        )

    assert run(with_client(handler, scenario)) is None
    assert captured_payload == {
        "chat_id": 99887766,
        "text": "Telegram bog'landi.",
    }
    assert captured_timeout == {
        "connect": 4,
        "read": 4,
        "write": 4,
        "pool": 4,
    }


def test_send_message_rejects_malformed_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(200, {"ok": True, "result": {}})

    async def scenario(client: TelegramBotApiClient):
        return await client.send_message(
            chat_id=VerifiedPrivateTelegramChatIdentity(99887766),
            text="Safe text",
        )

    with pytest.raises(TelegramApiError) as exc_info:
        run(with_client(handler, scenario))

    assert exc_info.value.code is TelegramApiErrorCode.PROTOCOL


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("server", TelegramApiErrorCode.TRANSIENT_SERVER),
        ("timeout", TelegramApiErrorCode.TRANSIENT_NETWORK),
    ],
)
def test_send_message_classifies_server_and_timeout_failures_without_chat_leak(
    failure: str,
    expected_code: TelegramApiErrorCode,
    caplog,
) -> None:
    raw_chat_id = 998877661234

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive timeout", request=request)
        return json_response(
            503,
            {
                "ok": False,
                "description": f"sensitive chat {raw_chat_id}",
            },
        )

    async def scenario(client: TelegramBotApiClient):
        return await client.send_message(
            chat_id=VerifiedPrivateTelegramChatIdentity(raw_chat_id),
            text="Safe text",
        )

    with pytest.raises(TelegramApiError) as exc_info:
        run(with_client(handler, scenario))

    logger = logging.getLogger("tests.telegram_bot_api")
    with caplog.at_level(logging.ERROR):
        logger.error(
            "send failed",
            exc_info=(
                type(exc_info.value),
                exc_info.value,
                exc_info.value.__traceback__,
            ),
        )
    assert exc_info.value.code is expected_code
    assert (
        str(raw_chat_id) not in f"{exc_info.value!s} {exc_info.value!r} {caplog.text}"
    )


@pytest.mark.parametrize(
    ("status_code", "expected_code", "is_transient"),
    [
        (401, TelegramApiErrorCode.FATAL_CREDENTIAL, False),
        (403, TelegramApiErrorCode.FATAL_CREDENTIAL, False),
        (409, TelegramApiErrorCode.FATAL_POLLER_CONFLICT, False),
        (429, TelegramApiErrorCode.TRANSIENT_RATE_LIMIT, True),
        (500, TelegramApiErrorCode.TRANSIENT_SERVER, True),
        (503, TelegramApiErrorCode.TRANSIENT_SERVER, True),
        (400, TelegramApiErrorCode.FATAL_REQUEST, False),
    ],
)
def test_http_error_classifier_matrix(
    status_code: int,
    expected_code: TelegramApiErrorCode,
    is_transient: bool,
) -> None:
    error = classify_telegram_http_error(
        status_code,
        retry_after_seconds=120,
    )

    assert error.code is expected_code
    assert error.is_transient is is_transient
    if status_code == 429:
        assert error.retry_after_seconds == TELEGRAM_RETRY_AFTER_CAP_SECONDS


def test_http_429_response_extracts_and_caps_retry_after() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "sensitive description",
                "parameters": {"retry_after": 120},
            },
        )

    async def scenario(client: TelegramBotApiClient):
        return await client.send_message(
            chat_id=VerifiedPrivateTelegramChatIdentity(99887766),
            text="Safe text",
        )

    with pytest.raises(TelegramApiError) as exc_info:
        run(with_client(handler, scenario))

    assert exc_info.value.code is TelegramApiErrorCode.TRANSIENT_RATE_LIMIT
    assert exc_info.value.retry_after_seconds == TELEGRAM_RETRY_AFTER_CAP_SECONDS
    assert "sensitive description" not in str(exc_info.value)


def test_backoff_uses_exponential_cap_and_injected_sleeper() -> None:
    policy = TelegramBackoffPolicy()
    delays = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    server_error = TelegramApiError(TelegramApiErrorCode.TRANSIENT_SERVER)

    assert policy.delay_seconds(server_error, attempt=0) == 1
    assert policy.delay_seconds(server_error, attempt=3) == 8
    assert (
        policy.delay_seconds(server_error, attempt=20) == TELEGRAM_BACKOFF_CAP_SECONDS
    )
    waited = run(policy.wait(server_error, attempt=2, sleeper=sleeper))

    assert waited == 4
    assert delays == [4]


def test_backoff_honors_bounded_retry_after_and_rejects_fatal() -> None:
    policy = TelegramBackoffPolicy()
    rate_error = TelegramApiError(
        TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
        retry_after_seconds=45,
    )
    fatal_error = TelegramApiError(TelegramApiErrorCode.FATAL_CREDENTIAL)

    assert policy.delay_seconds(rate_error, attempt=20) == 45
    with pytest.raises(ValueError):
        policy.delay_seconds(fatal_error, attempt=0)


def preflight_handler(
    *,
    username: str = "Nasiya_LinkBot",
    is_bot: bool = True,
    webhook_url: str = "",
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return json_response(
                200,
                {
                    "ok": True,
                    "result": {
                        "id": 123456789,
                        "is_bot": is_bot,
                        "username": username,
                    },
                },
            )
        return json_response(
            200,
            {"ok": True, "result": {"url": webhook_url}},
        )

    return handler


@pytest.mark.parametrize(
    ("handler", "expected_status", "expected_code"),
    [
        (
            preflight_handler(),
            TelegramPreflightStatus.READY,
            TelegramPreflightCode.READY,
        ),
        (
            preflight_handler(username="Other_LinkBot"),
            TelegramPreflightStatus.FATAL_FAILURE,
            TelegramPreflightCode.USERNAME_MISMATCH,
        ),
        (
            preflight_handler(is_bot=False),
            TelegramPreflightStatus.FATAL_FAILURE,
            TelegramPreflightCode.NOT_A_BOT,
        ),
        (
            preflight_handler(webhook_url="https://example.test/telegram"),
            TelegramPreflightStatus.FATAL_FAILURE,
            TelegramPreflightCode.WEBHOOK_ACTIVE,
        ),
    ],
)
def test_preflight_identity_and_webhook_matrix(
    handler: Callable[[httpx.Request], httpx.Response],
    expected_status: TelegramPreflightStatus,
    expected_code: TelegramPreflightCode,
) -> None:
    async def scenario(client: TelegramBotApiClient):
        return await run_telegram_preflight(
            client,
            configured_username=BOT_USERNAME,
        )

    result = run(with_client(handler, scenario))

    assert result.status is expected_status
    assert result.code is expected_code
    assert result.ready is (expected_status is TelegramPreflightStatus.READY)


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_code"),
    [
        (
            401,
            TelegramPreflightStatus.FATAL_FAILURE,
            TelegramPreflightCode.BAD_CREDENTIAL,
        ),
        (
            500,
            TelegramPreflightStatus.TRANSIENT_FAILURE,
            TelegramPreflightCode.API_TRANSIENT,
        ),
    ],
)
def test_preflight_maps_api_failures_to_worker_lifecycle_outcome(
    status_code: int,
    expected_status: TelegramPreflightStatus,
    expected_code: TelegramPreflightCode,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(status_code, {"ok": False})

    async def scenario(client: TelegramBotApiClient):
        return await run_telegram_preflight(
            client,
            configured_username=BOT_USERNAME,
        )

    result = run(with_client(handler, scenario))

    assert result.status is expected_status
    assert result.code is expected_code
