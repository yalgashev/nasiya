from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from app.audit.models import AuditLog
from app.debt.models import Debt
from app.idempotency.models import IdempotencyKey
from app.rating.models import RatingEvent

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d8e9f0a1b2c3"
PARENT = "c7d8e9f0a1b2"
MIGRATION = ROOT / "alembic/versions/d8e9f0a1b2c3_add_written_off_debt_persistence.py"


def _module():
    spec = spec_from_file_location("m17_persistence_revision", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks(table) -> set[str]:
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }


def test_m17_is_exact_single_linear_schema_only_child() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    child = scripts.get_revision(REVISION)
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade() -> None:", 1)[1].split(
        "def _guard_m17_downgrade_loss", 1
    )[0]
    assert scripts.get_heads() == [REVISION]
    assert child is not None and child.down_revision == PARENT
    assert source.count("op.add_column(") == 6
    assert "op.create_table(" not in source
    assert "UPDATE " not in source.upper()
    assert "DELETE FROM" not in source.upper()
    assert upgrade.index("LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE") < (
        upgrade.index("op.add_column")
    )


def test_m17_debt_metadata_has_exact_columns_fk_checks_and_queue_index() -> None:
    columns = Debt.__table__.columns
    assert tuple(columns.keys())[-8:-2] == (
        "written_off_at",
        "written_off_revision",
        "written_off_reason",
        "written_off_actor_user_id",
        "written_off_settled_at",
        "written_off_settled_revision",
    )
    assert all(columns[name].nullable for name in tuple(columns.keys())[-8:-2])
    actor_fks = {
        constraint.name: constraint
        for constraint in Debt.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    actor_fk = actor_fks["fk_debts_written_off_actor_user_id_users_id"]
    assert actor_fk.ondelete == "RESTRICT"
    assert tuple(element.target_fullname for element in actor_fk.elements) == (
        "users.id",
    )
    assert {
        "ck_debts_written_off_metadata_complete",
        "ck_debts_written_off_reason_allowed",
        "ck_debts_written_off_revision_positive",
        "ck_debts_written_off_revision_not_after_revision",
        "ck_debts_written_off_settled_metadata_pair",
        "ck_debts_written_off_settled_revision_positive",
        "ck_debts_written_off_settled_revision_not_after_revision",
        "ck_debts_written_off_revision_chain",
        "ck_debts_written_off_settled_revision_chain",
    } <= _checks(Debt.__table__)
    index = next(
        index
        for index in Debt.__table__.indexes
        if index.name == "ix_debts_status_overdue_at_id"
    )
    assert tuple(column.name for column in index.columns) == (
        "status",
        "overdue_at",
        "id",
    )
    assert index.unique is False


def test_m17_registry_metadata_matches_closed_schema_contracts() -> None:
    assert {
        "ck_rating_events_event_type_allowed",
        "ck_rating_events_delta_matches_event",
        "ck_rating_events_recording_source_allowed",
    } <= _checks(RatingEvent.__table__)
    assert "ck_audit_log_payload_exact_shape" in _checks(AuditLog.__table__)
    assert "ck_idempotency_keys_endpoint_result_pair_allowed" in _checks(
        IdempotencyKey.__table__
    )
    rating_sql = " ".join(
        str(constraint.sqltext)
        for constraint in RatingEvent.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    )
    assert "written_off" in rating_sql and "-40" in rating_sql
    assert "written_off_settled" in rating_sql and "10" in rating_sql
    assert "historical_reconciliation" in rating_sql


def test_all_m17_downgrade_guards_precede_ddl_and_no_rows_are_rewritten() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    assert downgrade.index("_guard_m17_downgrade_loss()") < downgrade.index(
        "_replace_audit_checks"
    )
    guard = source.split("def _guard_m17_downgrade_loss()", 1)[1].split(
        "def downgrade()", 1
    )[0]
    for value in (
        "written_off_settled",
        "debt.written_off",
        "admin.debts.write_off",
        "written-off Debt state exists",
    ):
        assert value in guard
    assert "op.drop_" not in guard
    assert "op.create_" not in guard
    module = _module()
    assert module.revision == REVISION
    assert module.down_revision == PARENT


def test_ci_hardcodes_exact_m17_head() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Verify Alembic M17 head" in workflow
    assert f'test "$current_revision" = "{REVISION}"' in workflow
