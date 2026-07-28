from collections.abc import Callable
from io import BytesIO
from typing import Final, Protocol

import segno

TELEGRAM_QR_ERROR_CORRECTION: Final = "M"
TELEGRAM_QR_SCALE: Final = 5
TELEGRAM_QR_BORDER: Final = 4
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"


class TelegramQrCode(Protocol):
    def save(self, out, **kwargs: object) -> None: ...


class TelegramQrRenderError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Telegram QR rendering failed")


def render_telegram_start_link_qr_png(
    start_link: str,
    *,
    encoder: Callable[..., TelegramQrCode] = segno.make_qr,
) -> bytes:
    try:
        qr_code = encoder(
            start_link,
            error=TELEGRAM_QR_ERROR_CORRECTION,
            boost_error=False,
        )
        output = BytesIO()
        qr_code.save(
            output,
            kind="png",
            scale=TELEGRAM_QR_SCALE,
            border=TELEGRAM_QR_BORDER,
            dark="#111827",
            light="#ffffff",
        )
        png_bytes = output.getvalue()
    except Exception:
        raise TelegramQrRenderError() from None

    if not png_bytes.startswith(_PNG_SIGNATURE):
        raise TelegramQrRenderError()
    return png_bytes
