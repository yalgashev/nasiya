from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.debt.models import Debt
from app.payment.models import Payment
from app.rating.models import RatingEvent

ROOT = Path(__file__).resolve().parents[1]
M17_HEAD = "d8e9f0a1b2c3"
CURRENT_HEAD = "e9f0a1b2c3d4"


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_m17_migration_compatibility_and_loss_evidence_remain_complete() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [CURRENT_HEAD]
    assert scripts.get_revision(CURRENT_HEAD).down_revision == M17_HEAD
    migration_tests = _source("tests/test_m17_migration_postgresql.py")
    for evidence in (
        "test_fresh_upgrade_empty_downgrade_and_reupgrade",
        "test_mixed_m16_upgrade_preserves_predecessor_columns_and_source_rows",
        "test_db_rejects_invalid_debt_rating_audit_and_idempotency_rows",
        "test_each_independent_m17_loss_class_denies_downgrade",
        "assert tuple(after) == tuple(before)",
        "assert counts_after == counts_before",
    ):
        assert evidence in migration_tests

    persistence = _source("tests/test_m17_persistence_contract.py")
    assert "ix_debts_status_overdue_at_id" in persistence
    index_contract = persistence.split(
        'index.name == "ix_debts_status_overdue_at_id"', 1
    )[1].split("assert index.unique", 1)[0]
    positions = tuple(
        index_contract.index(f'"{name}"') for name in ("status", "overdue_at", "id")
    )
    assert positions == tuple(sorted(positions))
    plan = _source("docs/m17_persistence_plan.md").casefold()
    for operational_rule in (
        "old-writer drain",
        "restart",
        "share row exclusive",
        "no backfill",
        "downgrade",
    ):
        assert operational_rule in plan


def test_m17_schema_adds_no_parallel_table_or_cached_money_rating_state() -> None:
    debt_columns = set(Debt.__table__.columns.keys())
    assert {
        "written_off_at",
        "written_off_revision",
        "written_off_reason",
        "written_off_actor_user_id",
        "written_off_settled_at",
        "written_off_settled_revision",
    } <= debt_columns
    for forbidden in (
        "cached_score",
        "cached_band",
        "cached_balance",
        "written_off_balance",
        "forgiven_amount",
    ):
        assert forbidden not in debt_columns
        assert forbidden not in Payment.__table__.columns
        assert forbidden not in RatingEvent.__table__.columns
    migration = _source(
        "alembic/versions/d8e9f0a1b2c3_add_written_off_debt_persistence.py"
    )
    assert "op.create_table(" not in migration
    assert "UPDATE " not in migration.upper()
    assert "DELETE FROM" not in migration.upper()


def test_m17_runtime_has_no_out_of_scope_product_wiring() -> None:
    m17_runtime_paths = (
        "app/debt/write_off_core.py",
        "app/debt/write_off_targeting.py",
        "app/debt/write_off_service.py",
        "app/debt/admin_write_off_presentation.py",
        "app/debt/admin_write_off_router.py",
        "app/payment/service.py",
        "app/payment/presentation.py",
        "app/rating/adapters.py",
        "app/rating/current_read_service.py",
    )
    runtime = "\n".join(_source(path) for path in m17_runtime_paths).casefold()
    for forbidden in (
        "refund_payment",
        "reverse_payment",
        "correct_payment",
        "debt_forgiveness",
        "scheduler",
        "cron_worker",
        "job_run",
        "notification_outbox",
        "write_off_report",
        "write_off_export",
        "rating_override",
        "system_setting",
        "bulk_write_off",
        "global_debt_search",
        '"/api/',
        "cached_score",
        "cached_balance",
    ):
        assert forbidden not in runtime


def test_m17_privacy_surfaces_have_only_the_frozen_reason_exception() -> None:
    admin_templates = "\n".join(
        _source(path)
        for path in (
            "app/templates/debt/admin_write_off_candidates.html",
            "app/templates/debt/admin_write_off_detail.html",
        )
    ).casefold()
    shop_customer_templates = "\n".join(
        _source(path)
        for path in (
            "app/templates/payment/shop_list.html",
            "app/templates/payment/shop_new.html",
            "app/templates/payment/shop_receipt.html",
            "app/templates/payment/customer_list.html",
            "app/templates/payment/customer_receipt.html",
            "app/templates/rating/disclosure_view.html",
        )
    ).casefold()
    assert 'name="reason"' in admin_templates
    assert "completed.reason" in admin_templates
    for forbidden in (
        "written_off_reason",
        "written_off_actor",
        "actor_user_id",
        "ratingevent",
        "current_score",
        "event_count",
        "block_cause",
    ):
        assert forbidden not in shop_customer_templates

    runtime = "\n".join(
        _source(path)
        for path in (
            "app/debt/write_off_core.py",
            "app/debt/write_off_targeting.py",
            "app/debt/write_off_service.py",
            "app/debt/admin_write_off_presentation.py",
            "app/debt/admin_write_off_router.py",
        )
    ).casefold()
    for forbidden in ("logger.", "logging.", "print("):
        assert forbidden not in runtime


def test_inherited_containment_guards_are_present_and_not_bypassed() -> None:
    inherited = {
        "tests/test_m14_out_containment.py": (
            "test_payment_routes_and_schema_have_no_out_surface_or_cached_financial_state"
        ),
        "tests/test_m15_out_containment.py": (
            "test_future_integrations_are_absent_from_new_m15_runtime_surfaces"
        ),
        "tests/test_m16_hardening_contracts.py": (
            "test_migration_and_runtime_keep_m16_out_vocabulary_contained"
        ),
    }
    for relative_path, test_name in inherited.items():
        source = _source(relative_path)
        assert f"def {test_name}" in source
        assert "pytest.skip" not in source
        assert "pytest.mark.xfail" not in source
