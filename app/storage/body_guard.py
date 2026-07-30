from collections.abc import Collection
from typing import Final

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.error_codes import (
    ErrorCode,
    get_error_http_status,
    get_public_error_body,
)

DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES: Final = 11_010_048
M8_STORAGE_BODY_GUARD_PATHS: Final[frozenset[str]] = frozenset()


class _BodyLimitExceeded(Exception):
    pass


class StorageBodyLimitMiddleware:
    """Reject oversized request bodies before multipart parsing.

    Paths are deliberately opt-in. M8 does not register a production upload
    route, so ``M8_STORAGE_BODY_GUARD_PATHS`` remains empty.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        protected_paths: Collection[str],
        max_body_bytes: int = DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES,
    ) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        if any(not path.startswith("/") for path in protected_paths):
            raise ValueError("protected paths must be absolute")

        self._app = app
        self._protected_paths = frozenset(protected_paths)
        self._max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("path") not in self._protected_paths:
            await self._app(scope, receive, send)
            return

        declared_length = _get_trusted_content_length(scope)
        if declared_length is not None and declared_length > self._max_body_bytes:
            await _send_file_too_large(scope, receive, send)
            return

        received_bytes = 0
        disconnected = False

        async def limited_receive() -> Message:
            nonlocal disconnected, received_bytes
            if disconnected:
                return {"type": "http.disconnect"}

            message = await receive()
            message_type = message["type"]
            if message_type == "http.disconnect":
                disconnected = True
                return message
            if message_type != "http.request":
                return message

            body = message.get("body", b"")
            received_bytes += len(body)
            if received_bytes > self._max_body_bytes:
                disconnected = True
                raise _BodyLimitExceeded
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _BodyLimitExceeded:
            await _send_file_too_large(scope, receive, send)


def _get_trusted_content_length(scope: Scope) -> int | None:
    raw_values = [
        value.strip()
        for name, value in scope.get("headers", ())
        if name.lower() == b"content-length"
    ]
    if not raw_values:
        return None

    parsed_values: list[int] = []
    for value in raw_values:
        if not value or not value.isdigit():
            return None
        parsed_values.append(int(value))

    if len(set(parsed_values)) != 1:
        return None
    return parsed_values[0]


async def _send_file_too_large(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        {"detail": get_public_error_body(ErrorCode.FILE_TOO_LARGE)},
        status_code=get_error_http_status(ErrorCode.FILE_TOO_LARGE),
        headers={
            "Cache-Control": "no-store",
            "X-Error-Code": ErrorCode.FILE_TOO_LARGE.value,
        },
    )
    await response(scope, receive, send)
