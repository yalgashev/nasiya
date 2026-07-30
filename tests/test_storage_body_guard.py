import asyncio
import json
from collections.abc import Iterable
from typing import Any

import pytest
from fastapi import FastAPI, Request
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.storage.body_guard import (
    DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES,
    M8_STORAGE_BODY_GUARD_PATHS,
    StorageBodyLimitMiddleware,
)

TEST_LIMIT = 8
PROTECTED_PATH = "/future-storage-upload"


def _tiny_app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={PROTECTED_PATH},
        max_body_bytes=TEST_LIMIT,
    )

    @application.post(PROTECTED_PATH)
    async def protected(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @application.post("/unrelated")
    async def unrelated(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    return application


def _run_asgi_request(
    application: FastAPI,
    *,
    path: str,
    messages: Iterable[dict[str, Any]],
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[str, str], bytes, int]:
    pending = list(messages)
    sent: list[dict[str, Any]] = []
    receive_calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    asyncio.run(application(scope, receive, send))

    start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    response_headers = {
        name.decode("latin-1").casefold(): value.decode("latin-1")
        for name, value in start["headers"]
    }
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, body, receive_calls


def _body_messages(*chunks: bytes) -> list[dict[str, Any]]:
    return [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]


def test_m8_production_path_set_is_empty_and_default_is_frozen() -> None:
    assert M8_STORAGE_BODY_GUARD_PATHS == frozenset()
    assert DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES == 11_010_048


def test_declared_content_length_above_limit_is_rejected_before_receive() -> None:
    status, headers, body, receive_calls = _run_asgi_request(
        _tiny_app(),
        path=PROTECTED_PATH,
        messages=_body_messages(b"must-not-be-read"),
        headers=[(b"content-length", str(TEST_LIMIT + 1).encode("ascii"))],
    )

    assert status == 413
    assert headers["cache-control"] == "no-store"
    assert headers["x-error-code"] == ErrorCode.FILE_TOO_LARGE.value
    assert json.loads(body) == {
        "detail": get_public_error_body(ErrorCode.FILE_TOO_LARGE)
    }
    assert receive_calls == 0


@pytest.mark.parametrize(
    ("chunks", "headers"),
    [
        ((b"123", b"45678"), []),
        ((b"1234", b"5678"), [(b"content-length", b"1")]),
        ((b"1234", b"5678"), [(b"content-length", b"invalid")]),
    ],
)
def test_chunked_missing_or_forged_length_allows_exact_actual_limit(
    chunks: tuple[bytes, ...],
    headers: list[tuple[bytes, bytes]],
) -> None:
    status, _, body, _ = _run_asgi_request(
        _tiny_app(),
        path=PROTECTED_PATH,
        messages=_body_messages(*chunks),
        headers=headers,
    )

    assert status == 200
    assert json.loads(body) == {"received": TEST_LIMIT}


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"content-length", b"1")],
        [(b"content-length", str(TEST_LIMIT).encode("ascii"))],
    ],
)
def test_missing_or_forged_length_rejects_actual_limit_plus_one(
    headers: list[tuple[bytes, bytes]],
) -> None:
    status, _, body, _ = _run_asgi_request(
        _tiny_app(),
        path=PROTECTED_PATH,
        messages=_body_messages(b"1234", b"5678", b"9"),
        headers=headers,
    )

    assert status == 413
    assert json.loads(body) == {
        "detail": get_public_error_body(ErrorCode.FILE_TOO_LARGE)
    }


def test_unrelated_route_is_unchanged() -> None:
    oversized_body = b"x" * (TEST_LIMIT + 10)
    status, _, body, _ = _run_asgi_request(
        _tiny_app(),
        path="/unrelated",
        messages=_body_messages(oversized_body),
        headers=[(b"content-length", str(len(oversized_body)).encode("ascii"))],
    )

    assert status == 200
    assert json.loads(body) == {"received": len(oversized_body)}


def test_disconnect_is_forwarded_without_a_body_dump() -> None:
    application = FastAPI()
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={PROTECTED_PATH},
        max_body_bytes=TEST_LIMIT,
    )

    @application.post(PROTECTED_PATH)
    async def protected(request: Request) -> JSONResponse:
        try:
            await request.body()
        except ClientDisconnect:
            return JSONResponse({"disconnected": True}, status_code=499)
        raise AssertionError("disconnect was not forwarded")

    status, _, body, _ = _run_asgi_request(
        application,
        path=PROTECTED_PATH,
        messages=[{"type": "http.disconnect"}],
    )

    assert status == 499
    assert json.loads(body) == {"disconnected": True}
