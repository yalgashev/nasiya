from typing import Final

from starlette.responses import Response

from app.auth.sessions import RawSessionToken
from app.settings import Settings

SESSION_COOKIE_PATH: Final = "/"
SESSION_COOKIE_SAMESITE: Final = "lax"
SECONDS_PER_DAY: Final = 24 * 60 * 60


def set_session_cookie(
    response: Response,
    raw_token: RawSessionToken,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token.as_cookie_value(),
        max_age=settings.session_ttl_days * SECONDS_PER_DAY,
        path=SESSION_COOKIE_PATH,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
    )


def delete_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=SESSION_COOKIE_PATH,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
    )
