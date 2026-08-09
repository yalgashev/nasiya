import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.deps import CsrfFailed, csrf_failed_exception_handler
from app.auth.router import router as auth_router
from app.customer.router import router as customer_router
from app.customer_activation.router import router as customer_activation_router
from app.customer_identity.router import router as customer_identity_router
from app.db import (
    create_database_engine,
    create_database_session_dependency,
    create_database_session_factory,
)
from app.debt.repository import locked_customer_global_hard_block_reader_factory
from app.debt.router import router as debt_router
from app.offers.router import router as offers_router
from app.payment.repository import payment_open_set_reader_factory
from app.payment.router import router as payment_router
from app.security_headers import install_security_headers_middleware
from app.settings import Settings
from app.shop.router import router as shop_router
from app.shop_customer.router import router as shop_customer_router
from app.storage.body_guard import StorageBodyLimitMiddleware
from app.storage.contracts import ObjectStorageService

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


def create_app(
    settings: Settings | None = None,
    *,
    customer_document_storage_service: ObjectStorageService | None = None,
) -> FastAPI:
    app_settings = settings or load_default_settings()

    application = FastAPI(title="Nasiya")
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={"/customer/identity/document"},
        max_body_bytes=app_settings.object_storage_max_multipart_bytes,
    )
    application.add_exception_handler(CsrfFailed, csrf_failed_exception_handler)
    install_security_headers_middleware(application, app_settings)
    application.state.settings = app_settings
    database_engine = create_database_engine(app_settings)
    database_session_factory = create_database_session_factory(database_engine)
    application.state.database_engine = database_engine
    application.state.database_session_factory = database_session_factory
    application.state.customer_document_storage_service = (
        customer_document_storage_service
    )
    application.state.debt_open_set_reader_factory = payment_open_set_reader_factory
    application.state.debt_hard_block_reader_factory = (
        locked_customer_global_hard_block_reader_factory
    )
    application.state.get_database_session = create_database_session_dependency(
        database_session_factory
    )
    application.include_router(auth_router)
    application.include_router(offers_router)
    application.include_router(customer_router)
    application.include_router(customer_activation_router)
    application.include_router(customer_identity_router)
    application.include_router(shop_router)
    application.include_router(shop_customer_router)
    application.include_router(debt_router)
    application.include_router(payment_router)
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
