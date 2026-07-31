import ast
import hashlib
import re
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.audit.models  # noqa: F401
import app.auth.models  # noqa: F401
import app.customer.models  # noqa: F401
import app.offers.models  # noqa: F401
import app.otp.models  # noqa: F401
import app.shop.models  # noqa: F401
import app.storage.models  # noqa: F401
import app.telegram.models  # noqa: F401
from app.db import Base
from app.main import create_app
from app.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRE_M8_IMMUTABLE_SHA256 = {
    "docs/tt_nasiya_web_v1.md": (
        "569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab"
    ),
    "app/main.py": ("1b8bd183d0a9017c5da30764c3b8e47be58415fd65c7edadc76b5801f29637c6"),
    "app/telegram/worker.py": (
        "ebe1a1dbceb3a428437fed8d5760082b0b140f06a98860b2db0262633c4e49f5"
    ),
    "app/otp/dispatcher.py": (
        "5d8c372c6a5b1c5eefdd0b40e1b43eabb5acbb217eabfd7e895f62493822eb1e"
    ),
}
PRE_M8_TABLES = {
    "auth_rate_limits",
    "customers",
    "otp_challenge_events",
    "otp_challenges",
    "otp_dispatcher_state",
    "otp_dispatches",
    "sessions",
    "shop_staff",
    "shop_staff_events",
    "shop_status_events",
    "shops",
    "telegram_link_events",
    "telegram_link_tokens",
    "telegram_links",
    "telegram_polling_state",
    "telegram_update_failures",
    "users",
}
M8_AND_M9_AUTHORIZED_TABLES = {
    "audit_log",
    "object_files",
    "offer_acceptances",
    "offer_texts",
    "offer_versions",
}
FORBIDDEN_STORAGE_IMPORT_PREFIXES = (
    "app.customer",
    "app.news",
    "app.otp",
    "app.shop",
)
FORBIDDEN_STORAGE_SYMBOL_PARTS = (
    "antivirus",
    "attachment",
    "cdn",
    "customer_document",
    "generic_audit",
    "media_vault",
    "ocr",
    "offer",
    "outbox",
    "owner_application",
    "presigned_put",
    "scheduler",
)
FORBIDDEN_FILE_ROUTE_PARTS = (
    "attachment",
    "document",
    "download",
    "file",
    "media",
    "storage",
    "upload",
    "vault",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=("postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"),
        session_cookie_secure=False,
        rate_limit_hmac_key="test-rate-limit-hmac-key-for-m8-containment",
    )


def test_m8_storage_and_m9_offer_tables_are_exactly_scoped_in_metadata() -> None:
    all_tables = set(Base.metadata.tables)

    assert all_tables - PRE_M8_TABLES == M8_AND_M9_AUTHORIZED_TABLES
    assert PRE_M8_TABLES <= all_tables
    assert len(all_tables) == len(PRE_M8_TABLES) + len(M8_AND_M9_AUTHORIZED_TABLES)


def test_production_runtime_has_no_file_route_or_upload_template() -> None:
    application = create_app(settings=_settings())
    api_routes = [route for route in application.routes if isinstance(route, APIRoute)]
    paths = {route.path_format.casefold() for route in api_routes}

    assert paths
    for path in paths:
        assert not any(part in path for part in FORBIDDEN_FILE_ROUTE_PARTS)

    template_source = "\n".join(
        path.read_text(encoding="utf-8").casefold()
        for path in sorted((PROJECT_ROOT / "app/templates").rglob("*"))
        if path.is_file()
    )
    assert 'type="file"' not in template_source
    assert "multipart/form-data" not in template_source

    response = TestClient(application).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_storage_package_has_narrow_dependencies_and_no_generic_domain() -> None:
    storage_paths = sorted((PROJECT_ROOT / "app/storage").glob("*.py"))
    combined_source = "\n".join(
        path.read_text(encoding="utf-8").casefold() for path in storage_paths
    )
    source_tokens = set(re.findall(r"[a-z0-9]+", combined_source))
    imported_modules: set[str] = set()
    class_names: set[str] = set()

    for path in storage_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)
            elif isinstance(node, ast.ClassDef):
                class_names.add(node.name.casefold())

    assert not any(
        module.startswith(FORBIDDEN_STORAGE_IMPORT_PREFIXES)
        for module in imported_modules
    )
    for marker in FORBIDDEN_STORAGE_SYMBOL_PARTS:
        if "_" in marker:
            assert marker not in combined_source
        else:
            assert marker not in source_tokens
    assert not any(
        "attachment" in class_name or "scheduler" in class_name or "audit" in class_name
        for class_name in class_names
    )
    assert "create_presigned_put_url" not in combined_source
    assert "create_presigned_post" not in combined_source
    assert "public-read" not in combined_source


def test_m5_m6_m7_roles_main_composition_and_tt_are_immutable() -> None:
    actual_hashes = {
        relative_path: _sha256(PROJECT_ROOT / relative_path)
        for relative_path in PRE_M8_IMMUTABLE_SHA256
    }

    assert actual_hashes == PRE_M8_IMMUTABLE_SHA256
    assert (PROJECT_ROOT / "tests/test_shop_containment_guard.py").is_file()
    assert (PROJECT_ROOT / "tests/test_shop_service_isolation.py").is_file()
    assert (PROJECT_ROOT / "tests/test_telegram_scope_regression.py").is_file()
    assert (PROJECT_ROOT / "tests/test_otp_concurrency_containment_matrix.py").is_file()
