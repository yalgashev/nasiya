from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.payment.models import Payment
from app.payment.presentation import PAYMENT_ROUTE_CONTRACTS
from app.payment.service import record_debt_payment

PAYMENT_ROOT = Path("app/payment")
PAYMENT_PYTHON = tuple(sorted(PAYMENT_ROOT.glob("*.py")))
FORBIDDEN_IMPORT_ROOTS = (
    "app.rating",
    "app.notification",
    "app.notifications",
    "app.scheduler",
    "app.reporting",
    "app.gateway",
    "requests",
    "boto3",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def test_payment_runtime_has_no_out_process_or_external_io_dependency() -> None:
    assert PAYMENT_PYTHON
    for path in PAYMENT_PYTHON:
        for imported in _imports(path):
            assert not imported.startswith(FORBIDDEN_IMPORT_ROOTS), (
                f"{path} imports M14 OUT dependency {imported}"
            )


def test_payment_routes_and_schema_have_no_out_surface_or_cached_financial_state() -> (
    None
):
    route_text = "\n".join(
        f"{route.method} {route.path} {route.name}" for route in PAYMENT_ROUTE_CONTRACTS
    ).casefold()
    for forbidden in (
        "void",
        "refund",
        "overdue",
        "write-off",
        "written_off",
        "rating",
        "notification",
        "scheduler",
        "report",
        "gateway",
        "admin",
        "/api",
    ):
        assert forbidden not in route_text

    columns = set(Payment.__table__.columns.keys())
    assert columns == {
        "id",
        "debt_id",
        "recorded_by_user_id",
        "amount_uzs",
        "method",
        "debt_revision_after",
        "created_at",
    }
    assert columns.isdisjoint(
        {
            "balance",
            "remaining",
            "exposure",
            "status",
            "updated_at",
            "voided_at",
            "gateway_reference",
            "cache_key",
        }
    )


def test_financial_coordinator_is_borrowed_decimal_only_and_has_no_workaround() -> None:
    coordinator = inspect.getsource(record_debt_payment).casefold()
    financial_source = "\n".join(
        (PAYMENT_ROOT / name).read_text(encoding="utf-8")
        for name in ("values.py", "repository.py", "service.py")
    ).casefold()

    for forbidden in (
        "commit(",
        "rollback(",
        "close(",
        "sleep(",
        "retry",
        "nowait",
        "skip_locked",
        "pg_advisory",
    ):
        assert forbidden not in coordinator
    assert "float(" not in financial_source
    assert "round(" not in financial_source
