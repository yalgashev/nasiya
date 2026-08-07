import ast
import hashlib
import re
import tomllib
from pathlib import Path

from app.audit.contracts import AuditEventType, AuditObjectType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M10_APP_DIRS = (
    PROJECT_ROOT / "app" / "customer_identity",
    PROJECT_ROOT / "app" / "customer_document",
)
M10_MIGRATION = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "b0c1d2e3f4a5_create_customer_identity_foundation.py"
)
TT_BLOB = "d77c0f0f330a1330155a4aee3c46b05d97cf5561"
FREEZE_SHA256 = "de766bc75752cd80f64e49081b5764a0bc7b3b2112366f1a5d11818a7ab3a462"


def _m10_python_paths() -> tuple[Path, ...]:
    return tuple(
        sorted(path for directory in M10_APP_DIRS for path in directory.glob("*.py"))
    )


def _m10_source() -> str:
    paths = (*_m10_python_paths(), M10_MIGRATION)
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _production_dependencies() -> set[str]:
    pyproject = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return {
        re.split(r"[\[<>=~! ]", dependency, maxsplit=1)[0].casefold()
        for dependency in pyproject["project"]["dependencies"]
    }


def test_m10_has_no_public_registration_or_registration_otp_surface() -> None:
    router = (PROJECT_ROOT / "app" / "customer_identity" / "router.py").read_text(
        encoding="utf-8"
    )
    assert 'APIRouter(prefix="/customer/identity")' in router
    assert "/registration" not in router
    assert "REGISTRATION_OTP" not in _m10_source()


def test_m10_never_transitions_customer_from_draft_to_active() -> None:
    source = _m10_source()
    assert "create_customer_draft_if_missing" not in source
    assert "onboarding_status =" not in source
    assert "CUSTOMER_ONBOARDING_STATUS_ACTIVE" not in source


def test_m10_adds_no_shop_customer_or_customer_lead_surface() -> None:
    migration = M10_MIGRATION.read_text(encoding="utf-8")
    m10_module_names = {path.stem for path in _m10_python_paths()}
    assert "shop_customer" not in m10_module_names
    assert "customer_lead" not in m10_module_names
    assert '"shop_customers"' not in migration
    assert '"customer_leads"' not in migration


def test_m10_adds_no_ocr_mrz_selfie_biometric_or_registry_integration() -> None:
    source = _m10_source().casefold()
    dependencies = _production_dependencies()
    assert dependencies.isdisjoint(
        {"easyocr", "paddleocr", "pytesseract", "tesseract", "face-recognition"}
    )
    for forbidden in ("easyocr", "paddleocr", "pytesseract", "mrz", "biometric"):
        assert forbidden not in source
    assert "selfie" not in source
    assert "government_registry" not in source


def test_m10_adds_no_generic_attachment_cms_kms_or_full_pii_admin_platform() -> None:
    migration = M10_MIGRATION.read_text(encoding="utf-8")
    assert migration.count("op.create_table(") == 2
    assert '"customer_identities"' in migration
    assert '"customer_documents"' in migration
    for forbidden_table in ("attachments", "cms", "kms_keys", "pii_admin"):
        assert f'"{forbidden_table}"' not in migration
    router = (PROJECT_ROOT / "app" / "customer_identity" / "router.py").read_text(
        encoding="utf-8"
    )
    assert "/admin" not in router


def test_m10_adds_no_debt_payment_rating_disclosure_notification_or_scheduler() -> None:
    m10_module_names = {
        path.stem for directory in M10_APP_DIRS for path in directory.glob("*.py")
    }
    assert m10_module_names.isdisjoint(
        {"debt", "payment", "rating", "disclosure", "notification", "scheduler"}
    )
    assert _production_dependencies().isdisjoint(
        {"apscheduler", "celery", "dramatiq", "huey", "rq"}
    )


def test_m10_adds_no_storage_delete_api_route_or_scheduler() -> None:
    router = (PROJECT_ROOT / "app" / "customer_identity" / "router.py").read_text(
        encoding="utf-8"
    )
    assert "@router.delete" not in router
    assert "delete_object" not in router
    assert "reconcile_stale_object_deletes" not in router
    assert "scheduler" not in router.casefold()


def test_m10_imports_no_private_storage_delete_symbols() -> None:
    for path in _m10_python_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.ImportFrom)
                or node.module != "app.storage.service"
            ):
                continue
            assert all(not alias.name.startswith("_delete") for alias in node.names)
    source = _m10_source()
    assert "_delete_object_target" not in source
    assert "_delete_prepared_object" not in source


def test_m10_defines_no_new_storage_rate_limit_scope_or_counter() -> None:
    coordinator = (
        PROJECT_ROOT / "app" / "customer_document" / "coordinator.py"
    ).read_text(encoding="utf-8")
    assert "check_storage_upload_rate_limit" in coordinator
    assert "ingest_sanitized_image" in coordinator
    assert "record_storage_upload_attempt" not in coordinator
    assert "AuthRateLimit(" not in _m10_source()


def test_cr_m10_01_reuses_m8_without_redesigning_storage() -> None:
    coordinator = (
        PROJECT_ROOT / "app" / "customer_document" / "coordinator.py"
    ).read_text(encoding="utf-8")
    assert "check_storage_upload_rate_limit" in coordinator
    assert "ingest_sanitized_image" in coordinator
    assert "claim_unattached_object_for_compensation" in coordinator
    for forbidden in (
        "presigned_put",
        "put_object(",
        "head_object(",
        "delete_object(",
        "reconcile_stale_object_deletes",
    ):
        assert forbidden not in coordinator


def test_m10_tests_forbid_sqlite_and_create_all_fallbacks() -> None:
    paths = (
        *M10_APP_DIRS,
        PROJECT_ROOT / "tests",
    )
    selected = tuple(
        path
        for directory in paths
        for path in directory.glob("test_m10_*.py")
        if path.name != Path(__file__).name
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in selected)
    assert "sqlite" not in source.casefold()
    assert "create_all(" not in source


def test_m1_m9_contracts_and_exact_audit_extension_remain_contained() -> None:
    m10_events = {
        AuditEventType.CUSTOMER_IDENTITY_SAVED,
        AuditEventType.CUSTOMER_DOCUMENT_ATTACHED,
        AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED,
        AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED,
    }
    m10_objects = {
        AuditObjectType.CUSTOMER_IDENTITY,
        AuditObjectType.CUSTOMER_DOCUMENT,
    }
    assert {event.value for event in m10_events} == {
        "customer.identity_saved",
        "customer.document_attached",
        "customer.document_superseded",
        "customer.document_access_granted",
    }
    assert {object_type.value for object_type in m10_objects} == {
        "customer_identity",
        "customer_document",
    }


def test_tt_and_final_scope_freeze_hashes_match_m10_baseline() -> None:
    tt = (PROJECT_ROOT / "docs" / "tt_nasiya_web_v1.md").read_bytes()
    tt_header = f"blob {len(tt)}\0".encode("ascii")
    assert hashlib.sha1(tt_header + tt, usedforsecurity=False).hexdigest() == TT_BLOB
    scope_contract = (PROJECT_ROOT / "docs" / "m10_scope_contract.md").read_text(
        encoding="utf-8"
    )
    assert FREEZE_SHA256 in scope_contract
