from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.settings import Settings

CONTENT_SECURITY_POLICY: Final = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)
STRICT_TRANSPORT_SECURITY: Final = "max-age=31536000"
AUTH_NO_STORE_CACHE_CONTROL: Final = "no-store"
SECURITY_HEADERS: Final[Mapping[str, str]] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def install_security_headers_middleware(
    application: FastAPI,
    settings: Settings,
) -> None:
    @application.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> Response:
        _ = request, exc
        response = PlainTextResponse("Internal Server Error", status_code=500)
        set_security_headers(response, settings)
        return response

    @application.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        set_security_headers(response, settings)
        return response


def set_security_headers(response: Response, settings: Settings) -> None:
    for header_name, header_value in SECURITY_HEADERS.items():
        response.headers[header_name] = header_value

    if _is_production(settings):
        response.headers["Strict-Transport-Security"] = STRICT_TRANSPORT_SECURITY


def mark_auth_response_no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = AUTH_NO_STORE_CACHE_CONTROL
    return response


def _is_production(settings: Settings) -> bool:
    return settings.app_environment.strip().casefold() == "production"
