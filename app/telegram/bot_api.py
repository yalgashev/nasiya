from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from app.settings import TelegramWorkerCredentials
from app.telegram.bot import TelegramBotUsername
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_LONG_POLL_SECONDS = 25
TELEGRAM_HTTP_READ_TIMEOUT_SECONDS = 35
TELEGRAM_HTTP_CONNECT_TIMEOUT_SECONDS = 10
TELEGRAM_HTTP_CONNECT_RETRIES = 1
TELEGRAM_ALLOWED_UPDATES = ("message",)
TELEGRAM_BACKOFF_BASE_SECONDS = 1.0
TELEGRAM_BACKOFF_CAP_SECONDS = 30.0
TELEGRAM_RETRY_AFTER_CAP_SECONDS = 60.0
TELEGRAM_UPDATE_ID_MAX = (1 << 63) - 2
TELEGRAM_LANGUAGE_CODE_MAX_LENGTH = 35


class TelegramApiErrorCode(StrEnum):
    FATAL_CREDENTIAL = "TELEGRAM_API_FATAL_CREDENTIAL"
    FATAL_POLLER_CONFLICT = "TELEGRAM_API_FATAL_POLLER_CONFLICT"
    FATAL_REQUEST = "TELEGRAM_API_FATAL_REQUEST"
    PROTOCOL = "TELEGRAM_API_PROTOCOL"
    TRANSIENT_RATE_LIMIT = "TELEGRAM_API_TRANSIENT_RATE_LIMIT"
    TRANSIENT_SERVER = "TELEGRAM_API_TRANSIENT_SERVER"
    TRANSIENT_NETWORK = "TELEGRAM_API_TRANSIENT_NETWORK"


_TRANSIENT_API_CODES = frozenset(
    {
        TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
        TelegramApiErrorCode.TRANSIENT_SERVER,
        TelegramApiErrorCode.TRANSIENT_NETWORK,
    }
)


