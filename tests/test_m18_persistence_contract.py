from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
REVISION = "e9f0a1b2c3d4"
PARENT = "d8e9f0a1b2c3"
MIGRATION = (
    ROOT
    / "alembic/versions/e9f0a1b2c3d4_add_payment_void_and_rating_source_revision.py"
)


def _module():
    spec = spec_from_file_location("m18_persistence_revision", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m18_is_exact_single_linear_child_and_locks_sources_first() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    child = scripts.get_revision(REVISION)
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade() -> None:", 1)[1].split(
        "def _guard_m18_downgrade_loss", 1
    )[0]

    assert scripts.get_heads() == [REVISION]
    assert child is not None and child.down_revision == PARENT
    lock = (
        "LOCK TABLE debts, payments, rating_events, audit_log "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    assert upgrade.index(lock) < upgrade.index("op.create_unique_constraint(")
    assert source.count('op.create_table(\n        "payment_voids"') == 1
    assert "DELETE FROM" not in source.upper()
    assert "INSERT INTO payment_voids" not in source


def test_payment_void_schema_has_exact_columns_chains_and_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    table = source.split('op.create_table(\n        "payment_voids"', 1)[1].split(
        "op.create_index(", 1
    )[0]
    for column in (
        "id",
        "payment_id",
        "debt_id",
        "shop_customer_id",
        "source_payment_revision",
        "debt_revision_after",
        "voided_by_user_id",
        "reason",
        "voided_at",
    ):
        assert table.count(f'sa.Column("{column}"') == 1
    for constraint in (
        "pk_payment_voids",
        "fk_payment_voids_payment_debt_revision",
        "fk_payment_voids_debt_shop_customer",
        "fk_payment_voids_voided_by_user_id_users_id",
        "uq_payment_voids_payment_id",
        "uq_payment_voids_debt_id_debt_revision_after",
        "ck_payment_voids_reason_allowed",
        "ck_payment_voids_source_payment_revision_positive",
        "ck_payment_voids_debt_revision_after_positive",
        "ck_payment_voids_revision_order",
    ):
        assert constraint in table
    assert "uq_payments_id_debt_id_debt_revision_after" in source
    assert "ix_payment_voids_shop_customer_voided_at_id" in source


def test_rating_metadata_backfill_is_source_complete_and_rows_immutable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for event_type, marker in (
        ("on_time_paid", "p.debt_revision_after"),
        ("overdue", "d.overdue_revision"),
        ("written_off", "d.written_off_revision"),
        ("written_off_settled", "d.written_off_settled_revision"),
    ):
        assert f"re.event_type = '{event_type}'" in source
        assert marker in source
    assert "candidate_count <> 1" in source
    assert "SET source_revision = candidates.source_revision" in source
    assert (
        'op.alter_column("rating_events", "source_revision", nullable=False)' in source
    )
    for forbidden_dml in (
        "UPDATE debts",
        "UPDATE payments",
        "UPDATE audit_log",
        "UPDATE idempotency_keys",
        "INSERT INTO rating_events",
    ):
        assert forbidden_dml not in source


def test_rating_audit_key_extensions_are_closed_and_downgrade_is_guarded() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for token in (
        "uq_rating_events_debt_event_source_revision",
        "ux_rating_events_single_debt_negative_source",
        "ux_rating_events_positive_shop_customer_business_date",
        "ix_rating_events_shop_customer_occurred_debt_event_src_rev",
        "on_time_paid_voided",
        "written_off_settled_voided",
        "payment.voided",
        "debt.reopened_after_payment_void",
        "shop.payments.void",
        "payload ->> 'source' = 'payment_void'",
        "payload ->> 'from_status' = 'paid'",
    ):
        assert token in source

    downgrade = source.split("def downgrade() -> None:", 1)[1]
    assert downgrade.index("_guard_m18_downgrade_loss()") < downgrade.index(
        "_replace_idempotency_check"
    )
    assert downgrade.index('op.drop_table("payment_voids")') < downgrade.index(
        '"uq_payments_id_debt_id_debt_revision_after"'
    )
    guard = source.split("def _guard_m18_downgrade_loss()", 1)[1].split(
        "def downgrade()", 1
    )[0]
    assert guard.count("M18 downgrade blocked:") == 7
    assert "op.drop_" not in guard
    assert "op.create_" not in guard
    assert _module().revision == REVISION


def test_ci_hardcodes_exact_m18_head() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Verify Alembic M18 head" in workflow
    assert f'test "$current_revision" = "{REVISION}"' in workflow
