import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.deps import CsrfFailed, csrf_failed_exception_handler
from app.auth.router import router as auth_router
from app.db import (
    create_database_engine,
    create_database_session_dependency,
    create_database_session_factory,
)
from app.security_headers import install_security_headers_middleware
from app.settings import Settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
STATIC_DIR = Path(__file__).resolve().parent / "static"
ENV_FILE = Path(".env")
SETTINGS_ENV_KEYS = frozenset(
    {
        "DATABASE_URL",
        "SESSION_COOKIE_SECURE",
        "RATE_LIMIT_HMAC_KEY",
    }
)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_default_settings()

    application = FastAPI(title="Nasiya")
    application.add_exception_handler(CsrfFailed, csrf_failed_exception_handler)
    install_security_headers_middleware(application, app_settings)
    application.state.settings = app_settings
    database_engine = create_database_engine(app_settings)
    database_session_factory = create_database_session_factory(database_engine)
    application.state.database_engine = database_engine
    application.state.database_session_factory = database_session_factory
    application.state.get_database_session = create_database_session_dependency(
        database_session_factory
    )
    application.include_router(auth_router)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "home.html")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


def load_default_settings() -> Settings:
    if ENV_FILE.exists() or any(key in os.environ for key in SETTINGS_ENV_KEYS):
        return Settings(_env_file=ENV_FILE)

    return Settings(
        _env_file=None,
        app_environment="development",
        debug=False,
        database_url="postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_dev",
        session_cookie_secure=False,
        rate_limit_hmac_key="development-only-rate-limit-hmac-key-change-me",
    )


app = create_app()
