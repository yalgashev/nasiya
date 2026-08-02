import ast
import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

import app.audit.models  # noqa: F401
import app.auth.models  # noqa: F401
import app.customer.models  # noqa: F401
import app.customer_document.models  # noqa: F401
import app.customer_identity.models  # noqa: F401
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
    "app/telegram/worker.py": (
        "ebe1a1dbceb3a428437fed8d5760082b0b140f06a98860b2db0262633c4e49f5"
    ),
    "app/otp/dispatcher.py": (
        "8cbc524e325d569520bb7feca595d4e870988407a47abd6807b5167af78cf599"
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
M8_M9_AND_M10_AUTHORIZED_TABLES = {
    "audit_log",
    "object_files",
    "offer_acceptances",
    "offer_texts",
    "offer_versions",
    "customer_identities",
    "customer_documents",
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


def test_m8_m9_and_m10_tables_are_exactly_scoped_in_metadata() -> None:
    all_tables = set(Base.metadata.tables)

    assert all_tables - PRE_M8_TABLES == M8_M9_AND_M10_AUTHORIZED_TABLES
    assert PRE_M8_TABLES <= all_tables
    assert len(all_tables) == len(PRE_M8_TABLES) + len(M8_M9_AND_M10_AUTHORIZED_TABLES)


def test_production_runtime_has_only_the_concrete_m10_document_file_route() -> None:
    application = create_app(settings=_settings())
    paths = {path.casefold() for path in application.openapi()["paths"]}

    assert paths
    m10_document_path = "/customer/identity/document"
    assert m10_document_path in paths
    for path in paths - {m10_document_path}:
        path_segments = set(path.strip("/").split("/"))
        assert not path_segments.intersection(FORBIDDEN_FILE_ROUTE_PARTS)

    template_source = "\n".join(
        path.read_text(encoding="utf-8").casefold()
        for path in sorted((PROJECT_ROOT / "app/templates").rglob("*"))
        if path.is_file()
    )
    assert template_source.count('type="file"') == 1
    assert template_source.count("multipart/form-data") == 1
    assert 'action="/customer/identity/document"' in template_source
    assert "presigned put" not in template_source

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


def test_m5_m6_m7_roles_workers_tt_are_immutable_and_main_is_narrowly_extended() -> (
    None
):
    actual_hashes = {
        relative_path: _sha256(PROJECT_ROOT / relative_path)
        for relative_path in PRE_M8_IMMUTABLE_SHA256
    }

    assert actual_hashes == PRE_M8_IMMUTABLE_SHA256
    main_source = (PROJECT_ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "customer_identity_router" in main_source
    assert "customer_activation_router" in main_source
    assert "StorageBodyLimitMiddleware" in main_source
    assert 'protected_paths={"/customer/identity/document"}' in main_source
    for forbidden in ("presigned_put", "scheduler", "shop_customer", "OCR"):
        assert forbidden not in main_source
    assert (PROJECT_ROOT / "tests/test_shop_containment_guard.py").is_file()
    assert (PROJECT_ROOT / "tests/test_shop_service_isolation.py").is_file()
    assert (PROJECT_ROOT / "tests/test_telegram_scope_regression.py").is_file()
    assert (PROJECT_ROOT / "tests/test_otp_concurrency_containment_matrix.py").is_file()
