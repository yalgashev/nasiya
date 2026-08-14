from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from app.debt.models import Debt
from app.payment.models import Payment, PaymentVoid
from app.payment.presentation import (
    M18_PAYMENT_VOID_ROUTE_CONTRACTS,
    CustomerPaymentVoidPresentation,
    ShopPaymentVoidPresentation,
)

ROOT = Path(__file__).resolve().parents[1]
M18_RUNTIME = (
    ROOT / "app/payment/void_targeting.py",
    ROOT / "app/payment/void_source.py",
    ROOT / "app/payment/void_service.py",
    ROOT / "app/payment/rating_ports.py",
    ROOT / "app/rating/adapters.py",
    ROOT / "app/payment/read_service.py",
    ROOT / "app/payment/router.py",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.append(node.module)
    return tuple(found)


def test_m18_payment_local_sources_have_only_structural_rating_dependencies() -> None:
    for path in (
        ROOT / "app/payment/rating_ports.py",
        ROOT / "app/payment/void_source.py",
    ):
        assert all(
            not imported.startswith(("app.rating", "app.audit.models"))
            for imported in _imports(path)
        )


def test_m18_runtime_keeps_session_ownership_and_out_capabilities_absent() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in M18_RUNTIME)
    folded = source.casefold()
    for forbidden in (
        "session.commit(",
        "session.rollback(",
        "session.close(",
        "sleep(",
        "retry",
        "nowait",
        "skip_locked",
        "refund_payment",
        "payment_refund",
        "payment_payout",
        "chargeback_payment",
        "unvoid_payment",
        "reverse_payment",
        "edit_payment",
        "delete_payment",
        "forgive_debt",
        "overdue_voided",
        "written_off_voided",
        "rating_override",
        "void_setting",
        "void_scheduler",
        "void_cron",
        "void_worker",
        "void_job_run",
        "void_notification",
        "notification_outbox",
        "payment_void_report",
        "payment_void_export",
        "bulk_payment_void",
        "search_payment_void",
        "cached_payment_total",
        "cached_rating_score",
        "m19",
        '"/api/',
        '"/admin/',
        '"/customer/payments/{payment_id}/void',
    ):
        assert forbidden not in folded


def test_m18_edge_and_compatibility_evidence_names_are_tracked_and_live() -> None:
    evidence = {
        "tests/test_m15_late_payment_postgresql.py": (
            "test_debt_lock_midnight_wait_uses_post_lock_clock_and_rejects_stale_basis",
            "test_on_time_basis_stays_discounted_at_last_tashkent_microsecond",
        ),
        "tests/test_m18_payment_void_money_contracts.py": (
            "test_current_anti_join_and_revision_as_of_predicate_are_exact",
            "test_void_money_is_exact_zero_inclusive_and_remaining_positive",
            "test_void_money_denies_underflow_zero_remaining_and_inexact_result",
        ),
        "tests/test_m18_debt_void_transition_contracts.py": (
            "test_partial_void_preserves_status_markers_and_exactly_one_revision",
            "test_paid_void_on_due_date_reopens_active_and_clears_only_paid_marker",
            "test_paid_after_due_void_is_one_revision_with_exact_pending_overdue_effect",
            "test_late_paid_void_preserves_old_overdue_marker_without_new_effect",
            "test_settlement_void_reopens_written_off_and_clears_only_settlement_pair",
            "test_stale_revision_terminal_result_and_nonpositive_remaining_are_denied",
        ),
        "tests/test_m18_rating_cycle_contracts.py": (
            "test_lawful_multi_cycle_sequence_and_per_event_clamp",
            "test_same_instant_settlement_cycles_follow_lexical_type_then_revision_order",
        ),
        "tests/test_m18_payment_void_service_postgresql.py": (
            "test_compensated_daily_slot_remains_consumed_and_later_day_reearns",
            "test_settlement_cycle_compensation_cannot_farm_bonus",
        ),
        "tests/test_m16_disclosure_barriers_postgresql.py": (
            "test_disclosure_before_payoff_is_complete_old_snapshot",
            "test_payoff_before_disclosure_is_complete_new_snapshot",
            "test_batch_overdue_before_disclosure_is_one_complete_blocked_snapshot",
        ),
    }
    for relative_path, tests in evidence.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for test_name in tests:
            assert f"def {test_name}" in source


def test_m18_adds_one_ledger_table_without_payment_or_debt_void_state() -> None:
    assert PaymentVoid.__tablename__ == "payment_voids"
    assert all("void" not in name for name in Payment.__table__.columns.keys())
    assert all("void" not in name for name in Debt.__table__.columns.keys())


def test_m18_route_and_projection_surfaces_are_exactly_closed() -> None:
    assert tuple(
        (route.method, route.path) for route in M18_PAYMENT_VOID_ROUTE_CONTRACTS
    ) == (
        ("GET", "/shop/payments/{payment_id}/void"),
        ("POST", "/shop/payments/{payment_id}/void"),
    )
    assert {field.name for field in fields(ShopPaymentVoidPresentation)} == {
        "is_voided",
        "voided_at",
        "reason_label",
    }
    assert {field.name for field in fields(CustomerPaymentVoidPresentation)} == {
        "is_voided",
        "voided_at",
    }
