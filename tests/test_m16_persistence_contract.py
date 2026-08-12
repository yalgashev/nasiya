from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.audit.models import AuditLog
from app.debt.models import Debt
from app.idempotency.models import IdempotencyKey
from app.rating.models import DisclosureViewLog, RatingEvent
from app.shop_customer.models import ShopCustomer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVISION = "c7d8e9f0a1b2"
PARENT = "b6c7d8e9f0a1"
MIGRATION = (
    PROJECT_ROOT
    / "alembic/versions/c7d8e9f0a1b2_add_rating_and_disclosure_persistence.py"
)


def _named_constraints(table, constraint_type):
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def test_m16_is_exact_single_linear_child_and_ci_head() -> None:
    scripts = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    child = scripts.get_revision(REVISION)
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert scripts.get_heads() == [REVISION]
    assert child is not None and child.down_revision == PARENT
    assert "Verify Alembic M16 head" in workflow
    assert f'test "$current_revision" = "{REVISION}"' in workflow


def test_revision_has_exact_two_tables_and_lock_is_first_source_action() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade() -> None:", 1)[1].split(
        "def _m15_audit_valid_sql", 1
    )[0]

    assert source.count("op.create_table(") == 2
    assert 'op.create_table(\n        "rating_events"' in source
    assert 'op.create_table(\n        "disclosure_view_logs"' in source
    assert upgrade.index("LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE") < (
        upgrade.index("op.create_unique_constraint")
    )
    assert "UPDATE " not in source.upper()
    assert "DELETE FROM" not in source.upper()
    assert "TRUNCATE " not in source.upper()
    assert "historical_reconciliation" in source
    assert "uuid5(" in source
    assert 'f"{event_type}:{row.debt_id}"' in source


def test_rating_metadata_matches_exact_chain_checks_and_indexes() -> None:
    table = RatingEvent.__table__
    assert tuple(table.columns) and tuple(table.columns.keys()) == (
        "id",
        "shop_customer_id",
        "debt_id",
        "event_type",
        "delta",
        "occurred_at",
        "business_date",
        "recording_source",
    )
    assert _named_constraints(table, CheckConstraint) == {
        "ck_rating_events_event_type_allowed",
        "ck_rating_events_delta_matches_event",
        "ck_rating_events_recording_source_allowed",
        "ck_rating_events_business_date_matches_occurred_at",
    }
    assert "fk_rating_events_debt_shop_customer" in _named_constraints(
        table, ForeignKeyConstraint
    )
    assert "uq_rating_events_debt_id_event_type" in _named_constraints(
        table, UniqueConstraint
    )
    indexes = {index.name: index for index in table.indexes}
    assert set(indexes) == {
        "ux_rating_events_positive_shop_customer_business_date",
        "ix_rating_events_shop_customer_occurred_debt_event",
    }
    assert indexes["ux_rating_events_positive_shop_customer_business_date"].unique
    assert (
        str(
            indexes[
                "ux_rating_events_positive_shop_customer_business_date"
            ].dialect_options["postgresql"]["where"]
        )
        == "event_type = 'on_time_paid'"
    )


def test_disclosure_metadata_and_parent_redundant_uniques_are_exact() -> None:
    table = DisclosureViewLog.__table__
    assert tuple(table.columns.keys()) == (
        "id",
        "actor_user_id",
        "shop_id",
        "shop_customer_id",
        "purpose",
        "band",
        "created_at",
    )
    assert _named_constraints(table, CheckConstraint) == {
        "ck_disclosure_view_logs_purpose_allowed",
        "ck_disclosure_view_logs_band_allowed",
    }
    assert _named_constraints(table, ForeignKeyConstraint) == {
        "fk_disclosure_logs_shop_customer_shop",
        "fk_disclosure_view_logs_actor_user_id_users_id",
    }
    assert {index.name for index in table.indexes} == {
        "ix_disclosure_view_logs_shop_id_id"
    }
    assert "uq_debts_id_shop_customer_id" in _named_constraints(
        Debt.__table__, UniqueConstraint
    )
    assert "uq_shop_customers_id_shop_id" in _named_constraints(
        ShopCustomer.__table__, UniqueConstraint
    )


def test_registry_extensions_are_closed_and_json_values_are_strings() -> None:
    idempotency_check = next(
        constraint
        for constraint in IdempotencyKey.__table__.constraints
        if constraint.name == "ck_idempotency_keys_endpoint_result_pair_allowed"
    )
    idempotency_sql = str(idempotency_check.sqltext)
    assert idempotency_sql.count("shop.risk_band_disclosures.create") == 1
    assert idempotency_sql.count("disclosure_view") == 1

    audit_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in AuditLog.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    payload = audit_checks["ck_audit_log_payload_exact_shape"]
    assert "disclosure.risk_band_viewed" in payload
    assert "jsonb_typeof(payload -> 'purpose') = 'string'" in payload
    assert "jsonb_typeof(payload -> 'band') = 'string'" in payload
    for value in (
        "debt_proposal_review",
        "credit_limit_review",
        "existing_debt_review",
        "new",
        "green",
        "yellow",
        "red",
        "blocked",
    ):
        assert value in payload


def test_downgrade_guards_precede_ddl_and_parent_uniques_drop_last() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    guard_end = downgrade.index("_replace_audit_checks(include_m16=False)")
    guard = downgrade[:guard_end]

    assert "M16 downgrade blocked:" in guard
    assert "rating_events" in guard
    assert "disclosure_view_logs" in guard
    assert "shop.risk_band_disclosures.create" in guard
    assert "disclosure.risk_band_viewed" in guard
    assert "op.drop_" not in guard
    assert downgrade.index('op.drop_table("rating_events")') < downgrade.index(
        '"uq_debts_id_shop_customer_id"'
    )
    assert downgrade.index('op.drop_table("disclosure_view_logs")') < (
        downgrade.index('"uq_shop_customers_id_shop_id"')
    )


def test_repositories_borrow_session_and_return_only_detached_safe_contracts() -> None:
    source = (PROJECT_ROOT / "app/rating/repository.py").read_text(encoding="utf-8")
    for forbidden in ("session.commit(", "session.rollback(", "session.close("):
        assert forbidden not in source
    assert "RatingEventContract(" in source
    assert "RiskBandDisclosureProjection(" in source
    assert "RatingEvent(<redacted>)" in (
        PROJECT_ROOT / "app/rating/models.py"
    ).read_text(encoding="utf-8")
    assert "DisclosureViewLog(<redacted>)" in (
        PROJECT_ROOT / "app/rating/models.py"
    ).read_text(encoding="utf-8")
