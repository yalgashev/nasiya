import asyncio
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from app.auth.csrf import get_csrf_token
from app.auth.deps import (
    CsrfFailed,
    CurrentSessionContext,
    CurrentSessionStatus,
    csrf_failed_exception_handler,
)
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.storage.body_guard import (
    DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES,
    StorageBodyLimitMiddleware,
)
from app.storage.multipart import (
    MAX_FILE_PART_BYTES,
    MAX_MULTIPART_FIELDS,
    bounded_multipart_upload,
)
from app.telegram.client_ip import ResolvedClientIp

UPLOAD_PATH = "/_test/future-storage-upload"
GUARD_PATH = "/_test/future-storage-guard"
NOW = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)
SMALL_LIMIT = 8
PRIVATE_FILENAME = "../../private customer <script>.png"
PRIVATE_HEADER_VALUE = "private-part-header-value"
PRIVATE_BODY = b"private-body-must-not-be-logged"


def _session_context() -> tuple[CurrentSessionContext, str]:
    session_id = uuid4()
    user_id = uuid4()
    session = AuthSession(
        id=session_id,
        user_id=user_id,
        active_shop_id=None,
        token_hash="a" * 64,
        csrf_secret="csrf-secret-for-adversarial-storage-multipart",
        user_agent="pytest",
        created_at=NOW,
        last_seen_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
        revoked_at=None,
    )
    context = CurrentSessionContext(
        status=CurrentSessionStatus.AUTHENTICATED,
        session_id=session_id,
        user_id=user_id,
        _session=session,
    )
    return context, get_csrf_token(session).as_form_value()


def _multipart_client() -> tuple[
    TestClient,
    str,
    list[str],
    list[UploadFile],
]:
    context, csrf_token = _session_context()
    events: list[str] = []
    observed_files: list[UploadFile] = []
    application = FastAPI()
    application.add_exception_handler(CsrfFailed, csrf_failed_exception_handler)
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={UPLOAD_PATH},
        max_body_bytes=DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES,
    )

    @application.post(UPLOAD_PATH)
    async def future_storage_upload(request: Request) -> dict[str, int]:
        events.append("authenticated")
        assert context.user_id is not None
        resolved_ip = ResolvedClientIp("192.0.2.40")
        events.append("trusted_ip")
        async with bounded_multipart_upload(
            request,
            file_field_name="image",
            session_context=context,
            now=NOW,
        ) as bounded:
            events.append("storage_user_ip_rate_limit")
            assert isinstance(resolved_ip, ResolvedClientIp)
            upload_file = bounded.as_upload_file()
            observed_files.append(upload_file)
            return {
                "size_bytes": bounded.size_bytes,
                "declared_length": int(request.headers["content-length"]),
            }

    return TestClient(application), csrf_token, events, observed_files


def _guard_app(events: list[str]) -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={GUARD_PATH},
        max_body_bytes=SMALL_LIMIT,
    )

    @application.post(GUARD_PATH)
    async def future_guard(request: Request) -> dict[str, int]:
        events.append("route")
        return {"received": len(await request.body())}

    return application


def _run_asgi_request(
    application: FastAPI,
    *,
    messages: Iterable[dict[str, Any]],
    headers: list[tuple[bytes, bytes]],
) -> tuple[int, bytes, int]:
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
        "path": GUARD_PATH,
        "raw_path": GUARD_PATH.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("192.0.2.40", 41000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    asyncio.run(application(scope, receive, send))
    start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return start["status"], body, receive_calls


def _messages(*chunks: bytes) -> list[dict[str, Any]]:
    return [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]


@pytest.mark.parametrize(
    "headers",
    (
        [],
        [(b"content-length", b"1")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"invalid")],
        [(b"content-length", b"8"), (b"content-length", b"7")],
        [(b"content-length", b"8"), (b"content-length", b"8")],
    ),
)
def test_missing_forged_negative_and_multiple_lengths_count_exact_actual_bytes(
    headers: list[tuple[bytes, bytes]],
) -> None:
    events: list[str] = []

    status, body, receive_calls = _run_asgi_request(
        _guard_app(events),
        messages=_messages(b"123", b"45", b"678"),
        headers=headers,
    )

    assert status == 200
    assert json.loads(body) == {"received": SMALL_LIMIT}
    assert events == ["route"]
    assert receive_calls == 3


@pytest.mark.parametrize(
    "headers",
    (
        [],
        [(b"content-length", b"1")],
        [(b"content-length", b"-9")],
        [(b"content-length", b"8"), (b"content-length", b"9")],
    ),
)
def test_chunked_limit_plus_one_is_bounded_without_sleep(
    headers: list[tuple[bytes, bytes]],
) -> None:
    events: list[str] = []

    status, body, receive_calls = _run_asgi_request(
        _guard_app(events),
        messages=_messages(*(b"x" for _index in range(SMALL_LIMIT + 1))),
        headers=headers,
    )

    assert status == 413
    assert json.loads(body)["detail"]["code"] == ErrorCode.FILE_TOO_LARGE.value
    assert events == ["route"]
    assert receive_calls == SMALL_LIMIT + 1


