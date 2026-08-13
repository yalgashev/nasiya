from pathlib import Path

from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from app.audit.models import AuditLog
from app.debt.models import Debt
from app.idempotency.models import IdempotencyKey

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "docs/m14_persistence_plan.md"
M13_MIGRATION_PATH = (
    PROJECT_ROOT / "alembic/versions/f4a5b6c7d8e_add_pending_debt_persistence.py"
)


def _plan() -> str:
    return PLAN_PATH.read_text(encoding="utf-8")


def test_plan_is_one_revision_after_the_exact_m13_baseline() -> None:
    plan = _plan()
    migration = M13_MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision: str = "f4a5b6c7d8e"' in migration
    assert 'down_revision = "f4a5b6c7d8e"' in plan
    assert "one PostgreSQL/Alembic revision" in plan
    assert "a second head" in plan


def test_plan_freezes_exact_payment_columns_constraints_and_index_policy() -> None:
    plan = _plan()
    expected_columns = (
        "`id`",
        "`debt_id`",
        "`recorded_by_user_id`",
        "`amount_uzs`",
        "`method`",
        "`debt_revision_after`",
        "`created_at`",
    )
    payment_section = plan.split("## Exact Payment metadata delta", 1)[1].split(
        "## Exact existing-table deltas", 1
    )[0]

    assert all(column in payment_section for column in expected_columns)
    assert "NUMERIC(18,0)" in payment_section
    assert "TIMESTAMPTZ" in payment_section
    assert "no ORM or database default" in payment_section
    assert "There is no secondary Payment index" in payment_section
    for constraint_name in (
        "pk_payments",
        "ck_payments_amount_uzs_bounds",
        "ck_payments_method_allowed",
        "ck_payments_debt_revision_after_positive",
        "uq_payments_debt_id_debt_revision_after",
        "fk_payments_debt_id_debts_id",
        "fk_payments_recorded_by_user_id_users_id",
    ):
        assert f"`{constraint_name}`" in payment_section
    for forbidden_column in (
        "`updated_at`",
        "`void`",
        "`note`",
        "`reference`",
        "`balance`",
        "`remaining`",
        "`exposure`",
    ):
        assert forbidden_column in payment_section


def test_plan_preserves_m13_debt_indexes_and_names_exactly() -> None:
    plan = _plan()
    debt_checks = {
        constraint.name
        for constraint in Debt.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    debt_indexes = {
        index.name for index in Debt.__table__.indexes if isinstance(index, Index)
    }

    for check_name in (
        "ck_debts_status_allowed",
        "ck_debts_status_metadata_matches_status",
        "ck_debts_timestamp_order",
    ):
        assert check_name in debt_checks
        assert f"`{check_name}`" in plan
    inherited_indexes = {
        "ix_debts_shop_customer_id_created_at_id",
        "ix_debts_shop_customer_id_status_due_date_id",
        "ix_debts_status_pending_expires_at_id",
    }
    assert inherited_indexes < debt_indexes
    assert debt_indexes == inherited_indexes | {
        "ix_debts_status_due_date_id",
        "ix_debts_status_overdue_at_id",
    }
    assert all(f"`{index_name}`" in plan for index_name in inherited_indexes)
    assert "Add nullable `debts.paid_at TIMESTAMPTZ` with no default" in plan


def test_plan_replaces_independent_idempotency_lists_with_one_pairwise_check() -> None:
    plan = _plan()
    checks = {
        constraint.name
        for constraint in IdempotencyKey.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    uniques = {
        constraint.name
        for constraint in IdempotencyKey.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert checks == {
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "ck_idempotency_keys_key_digest_sha256_hex",
        "ck_idempotency_keys_request_hash_sha256_hex",
    }
    assert "`ck_idempotency_keys_endpoint_result_pair_allowed`" in plan
    assert "shop.debts.create' AND result_object_type = 'debt'" in plan
    assert "shop.debt_payments.create' AND result_object_type = 'payment'" in plan
    assert uniques == {"uq_idempotency_keys_actor_user_id_endpoint_key_digest"}
    assert "`uq_idempotency_keys_actor_user_id_endpoint_key_digest`" in plan


def test_plan_extends_only_the_existing_audit_check_registry() -> None:
    plan = _plan()
    audit_checks = {
        constraint.name
        for constraint in AuditLog.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert audit_checks == {
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_kind_allowed",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_actor_matches_event",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_payload_exact_shape",
    }
    assert "`payment.recorded` | USER | `payment`" in plan
    assert "`debt.paid` | USER | `debt`" in plan
    assert (
        "`amount_uzs`, `method`, `from_status`, `to_status`, `debt_revision_after`"
    ) in plan
    assert "`source`, `debt_revision_after`" in plan
    assert "No Audit column, FK, index, system-actor event" in plan


def test_plan_freezes_fk_safe_registration_cleanup_and_operation_order() -> None:
    plan = _plan()
    db_source = (PROJECT_ROOT / "app/db.py").read_text(encoding="utf-8")
    env_source = (PROJECT_ROOT / "alembic/env.py").read_text(encoding="utf-8")

    assert "def _register_database_model_dependencies" in db_source
    assert "target_metadata = Base.metadata" in env_source
    assert "Create `payments` after its existing `users` and `debts` FK parents" in plan
    assert "put `payments` before `idempotency_keys`, `debts`, and `users`" in plan
    assert "register `app.payment.models` in both `app/db.py` and" in plan


def test_all_four_downgrade_guards_precede_exact_m13_restoration() -> None:
    plan = _plan()
    downgrade = plan.split("## Fail-closed downgrade and exact M13 restoration", 1)[1]
    normalized = " ".join(downgrade.split())
    guard_tokens = (
        "EXISTS (SELECT 1 FROM payments)",
        "endpoint = 'shop.debt_payments.create'",
        "result_object_type = 'payment'",
        "status = 'paid' OR paid_at IS NOT NULL",
        "event_type IN ('payment.recorded', 'debt.paid')",
    )

    assert all(token in downgrade for token in guard_tokens)
    assert "before schema mutation" in normalized
    assert "Restore the exact frozen M13 audit" in normalized
    assert "recreate the exact two M13 checks and names" in normalized
    assert "Restore the exact three M13 Debt check expressions" in normalized
    assert "no data loss" in normalized


def test_plan_does_not_authorize_out_of_scope_persistence() -> None:
    plan = _plan().casefold()

    assert "only new table is `payments`" in plan
    for forbidden in (
        "refunds table",
        "notifications table",
        "ratings table",
        "payment_events table",
        "outbox table",
        "gateway_transactions table",
    ):
        assert forbidden not in plan
