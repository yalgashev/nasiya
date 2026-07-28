import logging

import pytest
from starlette.requests import Request

from app.request_client_ip import ClientIpResolutionError, resolve_client_ip
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp

TEST_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-request-client-ip"


def make_settings(
    *,
    mode: str = "direct",
    trusted_proxy_cidrs: list[str] | None = None,
) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_environment": "testing",
        "debug": False,
        "database_url": TEST_DATABASE_URL,
        "session_cookie_secure": False,
        "rate_limit_hmac_key": TEST_RATE_LIMIT_HMAC_KEY,
        "client_ip_mode": mode,
    }
    if trusted_proxy_cidrs is not None:
        values["trusted_proxy_cidrs"] = trusted_proxy_cidrs
    return Settings(**values)


def make_request(
    peer: str | None,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": headers or [],
        "server": ("testserver", 80),
        "scheme": "http",
    }
    if peer is not None:
        scope["client"] = (peer, 12345)
    return Request(scope)


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        ("203.0.113.10", "203.0.113.10"),
        ("2001:0DB8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ("::ffff:203.0.113.10", "203.0.113.10"),
    ],
)
def test_direct_mode_uses_canonical_socket_peer(
    peer: str,
    expected: str,
) -> None:
    result = resolve_client_ip(make_request(peer), make_settings())

    assert isinstance(result, ResolvedClientIp)
    assert result.as_hmac_input() == expected


def test_direct_mode_ignores_all_proxy_headers_and_duplicates() -> None:
    request = make_request(
        "203.0.113.10",
        headers=[
            (b"x-real-ip", b"198.51.100.1"),
            (b"x-real-ip", b"198.51.100.2"),
            (b"x-forwarded-for", b"198.51.100.3, 198.51.100.4"),
            (b"forwarded", b"for=198.51.100.5"),
        ],
    )

    result = resolve_client_ip(request, make_settings())

    assert result.as_hmac_input() == "203.0.113.10"


@pytest.mark.parametrize("peer", [None, "", "testclient", "999.0.0.1"])
def test_direct_mode_rejects_missing_or_malformed_socket_peer(
    peer: str | None,
) -> None:
    with pytest.raises(ClientIpResolutionError):
        resolve_client_ip(make_request(peer), make_settings())


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("10.10.1.2", "203.0.113.10", "203.0.113.10"),
        ("2001:db8:10::2", "2001:db8:20::5", "2001:db8:20::5"),
        ("::ffff:10.10.1.2", "203.0.113.11", "203.0.113.11"),
    ],
)
def test_trusted_proxy_accepts_one_literal_from_trusted_peer(
    peer: str,
    forwarded: str,
    expected: str,
) -> None:
    settings = make_settings(
        mode="trusted_proxy",
        trusted_proxy_cidrs=["10.0.0.0/8", "2001:db8:10::/48"],
    )
    request = make_request(
        peer,
        headers=[(b"x-real-ip", forwarded.encode("ascii"))],
    )

    result = resolve_client_ip(request, settings)

    assert result.as_hmac_input() == expected


def test_trusted_proxy_overlapping_allowlist_is_deterministic() -> None:
    settings = make_settings(
        mode="trusted_proxy",
        trusted_proxy_cidrs=["10.0.0.0/8", "10.10.0.0/16"],
    )
    request = make_request(
        "10.10.2.3",
        headers=[(b"x-real-ip", b"203.0.113.10")],
    )

    result = resolve_client_ip(request, settings)

    assert result.as_hmac_input() == "203.0.113.10"


def test_trusted_proxy_ignores_xff_and_forwarded() -> None:
    settings = make_settings(
        mode="trusted_proxy",
        trusted_proxy_cidrs=["10.0.0.0/8"],
    )
    request = make_request(
        "10.1.2.3",
        headers=[
            (b"x-real-ip", b"203.0.113.10"),
            (b"x-forwarded-for", b"198.51.100.1"),
            (b"forwarded", b"for=198.51.100.2"),
        ],
    )

    result = resolve_client_ip(request, settings)

    assert result.as_hmac_input() == "203.0.113.10"


@pytest.mark.parametrize(
    ("peer", "headers"),
    [
        ("192.0.2.1", [(b"x-real-ip", b"203.0.113.10")]),
        ("10.1.2.3", []),
        (
            "10.1.2.3",
            [
                (b"x-real-ip", b"203.0.113.10"),
                (b"x-real-ip", b"203.0.113.11"),
            ],
        ),
        ("10.1.2.3", [(b"x-real-ip", b"203.0.113.10, 198.51.100.1")]),
        ("10.1.2.3", [(b"x-real-ip", b" 203.0.113.10")]),
        ("10.1.2.3", [(b"x-real-ip", b"203.0.113.10 ")]),
        ("10.1.2.3", [(b"x-real-ip", b"")]),
        ("10.1.2.3", [(b"x-real-ip", b"not-an-ip")]),
    ],
)
def test_trusted_proxy_rejects_untrusted_or_ambiguous_identity(
    peer: str,
    headers: list[tuple[bytes, bytes]],
) -> None:
    settings = make_settings(
        mode="trusted_proxy",
        trusted_proxy_cidrs=["10.0.0.0/8"],
    )

    with pytest.raises(ClientIpResolutionError):
        resolve_client_ip(make_request(peer, headers=headers), settings)


def test_resolution_failures_and_logging_do_not_echo_raw_values(
    caplog,
) -> None:
    raw_peer = "192.0.2.99"
    raw_header = "203.0.113.77, 198.51.100.22"
    settings = make_settings(
        mode="trusted_proxy",
        trusted_proxy_cidrs=["10.0.0.0/8"],
    )
    request = make_request(
        raw_peer,
        headers=[(b"x-real-ip", raw_header.encode("ascii"))],
    )

    with pytest.raises(ClientIpResolutionError) as exc_info:
        resolve_client_ip(request, settings)

    logger = logging.getLogger("tests.request_client_ip")
    with caplog.at_level(logging.INFO):
        logger.error(
            "resolution failed",
            exc_info=(
                type(exc_info.value),
                exc_info.value,
                exc_info.value.__traceback__,
            ),
        )

    rendered = f"{exc_info.value!s} {exc_info.value!r} {caplog.text}"
    assert raw_peer not in rendered
    assert raw_header not in rendered


def test_malformed_peer_traceback_does_not_echo_raw_value(caplog) -> None:
    raw_peer = "sensitive.invalid.peer"

    try:
        resolve_client_ip(make_request(raw_peer), make_settings())
    except ClientIpResolutionError as exc:
        logger = logging.getLogger("tests.request_client_ip")
        with caplog.at_level(logging.ERROR):
            logger.exception("resolution failed")
        assert raw_peer not in f"{exc!s} {exc!r} {caplog.text}"
    else:
        pytest.fail("malformed peer was accepted")
