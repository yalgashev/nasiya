from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.debt.models import Debt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M14_REVISION = "a5b6c7d8e9f0"
M15_REVISION = "b6c7d8e9f0a1"
M16_REVISION = "c7d8e9f0a1b2"
M17_REVISION = "d8e9f0a1b2c3"
MIGRATION_PATH = (
    PROJECT_ROOT / "alembic/versions/b6c7d8e9f0a1_add_overdue_persistence.py"
)
M16_MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic/versions/c7d8e9f0a1b2_add_rating_and_disclosure_persistence.py"
)


def _migration_module():
    spec = spec_from_file_location("m15_overdue_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m15_is_the_single_linear_child_of_exact_m14() -> None:
    scripts = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    revision = scripts.get_revision(M15_REVISION)

    assert scripts.get_heads() == [M17_REVISION]
    assert revision is not None
    assert revision.down_revision == M14_REVISION


def test_m15_migration_payload_remains_exact_m16_downgrade_authority() -> None:
    migration = _migration_module()
    spec = spec_from_file_location("m16_rating_migration", M16_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    m16 = module_from_spec(spec)
    spec.loader.exec_module(m16)

    assert migration._audit_payload_sql(include_m15=True) == (
        m16._audit_payload_sql(include_m16=False)
    )


def test_m15_debt_metadata_has_exact_columns_checks_and_candidate_index() -> None:
    columns = Debt.__table__.columns
    checks = {
        constraint.name
        for constraint in Debt.__table__.constraints
        if constraint.name is not None
    }
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Debt.__table__.indexes
    }

    assert columns["overdue_at"].nullable is True
    assert columns["overdue_at"].default is None
    assert columns["overdue_at"].server_default is None
    assert columns["overdue_revision"].nullable is True
    assert columns["overdue_revision"].default is None
    assert columns["overdue_revision"].server_default is None
    assert {
        "ck_debts_overdue_metadata_pair",
        "ck_debts_overdue_revision_positive",
        "ck_debts_overdue_revision_not_after_revision",
    } <= checks
    assert indexes["ix_debts_status_due_date_id"] == ("status", "due_date", "id")


def test_migration_is_no_rewrite_and_does_not_touch_payment_or_idempotency_schema() -> (
    None
):
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    upgrade_body = source.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]

    assert "UPDATE " not in source.upper()
    assert '"payments"' not in upgrade_body
    assert '"idempotency_keys"' not in upgrade_body
    assert "create_table" not in upgrade_body
    assert source.count("op.add_column(") == 2
    assert source.count("op.create_index(") == 1


def test_all_m15_downgrade_guards_run_before_ddl() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    guard_body = source.split("def downgrade() -> None:", 1)[1].split(
        "_replace_audit_checks(include_m15=False)", 1
    )[0]

    for predicate in (
        "status = 'overdue' OR overdue_at IS NOT NULL OR overdue_revision IS NOT NULL",
        "event_type IN ('debt.overdue','debt.clawback_applied')",
        "payload ->> 'from_status' = 'overdue'",
        "debt row is not M14-compatible",
    ):
        assert predicate in guard_body
    assert "op.drop_" not in guard_body
    assert "op.create_" not in guard_body


def test_ci_verifies_exact_m15_head() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "Verify Alembic M17 head" in workflow
    assert f'test "$current_revision" = "{M17_REVISION}"' in workflow
