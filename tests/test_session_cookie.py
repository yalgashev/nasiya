import logging

from starlette.responses import Response

from app.auth.cookies import delete_session_cookie, set_session_cookie
from app.auth.sessions import create_session_token
from app.settings import Settings

TEST_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-session-cookie"


def make_settings(
    *,
    app_environment: str = "development",
    session_cookie_secure: bool = False,
    session_ttl_days: int = 30,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment=app_environment,
        debug=False,
        database_url=TEST_DATABASE_URL,
        session_cookie_secure=session_cookie_secure,
        session_ttl_days=session_ttl_days,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def get_set_cookie_header(response: Response) -> str:
    return response.headers["set-cookie"]


def test_set_session_cookie_uses_default_name_and_http_only() -> None:
    response = Response()
    raw_token = create_session_token()

    set_session_cookie(response, raw_token, make_settings())

    header = get_set_cookie_header(response)
    assert header.startswith(f"nasiya_session={raw_token.as_cookie_value()};")
    assert "HttpOnly" in header
    assert "Path=/" in header
    assert "Domain=" not in header


def test_set_session_cookie_uses_samesite_lax() -> None:
    response = Response()

    set_session_cookie(response, create_session_token(), make_settings())

    assert "SameSite=lax" in get_set_cookie_header(response)


def test_set_session_cookie_omits_secure_in_development() -> None:
    response = Response()

    set_session_cookie(response, create_session_token(), make_settings())

    assert "secure" not in get_set_cookie_header(response).casefold()


def test_set_session_cookie_adds_secure_for_production_like_settings() -> None:
    response = Response()
    settings = make_settings(
        app_environment="production",
        session_cookie_secure=True,
    )

    set_session_cookie(response, create_session_token(), settings)

    assert "Secure" in get_set_cookie_header(response)


def test_set_session_cookie_max_age_matches_authenticated_ttl() -> None:
    response = Response()

    set_session_cookie(
        response,
        create_session_token(),
        make_settings(session_ttl_days=2),
    )

    assert "Max-Age=172800" in get_set_cookie_header(response)


def test_delete_session_cookie_expires_same_name_and_path() -> None:
    response = Response()

    delete_session_cookie(response, make_settings())

    header = get_set_cookie_header(response)
    assert header.startswith("nasiya_session=")
    assert "Path=/" in header
    assert "Max-Age=0" in header
    assert "expires=" in header.casefold()
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Domain=" not in header


def test_session_cookie_helper_does_not_log_raw_token(caplog) -> None:
    response = Response()
    raw_token = create_session_token()
    logger = logging.getLogger("tests.session_cookie")

    with caplog.at_level(logging.INFO):
        set_session_cookie(response, raw_token, make_settings())
        logger.info("session cookie set")

    assert raw_token.as_cookie_value() not in caplog.text
