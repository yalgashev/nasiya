from __future__ import annotations

from inspect import getsource
from pathlib import Path

from app.debt import overdue_service
from app.payment import service as payment_service
from app.rating import disclosure_service
from app.rating.models import DisclosureViewLog, RatingEvent
from app.rating.router import router as rating_router

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _assert_ordered(source: str, *needles: str) -> None:
    positions = tuple(source.index(needle) for needle in needles)
    assert positions == tuple(sorted(positions))


def test_global_m16_append_tails_keep_exact_stage_order() -> None:
    payment = getsource(payment_service.record_debt_payment)
    _assert_ordered(
        payment,
        "update_locked_debt",
        "insert_payment",
        "append_pending_overdue(",
        "append_pending_overdue_audits",
        "append_payment_recorded_audit",
    )
    _assert_ordered(
        payment,
        "update_locked_debt",
        "insert_payment",
        "append_pending_on_time_paid(",
        "append_payment_recorded_audit",
        "append_debt_paid_audit",
    )

    locked_overdue = getsource(overdue_service.materialize_locked_overdue_debt)
    assert "update_locked_debt" in locked_overdue
    assert "append_audit_event" not in locked_overdue
    assert "append_pending_overdue(" not in locked_overdue
    batch = getsource(overdue_service.materialize_overdue_candidate)
    _assert_ordered(
        batch,
        "materialize_locked_overdue_debt",
        "append_pending_overdue(",
        "append_pending_overdue_audits",
    )

    disclosure = getsource(disclosure_service.record_risk_band_disclosure)
    _assert_ordered(
        disclosure,
        "lock_tenant_disclosure_target",
        "insert_or_resolve_key",
        "read_locked_current_risk_band",
        "insert_disclosure_view_locked",
        "append_risk_band_disclosure_audit",
    )


def test_combined_barrier_evidence_covers_inherited_and_m16_graph() -> None:
    evidence = {
        "tests/test_m14_combined_lock_order_postgresql.py": (
            "test_payment_before_new_debt_is_a_complete_after_state_under_shop_barrier",
            "test_pending_accept_completes_before_payment_as_one_forward_ordered_history",
            "test_pending_terminal_transition_completes_before_payment_with_zero_ledger_write",
            "test_same_key_waits_for_unique_resolution_then_persists_one_key",
        ),
        "tests/test_m15_combined_lock_order_postgresql.py": (
            "test_batch_audit_vs_cross_shop_create_finishes_in_complete_blocked_state",
            "test_batch_vs_m12_policy_mutations_serialize_at_shop_predecessor",
        ),
        "tests/test_m16_combined_lock_order_postgresql.py": (
            "test_cross_shop_disclosure_and_create_serialize_at_customer_forward_lock",
        ),
        "tests/test_m16_disclosure_barriers_postgresql.py": (
            "test_disclosure_before_payoff_is_complete_old_snapshot",
            "test_payoff_before_disclosure_is_complete_new_snapshot",
            "test_batch_overdue_before_disclosure_is_one_complete_blocked_snapshot",
        ),
        "tests/test_m16_rating_producer_races_postgresql.py": (
            "test_parallel_exact_payoff_is_one_source_for_same_or_different_key",
            "test_parallel_eligible_debts_same_pair_day_keep_two_payments_one_bonus",
        ),
    }
    for path, test_names in evidence.items():
        source = _source(path)
        for name in test_names:
            assert f"def {name}" in source

    m16_barriers = "\n".join(
        _source(path)
        for path in (
            "tests/test_m16_combined_lock_order_postgresql.py",
            "tests/test_m16_disclosure_barriers_postgresql.py",
            "tests/test_m16_rating_producer_races_postgresql.py",
        )
    ).casefold()
    for forbidden in (
        "sleep(",
        "retry",
        "timeout",
        "nowait",
        "skip_locked",
        "pg_advisory",
    ):
        assert forbidden not in m16_barriers


def test_migration_and_runtime_keep_m16_out_vocabulary_contained() -> None:
    runtime_paths = tuple(
        path
        for path in (PROJECT_ROOT / "app/rating").glob("*.py")
        if path.name not in {"__pycache__", "enums.py"}
    )
    written_off_paths = {
        path.name
        for path in runtime_paths
        if "written_off" in path.read_text(encoding="utf-8").casefold()
    }
    assert written_off_paths == {
        "adapters.py",
        "contracts.py",
        "current_read_service.py",
        "models.py",
        "service.py",
    }

    source = "\n".join(path.read_text(encoding="utf-8") for path in runtime_paths)
    m16_migration = _source(
        "alembic/versions/c7d8e9f0a1b2_add_rating_and_disclosure_persistence.py"
    )
    assert "-40" not in m16_migration and "+10" not in m16_migration
    source += m16_migration
    lowered = source.casefold()
    for forbidden in (
        "void_payment",
        "rating_override",
        "system_setting",
        "scheduler",
        "job_run",
        "notification",
        "score_cache",
        "band_cache",
        "cached_score",
        "cached_band",
    ):
        assert forbidden not in lowered

    assert set(RatingEvent.__table__.columns.keys()) == {
        "id",
        "shop_customer_id",
        "debt_id",
        "event_type",
        "delta",
        "occurred_at",
        "business_date",
        "recording_source",
        "source_revision",
    }
    assert set(DisclosureViewLog.__table__.columns.keys()) == {
        "id",
        "actor_user_id",
        "shop_id",
        "shop_customer_id",
        "purpose",
        "band",
        "created_at",
    }


def test_disclosure_delivery_and_template_have_no_parallel_raw_channel() -> None:
    routes = {
        (method, route.path)
        for route in rating_router.routes
        for method in route.methods or ()
    }
    assert routes == {
        ("POST", "/shop/customers/{shop_customer_id}/risk-band-disclosures"),
        ("GET", "/shop/risk-band-disclosures/{disclosure_view_id}"),
    }
    template = _source("app/templates/rating/disclosure_view.html").casefold()
    for forbidden in (
        "score",
        "delta",
        "event_count",
        "history",
        "amount",
        "balance",
        "cause",
        "customer_name",
        "phone",
        "<script",
    ):
        assert forbidden not in template
    assert 'name="idempotency_key"' in template
    assert 'method="post"' in template
    assert "localstorage" not in template
    assert "sessionstorage" not in template
