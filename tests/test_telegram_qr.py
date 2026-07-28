import inspect

import pytest

import app.telegram.qr as telegram_qr
from app.telegram.qr import TelegramQrRenderError, render_telegram_start_link_qr_png

FAKE_START_LINK = "https://t.me/nasiya_linkbot?start=fake_test_token_123"


def test_qr_generator_returns_bounded_in_memory_png() -> None:
    first = render_telegram_start_link_qr_png(FAKE_START_LINK)
    second = render_telegram_start_link_qr_png(FAKE_START_LINK)

    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    assert first == second
    assert len(first) < 8_192
    assert FAKE_START_LINK.encode() not in first


def test_qr_generator_passes_exact_link_and_approved_values_to_encoder() -> None:
    captured: dict[str, object] = {}

    class FakeQrCode:
        def save(self, output, **kwargs: object) -> None:
            captured["save"] = kwargs
            output.write(b"\x89PNG\r\n\x1a\nfake-png")

    def fake_encoder(value: str, **kwargs: object) -> FakeQrCode:
        captured["value"] = value
        captured["encoder"] = kwargs
        return FakeQrCode()

    result = render_telegram_start_link_qr_png(
        FAKE_START_LINK,
        encoder=fake_encoder,
    )

    assert result == b"\x89PNG\r\n\x1a\nfake-png"
    assert captured == {
        "value": FAKE_START_LINK,
        "encoder": {"error": "M", "boost_error": False},
        "save": {
            "kind": "png",
            "scale": 5,
            "border": 4,
            "dark": "#111827",
            "light": "#ffffff",
        },
    }


@pytest.mark.parametrize("failure_mode", ["encoder", "save", "invalid_png"])
def test_qr_generator_maps_failures_without_link_leakage(
    failure_mode: str,
) -> None:
    class FakeQrCode:
        def save(self, output, **kwargs: object) -> None:
            _ = kwargs
            if failure_mode == "save":
                raise RuntimeError(FAKE_START_LINK)
            output.write(b"not-png")

    def fake_encoder(value: str, **kwargs: object) -> FakeQrCode:
        _ = value, kwargs
        if failure_mode == "encoder":
            raise RuntimeError(FAKE_START_LINK)
        return FakeQrCode()

    with pytest.raises(TelegramQrRenderError) as captured:
        render_telegram_start_link_qr_png(
            FAKE_START_LINK,
            encoder=fake_encoder,
        )

    assert FAKE_START_LINK not in str(captured.value)
    assert FAKE_START_LINK not in repr(captured.value)


def test_qr_helper_has_no_file_cache_network_database_or_logging_path() -> None:
    source = inspect.getsource(telegram_qr)

    for forbidden in (
        "tempfile",
        "open(",
        "Path(",
        "requests",
        "httpx",
        "Session",
        "logger",
        "logging",
    ):
        assert forbidden not in source