class TelegramApiError(RuntimeError):
    __slots__ = ("code", "retry_after_seconds")

    def __init__(
        self,
        code: TelegramApiErrorCode,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Telegram Bot API operation failed ({code.value})")

    @property
    def is_transient(self) -> bool:
        return self.code in _TRANSIENT_API_CODES

    def __repr__(self) -> str:
        return (
            "TelegramApiError("
            f"code={self.code.value!r}, retry_after_seconds="
            f"{self.retry_after_seconds!r})"
        )


@dataclass(frozen=True)
class TelegramBotIdentity:
    bot_id: int
    username: TelegramBotUsername
    is_bot: bool


@dataclass(frozen=True)
class TelegramWebhookInfo:
    active: bool


@dataclass(frozen=True, repr=False)
class TelegramMessageEnvelope:
    chat_id: int | None
    chat_type: str | None
    text: str | None
    structurally_valid: bool
    language_code: str | None = None

    def __repr__(self) -> str:
        return (
            "TelegramMessageEnvelope("
            "chat_id=<redacted>, chat_type=<redacted>, text=<redacted>, "
            f"structurally_valid={self.structurally_valid!r}, "
            "language_code=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class TelegramUpdateEnvelope:
    update_id: int
    message: TelegramMessageEnvelope | None = None

    def __repr__(self) -> str:
        return (
            f"TelegramUpdateEnvelope(update_id={self.update_id!r}, message=<redacted>)"
        )


class TelegramPreflightStatus(StrEnum):
    READY = "READY"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    FATAL_FAILURE = "FATAL_FAILURE"


class TelegramPreflightCode(StrEnum):
    READY = "TELEGRAM_PREFLIGHT_READY"
    API_TRANSIENT = "TELEGRAM_PREFLIGHT_API_TRANSIENT"
    BAD_CREDENTIAL = "TELEGRAM_PREFLIGHT_BAD_CREDENTIAL"
    POLLER_CONFLICT = "TELEGRAM_PREFLIGHT_POLLER_CONFLICT"
    PROTOCOL = "TELEGRAM_PREFLIGHT_PROTOCOL"
    NOT_A_BOT = "TELEGRAM_PREFLIGHT_NOT_A_BOT"
    USERNAME_MISMATCH = "TELEGRAM_PREFLIGHT_USERNAME_MISMATCH"
    WEBHOOK_ACTIVE = "TELEGRAM_PREFLIGHT_WEBHOOK_ACTIVE"


@dataclass(frozen=True)
class TelegramPreflightResult:
    status: TelegramPreflightStatus
    code: TelegramPreflightCode

    @property
    def ready(self) -> bool:
        return self.status is TelegramPreflightStatus.READY


class TelegramBotApiClient:
    __slots__ = ("_http_client",)

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._http_client = http_client

    def __repr__(self) -> str:
        return "TelegramBotApiClient(token=<redacted>)"

    async def get_me(self) -> TelegramBotIdentity:
        result = await self._post("getMe", {})
        if not isinstance(result, Mapping):
            raise _protocol_error()

        bot_id = result.get("id")
        is_bot = result.get("is_bot")
        username = result.get("username")
        if (
            isinstance(bot_id, bool)
            or not isinstance(bot_id, int)
            or bot_id < 1
            or not isinstance(is_bot, bool)
            or not isinstance(username, str)
        ):
            raise _protocol_error()
        try:
            canonical_username = TelegramBotUsername(username)
        except ValueError:
            raise _protocol_error() from None

        return TelegramBotIdentity(
            bot_id=bot_id,
            username=canonical_username,
            is_bot=is_bot,
        )

    async def get_webhook_info(self) -> TelegramWebhookInfo:
        result = await self._post("getWebhookInfo", {})
        if not isinstance(result, Mapping):
            raise _protocol_error()
        webhook_url = result.get("url")
        if not isinstance(webhook_url, str):
            raise _protocol_error()
        return TelegramWebhookInfo(active=bool(webhook_url))

    async def get_updates(
        self,
        *,
        offset: int,
        timeout: int,
        allowed_updates: Sequence[str],
    ) -> tuple[TelegramUpdateEnvelope, ...]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Telegram update offset must be a non-negative integer")
        if timeout != TELEGRAM_LONG_POLL_SECONDS:
            raise ValueError("Telegram long-poll timeout must use the approved value")
        if tuple(allowed_updates) != TELEGRAM_ALLOWED_UPDATES:
            raise ValueError("Telegram allowed_updates must contain only message")

        result = await self._post(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": list(TELEGRAM_ALLOWED_UPDATES),
            },
        )
        if not isinstance(result, list):
            raise _protocol_error()

        updates: list[TelegramUpdateEnvelope] = []
        for raw_update in result:
            if not isinstance(raw_update, Mapping):
                raise _protocol_error()
            update_id = raw_update.get("update_id")
            if (
                isinstance(update_id, bool)
                or not isinstance(update_id, int)
                or update_id < 0
                or update_id > TELEGRAM_UPDATE_ID_MAX
            ):
                raise _protocol_error()
            updates.append(
                TelegramUpdateEnvelope(
                    update_id=update_id,
                    message=_parse_message_envelope(raw_update),
                )
            )
        return tuple(updates)

    async def send_message(
        self,
        *,
        chat_id: VerifiedPrivateTelegramChatIdentity,
        text: str,
        timeout_seconds: float | None = None,
    ) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError("Telegram message text must be non-empty and bounded")

        result = await self._post(
            "sendMessage",
            {
                "chat_id": chat_id.as_bigint(),
                "text": text,
            },
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, Mapping):
            raise _protocol_error()
        message_id = result.get("message_id")
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 1
        ):
            raise _protocol_error()

    async def _post(
        self,
        method: str,
        payload: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        request_url = f"{TELEGRAM_API_BASE_URL}/{method}"
        timeout = _telegram_request_timeout(timeout_seconds)
        try:
            response = await self._http_client.post(
                request_url,
                json=dict(payload),
                timeout=timeout,
            )
        except httpx.TransportError:
            raise TelegramApiError(TelegramApiErrorCode.TRANSIENT_NETWORK) from None

        if not 200 <= response.status_code < 300:
            raise classify_telegram_http_error(
                response.status_code,
                retry_after_seconds=_extract_retry_after(response),
            )

        body = _parse_json_object(response)
        if body.get("ok") is not True:
            error_status = body.get("error_code")
            if isinstance(error_status, bool) or not isinstance(error_status, int):
                raise _protocol_error()
            raise classify_telegram_http_error(
                error_status,
                retry_after_seconds=_extract_retry_after_from_body(body),
            )
        if "result" not in body:
            raise _protocol_error()
        return body["result"]


def _telegram_request_timeout(timeout_seconds: float | None) -> httpx.Timeout:
    if timeout_seconds is not None:
        if timeout_seconds <= 0:
            raise ValueError("Telegram send timeout must be positive")
        return httpx.Timeout(
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )
    return httpx.Timeout(
        connect=TELEGRAM_HTTP_CONNECT_TIMEOUT_SECONDS,
        read=TELEGRAM_HTTP_READ_TIMEOUT_SECONDS,
        write=TELEGRAM_HTTP_CONNECT_TIMEOUT_SECONDS,
        pool=TELEGRAM_HTTP_CONNECT_TIMEOUT_SECONDS,
    )


def create_telegram_http_client(
    credentials: TelegramWorkerCredentials,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    # HTTPX limits this retry to connection setup, before request bytes are sent.
    inner_transport = transport or httpx.AsyncHTTPTransport(
        retries=TELEGRAM_HTTP_CONNECT_RETRIES
    )
    token_transport = _TelegramTokenPathTransport(
        token=credentials.bot_token.get_secret_value(),
        inner_transport=inner_transport,
    )
    return httpx.AsyncClient(transport=token_transport)


class _TelegramTokenPathTransport(httpx.AsyncBaseTransport):
    __slots__ = ("_inner_transport", "_token")

    def __init__(
        self,
        *,
        token: str,
        inner_transport: httpx.AsyncBaseTransport,
    ) -> None:
        self._token = token
        self._inner_transport = inner_transport

    def __repr__(self) -> str:
        return "_TelegramTokenPathTransport(token=<redacted>)"

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        safe_url = request.url
        method_name = safe_url.path.removeprefix("/")
        if (
            safe_url.scheme != "https"
            or safe_url.host != "api.telegram.org"
            or not method_name
            or "/" in method_name
        ):
            raise TelegramApiError(TelegramApiErrorCode.FATAL_REQUEST)

        request.url = safe_url.copy_with(
            path=f"/bot{self._token}/{method_name}",
        )
        try:
            return await self._inner_transport.handle_async_request(request)
        finally:
            request.url = safe_url

    async def aclose(self) -> None:
        await self._inner_transport.aclose()


def classify_telegram_http_error(
    status_code: int,
    *,
    retry_after_seconds: float | None = None,
) -> TelegramApiError:
    if status_code in {401, 403}:
        return TelegramApiError(TelegramApiErrorCode.FATAL_CREDENTIAL)
    if status_code == 409:
        return TelegramApiError(TelegramApiErrorCode.FATAL_POLLER_CONFLICT)
    if status_code == 429:
        return TelegramApiError(
            TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
            retry_after_seconds=_bounded_retry_after(retry_after_seconds),
        )
    if 500 <= status_code < 600:
        return TelegramApiError(TelegramApiErrorCode.TRANSIENT_SERVER)
    return TelegramApiError(TelegramApiErrorCode.FATAL_REQUEST)


@dataclass(frozen=True)
class TelegramBackoffPolicy:
    base_seconds: float = TELEGRAM_BACKOFF_BASE_SECONDS
    cap_seconds: float = TELEGRAM_BACKOFF_CAP_SECONDS
    retry_after_cap_seconds: float = TELEGRAM_RETRY_AFTER_CAP_SECONDS

    def delay_seconds(self, error: TelegramApiError, *, attempt: int) -> float:
        if attempt < 0:
            raise ValueError("Retry attempt must be non-negative")
        if not error.is_transient:
            raise ValueError("Fatal Telegram API errors cannot be retried")
        if (
            error.code is TelegramApiErrorCode.TRANSIENT_RATE_LIMIT
            and error.retry_after_seconds is not None
        ):
            return min(error.retry_after_seconds, self.retry_after_cap_seconds)
        return min(self.base_seconds * (2**attempt), self.cap_seconds)

    async def wait(
        self,
        error: TelegramApiError,
        *,
        attempt: int,
        sleeper: Callable[[float], Awaitable[None]],
    ) -> float:
        delay = self.delay_seconds(error, attempt=attempt)
        await sleeper(delay)
        return delay


async def run_telegram_preflight(
    client: TelegramBotApiClient,
    *,
    configured_username: TelegramBotUsername,
) -> TelegramPreflightResult:
    try:
        identity = await client.get_me()
    except TelegramApiError as exc:
        return _preflight_api_failure(exc)

    if not identity.is_bot:
        return TelegramPreflightResult(
            status=TelegramPreflightStatus.FATAL_FAILURE,
            code=TelegramPreflightCode.NOT_A_BOT,
        )
    if identity.username.as_username() != configured_username.as_username():
        return TelegramPreflightResult(
            status=TelegramPreflightStatus.FATAL_FAILURE,
            code=TelegramPreflightCode.USERNAME_MISMATCH,
        )

    try:
        webhook_info = await client.get_webhook_info()
    except TelegramApiError as exc:
        return _preflight_api_failure(exc)

    if webhook_info.active:
        return TelegramPreflightResult(
            status=TelegramPreflightStatus.FATAL_FAILURE,
            code=TelegramPreflightCode.WEBHOOK_ACTIVE,
        )
    return TelegramPreflightResult(
        status=TelegramPreflightStatus.READY,
        code=TelegramPreflightCode.READY,
    )


def _preflight_api_failure(error: TelegramApiError) -> TelegramPreflightResult:
    if error.is_transient:
        return TelegramPreflightResult(
            status=TelegramPreflightStatus.TRANSIENT_FAILURE,
            code=TelegramPreflightCode.API_TRANSIENT,
        )
    code = {
        TelegramApiErrorCode.FATAL_CREDENTIAL: TelegramPreflightCode.BAD_CREDENTIAL,
        TelegramApiErrorCode.FATAL_POLLER_CONFLICT: (
            TelegramPreflightCode.POLLER_CONFLICT
        ),
        TelegramApiErrorCode.FATAL_REQUEST: TelegramPreflightCode.PROTOCOL,
        TelegramApiErrorCode.PROTOCOL: TelegramPreflightCode.PROTOCOL,
    }[error.code]
    return TelegramPreflightResult(
        status=TelegramPreflightStatus.FATAL_FAILURE,
        code=code,
    )


def _parse_json_object(response: httpx.Response) -> Mapping[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise _protocol_error() from None
    if not isinstance(body, Mapping):
        raise _protocol_error()
    return body


def _parse_message_envelope(
    raw_update: Mapping[str, Any],
) -> TelegramMessageEnvelope | None:
    if "message" not in raw_update:
        return None
    raw_message = raw_update.get("message")
    if not isinstance(raw_message, Mapping):
        return _malformed_message_envelope()
    raw_chat = raw_message.get("chat")
    if not isinstance(raw_chat, Mapping):
        return _malformed_message_envelope()

    chat_id = raw_chat.get("id")
    chat_type = raw_chat.get("type")
    text = raw_message.get("text")
    raw_sender = raw_message.get("from")
    language_code = (
        raw_sender.get("language_code") if isinstance(raw_sender, Mapping) else None
    )
    if (
        not isinstance(language_code, str)
        or not language_code
        or len(language_code) > TELEGRAM_LANGUAGE_CODE_MAX_LENGTH
    ):
        language_code = None
    if (
        isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or not isinstance(chat_type, str)
        or (text is not None and not isinstance(text, str))
    ):
        return _malformed_message_envelope()
    return TelegramMessageEnvelope(
        chat_id=chat_id,
        chat_type=chat_type,
        text=text,
        structurally_valid=True,
        language_code=language_code,
    )


def _malformed_message_envelope() -> TelegramMessageEnvelope:
    return TelegramMessageEnvelope(
        chat_id=None,
        chat_type=None,
        text=None,
        structurally_valid=False,
        language_code=None,
    )


def _extract_retry_after(response: httpx.Response) -> float | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, Mapping):
        return None
    return _extract_retry_after_from_body(body)


def _extract_retry_after_from_body(body: Mapping[str, Any]) -> float | None:
    parameters = body.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    retry_after = parameters.get("retry_after")
    if (
        isinstance(retry_after, bool)
        or not isinstance(retry_after, (int, float))
        or retry_after <= 0
    ):
        return None
    return float(retry_after)


def _bounded_retry_after(value: float | None) -> float | None:
    if value is None:
        return None
    return min(value, TELEGRAM_RETRY_AFTER_CAP_SECONDS)


def _protocol_error() -> TelegramApiError:
    return TelegramApiError(TelegramApiErrorCode.PROTOCOL)
