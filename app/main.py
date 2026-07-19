from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.deps import CsrfFailed, csrf_failed_exception_handler
from app.db import (
    create_database_engine,
    create_database_session_dependency,
    create_database_session_factory,
)
from app.settings import Settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings(
        _env_file=".env",
        app_environment="development",
        debug=False,
        database_url="postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_dev",
        session_cookie_secure=False,
        rate_limit_hmac_key="development-only-rate-limit-hmac-key-change-me",
    )

    application = FastAPI(title="Nasiya")
    application.add_exception_handler(CsrfFailed, csrf_failed_exception_handler)
    application.state.settings = app_settings
    database_engine = create_database_engine(app_settings)
    database_session_factory = create_database_session_factory(database_engine)
    application.state.database_engine = database_engine
    application.state.database_session_factory = database_session_factory
    application.state.get_database_session = create_database_session_dependency(
        database_session_factory
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "home.html")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
