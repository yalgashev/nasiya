import inspect
from pathlib import Path

from tests import postgresql
from tests.postgresql import M2_CLEANUP_TABLE_NAMES, get_alembic_head

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M8_REVISION = "f8a9b0c1d2e3"
M9_REVISION = "a9b0c1d2e3f4"
M10_REVISION = "b0c1d2e3f4a5"
M11_ORIGINAL_REVISION = "c1d2e3f4a5b6"
M11_RECOVERY_REVISION = "d2e3f4a5b6c7"
M11_CLEANUP_PREFIX = (
    "otp_challenge_events",
    "otp_dispatches",
    "otp_challenges",
    "customer_documents",
    "customer_identities",
    "audit_log",
    "offer_acceptances",
    "offer_texts",
    "offer_versions",
    "object_files",
)
INHERITED_CLEANUP_ORDER = (
    "otp_dispatcher_state",
    "telegram_update_failures",
    "telegram_polling_state",
    "telegram_link_events",
    "telegram_link_tokens",
    "telegram_links",
    "customers",
    "auth_rate_limits",
    "sessions",
    "shop_staff_events",
    "shop_status_events",
    "shop_staff",
    "shops",
    "users",
)


def test_code_and_ci_are_wired_to_exact_recovery_head() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert get_alembic_head() == M11_RECOVERY_REVISION
    assert "Verify Alembic M11 recovery head" in workflow
    assert f'test "$current_revision" = "{M11_RECOVERY_REVISION}"' in workflow
    assert f'test "$current_revision" = "{M11_ORIGINAL_REVISION}"' not in workflow
    assert f'test "$current_revision" = "{M10_REVISION}"' not in workflow
    assert f'test "$current_revision" = "{M9_REVISION}"' not in workflow
    assert f'test "$current_revision" = "{M8_REVISION}"' not in workflow
    assert 'test "$current_revision" = "e7f8a9b0c1d2"' not in workflow


def test_alembic_metadata_has_minimal_storage_model_import() -> None:
    env_source = (PROJECT_ROOT / "alembic/env.py").read_text(encoding="utf-8")

    assert (
        "from app.storage import models as _storage_models  # noqa: F401" in env_source
    )
    assert env_source.count("app.storage") == 1


def test_cleanup_keeps_m11_children_before_referenced_inherited_rows() -> None:
    assert M2_CLEANUP_TABLE_NAMES[:10] == M11_CLEANUP_PREFIX
    assert M2_CLEANUP_TABLE_NAMES[10:] == INHERITED_CLEANUP_ORDER
    assert M2_CLEANUP_TABLE_NAMES.index("telegram_link_tokens") < (
        M2_CLEANUP_TABLE_NAMES.index("telegram_links")
    )
    assert M2_CLEANUP_TABLE_NAMES.index("telegram_links") < (
        M2_CLEANUP_TABLE_NAMES.index("users")
    )
    assert len(M2_CLEANUP_TABLE_NAMES) == len(set(M2_CLEANUP_TABLE_NAMES))


def test_cleanup_uses_exact_allowlist_and_no_generic_schema_reset() -> None:
    source = inspect.getsource(postgresql)

    assert "DROP SCHEMA" not in source
    assert "DROP TABLE" not in source
    assert "TRUNCATE" not in source
    assert "create_all" not in source
    assert "DELETE FROM" in source
    assert "M2_CLEANUP_TABLE_NAMES" in source
