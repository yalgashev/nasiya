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
        "unvoid_payment",
        "reverse_payment",
        "rating_override",
        "notification_outbox",
        "payment_void_report",
        "bulk_payment_void",
        '"/api/',
        '"/admin/',
        '"/customer/payments/{payment_id}/void',
    ):
        assert forbidden not in folded


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
