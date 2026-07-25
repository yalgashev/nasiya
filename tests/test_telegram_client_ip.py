import inspect
import logging

import pytest

import app.telegram.client_ip as client_ip_module
from app.telegram.client_ip import ResolvedClientIp


@pytest.mark.parametrize(
    ("raw_value", "canonical_value"),
    [
        ("203.0.113.10", "203.0.113.10"),
        ("2001:0DB8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ("2001:db8::ABCD", "2001:db8::abcd"),
        ("::ffff:203.0.113.10", "203.0.113.10"),
        ("::ffff:cb00:710a", "203.0.113.10"),
    ],
)
def test_resolved_client_ip_accepts_and_canonicalizes_literals(
    raw_value: str,
    canonical_value: str,
) -> None:
    resolved_ip = ResolvedClientIp(raw_value)

    assert resolved_ip.as_hmac_input() == canonical_value


@pytest.mark.parametrize(
    "raw_value",
    [
        "",
        " ",
        "example.com",
        "203.0.113.10, 198.51.100.20",
        "203.0.113.10:443",
        "[2001:db8::1]",
        "[2001:db8::1]:443",
        "999.0.113.10",
        "203.0.113",
        "2001:db8:::1",
        " 203.0.113.10",
        "203.0.113.10 ",
    ],
)
def test_resolved_client_ip_rejects_non_literal_or_header_values(
    raw_value: str,
) -> None:
    with pytest.raises(ValueError):
        ResolvedClientIp(raw_value)


def test_resolved_client_ip_rejects_non_string_values() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        ResolvedClientIp(123)  # type: ignore[arg-type]


def test_resolved_client_ip_repr_str_and_logging_are_redacted(caplog) -> None:
    resolved_ip = ResolvedClientIp("203.0.113.10")
    canonical_value = resolved_ip.as_hmac_input()
    logger = logging.getLogger("tests.telegram_client_ip")

    with caplog.at_level(logging.INFO):
        logger.info("resolved client IP %s %r", resolved_ip, resolved_ip)

    assert canonical_value not in str(resolved_ip)
    assert canonical_value not in repr(resolved_ip)
    assert canonical_value not in caplog.text
    assert "redacted" in caplog.text


def test_resolved_client_ip_exposes_only_hmac_input_accessor() -> None:
    resolved_ip = ResolvedClientIp("203.0.113.10")

    assert hasattr(resolved_ip, "as_hmac_input")
    assert not hasattr(resolved_ip, "as_header_value")
    assert not hasattr(resolved_ip, "as_request_host")
    assert not hasattr(resolved_ip, "value")


def test_resolved_client_ip_validation_errors_do_not_echo_raw_value() -> None:
    raw_value = "203.0.113.10, 198.51.100.20"

    with pytest.raises(ValueError) as exc_info:
        ResolvedClientIp(raw_value)

    assert raw_value not in str(exc_info.value)
    assert "Resolved client IP" in str(exc_info.value)


def test_resolved_client_ip_primitive_does_not_parse_http_or_proxy_headers() -> None:
    source = inspect.getsource(client_ip_module)
    forbidden_source_terms = {
        "Request",
        "headers",
        "x-forwarded-for",
        "forwarded",
        "proxy",
        "CLIENT_IP_MODE",
        "TRUSTED_PROXY_CIDRS",
        "Settings",
        "os.environ",
        "getenv",
    }

    for forbidden_source_term in forbidden_source_terms:
        assert forbidden_source_term not in source
