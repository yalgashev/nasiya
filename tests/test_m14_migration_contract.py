from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.postgresql import M2_CLEANUP_TABLE_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M13_REVISION = "f4a5b6c7d8e"
M14_REVISION = "a5b6c7d8e9f0"
MIGRATION_PATH = (
    PROJECT_ROOT / "alembic/versions/a5b6c7d8e9f0_add_active_payment_persistence.py"
)


def _migration_module():
    spec = spec_from_file_location("m14_payment_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m14_is_the_single_linear_child_of_m13() -> None:
    scripts = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    revision = scripts.get_revision(M14_REVISION)

    assert scripts.get_heads() == ["d8e9f0a1b2c3"]
    assert revision is not None
    assert revision.down_revision == M13_REVISION


def test_migration_keeps_its_frozen_m14_audit_payload() -> None:
    migration = _migration_module()
    payload = migration._audit_payload_sql(include_m14=True)

    assert "payment.recorded" in payload
    assert "debt.paid" in payload
    assert "debt.overdue" not in payload
    assert "debt.clawback_applied" not in payload


def test_migration_freezes_m13_rollback_without_live_model_imports() -> None:
    migration = _migration_module()
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    m13_payload = migration._audit_payload_sql(include_m14=False)

    assert "from app.audit.models" not in source
    assert "payment.recorded" not in m13_payload
    assert "debt.paid" not in m13_payload
    for event_type in migration._M13_EVENTS:
        assert event_type in m13_payload or event_type == "platform_admin.bootstrapped"
    assert "ck_idempotency_keys_endpoint_allowed" in source
    assert "ck_idempotency_keys_result_object_type_allowed" in source


def test_cleanup_keeps_m14_payment_fk_order() -> None:
    assert M2_CLEANUP_TABLE_NAMES.index("payments") < (
        M2_CLEANUP_TABLE_NAMES.index("idempotency_keys")
    )
    assert M2_CLEANUP_TABLE_NAMES.index("payments") < (
        M2_CLEANUP_TABLE_NAMES.index("debts")
    )


def test_migration_contains_all_four_pre_ddl_downgrade_guards() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    guard_body = source.split("def downgrade()", 1)[1].split(
        "_replace_audit_checks(include_m14=False)", 1
    )[0]

    for predicate in (
        "SELECT EXISTS (SELECT 1 FROM payments)",
        "endpoint = 'shop.debt_payments.create' OR result_object_type = 'payment'",
        "status = 'paid' OR paid_at IS NOT NULL",
        "event_type IN ('payment.recorded','debt.paid')",
    ):
        assert predicate in guard_body
    assert "op.drop_" not in guard_body
    assert "op.create_" not in guard_body
