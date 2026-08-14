from __future__ import annotations

import ast
from inspect import getsource
from pathlib import Path

import app.payment.void_service as void_service

ROOT = Path(__file__).resolve().parents[1]


def test_m18_void_stage_order_extends_the_inherited_append_tail() -> None:
    source = getsource(void_service.void_payment)
    _assert_ordered(
        source,
        "lock_tenant_payment_void_predecessors(",
        "insert_or_resolve_key(",
        "lock_tenant_payment_void_target(",
        "read_pre_transition_source(",
        "capture_payment_server_now(",
        "update_locked_debt(",
        "insert_payment_void(",
        "append_source_compensation(",
        "append_pending_overdue(",
        "append_pending_overdue_audits(",
        "create_payment_voided_audit_event(",
        "create_debt_reopened_after_payment_void_audit_event(",
    )


def test_m18_concurrency_matrix_reuses_only_deterministic_forward_barriers() -> None:
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
        "tests/test_m15_transition_race_postgresql.py": (
            "test_on_time_payment_holds_lock_before_stale_batch_revalidation",
            "test_batch_holds_lock_before_boundary_payment_and_is_the_only_winner",
        ),
        "tests/test_m16_combined_lock_order_postgresql.py": (
            "test_cross_shop_disclosure_and_create_serialize_at_customer_forward_lock",
        ),
        "tests/test_m16_disclosure_barriers_postgresql.py": (
            "test_disclosure_before_payoff_is_complete_old_snapshot",
            "test_payoff_before_disclosure_is_complete_new_snapshot",
            "test_batch_overdue_before_disclosure_is_one_complete_blocked_snapshot",
        ),
        "tests/test_m17_global_lock_order_postgresql.py": (
            "test_write_off_before_late_payoff_is_one_complete_written_off_state",
            "test_late_payoff_before_write_off_is_one_complete_paid_state",
        ),
        "tests/test_m17_recovery_service_postgresql.py": (
            "test_two_terminal_attempts_serialize_to_one_payment_and_one_plus_ten",
        ),
        "tests/test_m18_payment_void_service_postgresql.py": (
            "test_two_voids_serialize_to_one_complete_append_family",
            "test_void_vs_new_payment_preserves_one_linear_latest_stack",
        ),
        "tests/test_shop_customer_link_concurrency_postgresql.py": (
            "test_link_vs_suspend_has_no_deadlock_and_valid_serial_outcome",
            "test_link_vs_actor_revoke_has_no_deadlock_and_valid_serial_outcome",
            "test_link_vs_m11_ordered_activation_has_no_deadlock_and_valid_outcome",
        ),
        "tests/test_shop_customer_policy_concurrency_postgresql.py": (
            "test_parallel_policy_writers_have_exactly_one_revision_winner",
            "test_parallel_default_writers_have_one_updated_at_winner_and_one_stale_result",
        ),
    }
    concurrency_sources: list[str] = []
    module_sources: list[str] = []
    for relative_path, test_names in evidence.items():
        path = ROOT / relative_path
        source = path.read_text(encoding="utf-8")
        module_sources.append(source)
        for test_name in test_names:
            assert f"def {test_name}" in source
            concurrency_sources.append(_function_source(path, test_name))

    combined = "\n".join(concurrency_sources).casefold()
    assert all(
        "from threading import barrier" in source.casefold()
        or "from threading import event" in source.casefold()
        for source in module_sources
    )
    for forbidden in ("sleep(", "retry", "nowait", "skip_locked"):
        assert forbidden not in combined
    m18_sources = (
        ROOT / "tests/test_m18_payment_void_service_postgresql.py"
    ).read_text(encoding="utf-8")
    assert "timeout=" not in m18_sources.casefold()


def test_combined_graph_keeps_uuid_and_append_unique_wait_evidence() -> None:
    sources = {
        relative: (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "tests/test_shop_customer_link_concurrency_postgresql.py",
            "tests/test_m14_combined_lock_order_postgresql.py",
            "tests/test_m18_payment_void_service_postgresql.py",
        )
    }
    assert (
        "test_static_trace_preserves_total_and_same_class_uuid_order"
        in sources["tests/test_shop_customer_link_concurrency_postgresql.py"]
    )
    assert (
        "test_same_key_waits_for_unique_resolution_then_persists_one_key"
        in sources["tests/test_m14_combined_lock_order_postgresql.py"]
    )
    assert (
        "test_two_voids_serialize_to_one_complete_append_family"
        in sources["tests/test_m18_payment_void_service_postgresql.py"]
    )
    assert "separate completion" not in getsource(void_service.void_payment).casefold()


def _assert_ordered(source: str, *needles: str) -> None:
    positions = tuple(source.index(needle) for needle in needles)
    assert positions == tuple(sorted(positions))


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    segment = ast.get_source_segment(source, node)
    assert segment is not None
    return segment
