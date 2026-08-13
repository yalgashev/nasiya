from inspect import getsource
from pathlib import Path

from app.debt import overdue_service
from app.payment import service as payment_service
from app.rating import current_read_service, repository

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_m16_rating_checkpoint_matches_frozen_producer_and_read_contract() -> None:
    scope = _source("docs/m16_scope_contract.md")
    decisions = _source("docs/m16_decisions.md")
    runtime = "\n".join(
        (
            getsource(payment_service.record_debt_payment),
            getsource(overdue_service.materialize_locked_overdue_debt),
            getsource(overdue_service.materialize_overdue_candidate),
            getsource(current_read_service),
            getsource(repository.read_ordered_locked_event_tuples),
        )
    )

    for required in (
        "Customer lock remains the serialization point",
        "`+5`",
        "`-15`",
        "daily cap",
        "effective hard-block",
        "sequential",
    ):
        assert required.casefold() in (scope + decisions).casefold()
    assert "append_pending_on_time_paid" in runtime
    assert "append_pending_overdue" in runtime
    assert "append_pending_overdue_audits" in runtime
    assert "read_ordered_locked_event_tuples" in runtime
    assert "func.sum" not in runtime.casefold()


def test_m16_rating_checkpoint_allows_only_the_later_m17_written_off_extension() -> (
    None
):
    changed_runtime = "\n".join(
        _source(path)
        for path in (
            "app/rating/adapters.py",
            "app/rating/current_read_service.py",
            "app/payment/service.py",
            "app/debt/overdue_service.py",
        )
    ).casefold()
    for forbidden in (
        "@router.",
        "from app.notification",
        "from app.scheduler",
        "void_payment",
        "reverse_payment",
        "rating_override",
        "score_cache",
        "band_cache",
    ):
        assert forbidden not in changed_runtime
    assert "written_off" in changed_runtime
    assert "void_payment" not in changed_runtime


def test_m16_rating_checkpoint_keeps_required_barrier_and_boundary_evidence() -> None:
    evidence = {
        "tests/test_m16_rating_producer_races_postgresql.py": (
            "test_parallel_exact_payoff_is_one_source_for_same_or_different_key",
            "test_parallel_eligible_debts_same_pair_day_keep_two_payments_one_bonus",
            "test_same_customer_two_shops_serialize_complete_two_pair_bonus_state",
            "test_first_or_second_payment_audit_fault_rolls_back_bonus_and_financial_unit",
            "test_suspended_exact_payoff_is_zero_write_and_redacted",
        ),
        "tests/test_m15_transition_race_postgresql.py": (
            "test_on_time_payment_holds_lock_before_stale_batch_revalidation",
            "test_batch_holds_lock_before_boundary_payment_and_is_the_only_winner",
        ),
        "tests/test_m15_overdue_service_postgresql.py": (
            "test_overlapping_batch_runs_are_exact_once",
        ),
        "tests/test_m16_rating_scoring.py": (
            "test_sequential_clamp_hits_exact_zero_and_hundred_edges",
        ),
        "tests/test_m16_positive_eligibility.py": (
            "test_exact_threshold_and_due_date_boundary_award_one_bonus",
            "test_partial_threshold_same_day_late_marker_replay_and_state_matrix_get_no_bonus",
        ),
    }
    for path, symbols in evidence.items():
        source = _source(path)
        assert "sleep(" not in source
        assert "retry" not in source.casefold()
        for symbol in symbols:
            assert symbol in source
