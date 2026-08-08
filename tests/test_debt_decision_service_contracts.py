from datetime import UTC, datetime
from inspect import getsource
from uuid import UUID

from app.debt.customer_reject_service import (
    RejectCustomerDebtCommand,
    reject_own_customer_debt,
)
from app.debt.tenant_cancel_service import (
    CancelTenantDebtCommand,
    cancel_tenant_debt,
)
from app.debt.tenant_cancel_targeting import lock_tenant_debt_for_cancel
from app.debt.values import DebtId, DebtRevision


def test_reject_and_cancel_commands_never_render_private_reasons_or_ids() -> None:
    debt_id = UUID("11111111-1111-4111-8111-111111111111")
    now = datetime(2026, 8, 8, tzinfo=UTC)
    reject = RejectCustomerDebtCommand(
        debt_id=DebtId(debt_id),
        expected_revision=DebtRevision(1),
        now=now,
        raw_reason="SECRET REJECTION",
    )
    cancel = CancelTenantDebtCommand(
        debt_id=DebtId(debt_id),
        expected_revision=DebtRevision(1),
        now=now,
        raw_reason="SECRET CANCELLATION",
    )

    for rendered in (repr(reject), repr(cancel)):
        assert str(debt_id) not in rendered
        assert "SECRET" not in rendered


def test_cancel_locks_forward_and_decision_services_borrow_the_session() -> None:
    targeting = getsource(lock_tenant_debt_for_cancel)
    reject = getsource(reject_own_customer_debt)
    cancel = getsource(cancel_tenant_debt)

    assert (
        targeting.index("lock_shop_for_update")
        < targeting.index("lock_actor_shop_staff_for_update")
        < targeting.index("select(User)")
        < targeting.index("lock_shop_customer_by_tenant_locator")
        < targeting.index("select(Debt)")
    )
    assert "offer=None" in reject
    for source in (reject, cancel):
        assert "OfferAcceptance" not in source
        for forbidden in (".commit(", ".rollback(", ".close("):
            assert forbidden not in source