def test_duplicate_declared_oversize_rejects_before_auth_or_receive() -> None:
    events: list[str] = []

    status, body, receive_calls = _run_asgi_request(
        _guard_app(events),
        messages=_messages(PRIVATE_BODY),
        headers=[
            (b"content-length", b"9"),
            (b"content-length", b"9"),
        ],
    )

    assert status == 413
    assert json.loads(body)["detail"]["code"] == ErrorCode.FILE_TOO_LARGE.value
    assert events == []
    assert receive_calls == 0


@pytest.mark.parametrize(
    ("form_token", "expected_status", "expected_events"),
    (
        (None, 403, ["authenticated", "trusted_ip"]),
        ("wrong", 403, ["authenticated", "trusted_ip"]),
        (
            "correct",
            200,
            [
                "authenticated",
                "trusted_ip",
                "storage_user_ip_rate_limit",
            ],
        ),
    ),
)
def test_auth_ip_multipart_csrf_and_rate_ordering(
    form_token: str | None,
    expected_status: int,
    expected_events: list[str],
) -> None:
    client, csrf_token, events, observed_files = _multipart_client()
    data = {}
    if form_token is not None:
        data["csrf_token"] = csrf_token if form_token == "correct" else form_token

    response = client.post(
        UPLOAD_PATH,
        data=data,
        files={"image": ("synthetic.png", b"safe", "image/png")},
    )

    assert response.status_code == expected_status
    assert events == expected_events
    if expected_status == 200:
        assert observed_files[0].file.closed is True
    else:
        assert observed_files == []


def test_multipart_envelope_allows_exact_file_part_and_overhead() -> None:
    client, csrf_token, events, observed_files = _multipart_client()

    response = client.post(
        UPLOAD_PATH,
        data={"csrf_token": csrf_token, "caption": "bounded"},
        files={
            "image": (
                "synthetic.bin",
                b"x" * MAX_FILE_PART_BYTES,
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["size_bytes"] == MAX_FILE_PART_BYTES
    assert MAX_FILE_PART_BYTES < body["declared_length"]
    assert body["declared_length"] <= DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES
    assert events[-1] == "storage_user_ip_rate_limit"
    assert observed_files[0].file.closed is True


def test_duplicate_files_many_fields_and_filename_header_abuse_are_closed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, csrf_token, _events, observed_files = _multipart_client()
    fields = [("csrf_token", csrf_token)]
    fields.extend((f"field_{index}", "x") for index in range(MAX_MULTIPART_FIELDS))

    with caplog.at_level(logging.DEBUG):
        duplicate = client.post(
            UPLOAD_PATH,
            data={"csrf_token": csrf_token},
            files=[
                ("image", (PRIVATE_FILENAME, b"first", "image/png")),
                ("image", ("second-private.png", b"second", "image/png")),
            ],
        )
        many_fields = client.post(
            UPLOAD_PATH,
            data=dict(fields),
            files={"image": (PRIVATE_FILENAME, b"safe", "image/png")},
        )
        header_abuse = client.post(
            UPLOAD_PATH,
            data={"csrf_token": csrf_token},
            files={
                "image": (
                    PRIVATE_FILENAME,
                    b"safe",
                    "image/png",
                    {"X-Private-Part": PRIVATE_HEADER_VALUE},
                )
            },
        )

    assert duplicate.status_code == 415
    assert many_fields.status_code == 415
    assert header_abuse.status_code == 200
    assert header_abuse.json()["size_bytes"] == len(b"safe")
    assert observed_files[-1].file.closed is True
    rendered = f"{duplicate.text} {many_fields.text} {header_abuse.text} {caplog.text}"
    for hidden in (
        PRIVATE_FILENAME,
        PRIVATE_HEADER_VALUE,
        PRIVATE_BODY.decode(),
        "/tmp/",
    ):
        assert hidden not in rendered


def test_disconnect_is_forwarded_and_does_not_log_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = FastAPI()
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={GUARD_PATH},
        max_body_bytes=SMALL_LIMIT,
    )

    @application.post(GUARD_PATH)
    async def guarded_disconnect(request: Request) -> JSONResponse:
        try:
            await request.body()
        except ClientDisconnect:
            return JSONResponse({"disconnected": True}, status_code=499)
        raise AssertionError("disconnect was not forwarded")

    with caplog.at_level(logging.DEBUG):
        status, body, receive_calls = _run_asgi_request(
            application,
            messages=[{"type": "http.disconnect"}],
            headers=[],
        )

    assert status == 499
    assert json.loads(body) == {"disconnected": True}
    assert receive_calls == 1
    assert PRIVATE_BODY.decode() not in caplog.text
