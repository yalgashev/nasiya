from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import Decimal
from inspect import getsource
from uuid import UUID

import pytest

import app.payment.read_service as payment_read_service
from app.auth.error_codes import ErrorCode
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.payment_progress import DebtPaymentProgressProjection
from app.debt.presentation import DebtWebLanguage
from app.payment.contracts import PaymentReceiptProjection
from app.payment.dependencies import (
    DetachedPaymentReadActorContext,
    get_detached_current_shop_payment_read_actor_context,
)
from app.payment.enums import PaymentMethod
from app.payment.models import Payment
from app.payment.read_service import (
    CustomerPaymentReadResult,
    TenantPaymentReadResult,
    _history,
    _progress,
    resolve_tenant_payment_read_authority,
)
from app.payment.repository import ScopedPaymentRow
from app.payment.values import PostedPaymentTotalUZS
from app.shop.enums import ShopRole


def _debt(
    *, status: DebtStatus, due_date: date, paid_at: datetime | None = None
) -> Debt:
    return Debt(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        shop_customer_id=UUID("22222222-2222-4222-8222-222222222222"),
        created_by_user_id=UUID("33333333-3333-4333-8333-333333333333"),
        original_amount_uzs=Decimal("1000"),
        discount_basis_points=1000,
        discounted_amount_uzs=Decimal("900"),
        due_date=due_date,
        pending_expires_at=datetime(2026, 1, 2, tzinfo=UTC),
        status=status.value,
        revision=3,
        paid_at=paid_at,
    )


def test_progress_is_server_derived_and_uses_tashkent_due_boundary() -> None:
    now = datetime(2026, 1, 1, 20, tzinfo=UTC)  # 2026-01-02 in Tashkent
    active = _progress(
        _debt(status=DebtStatus.ACTIVE, due_date=date(2026, 1, 2)),
        PostedPaymentTotalUZS(Decimal("400")),
        now,
    )
    past_due = _progress(
        _debt(status=DebtStatus.ACTIVE, due_date=date(2026, 1, 1)),
        PostedPaymentTotalUZS(Decimal("400")),
        now,
    )
    paid = _progress(
        _debt(
            status=DebtStatus.PAID,
            due_date=date(2026, 1, 2),
            paid_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        PostedPaymentTotalUZS(Decimal("900")),
        now,
    )

    assert active.posted_total_uzs == Decimal("400")
    assert active.remaining_due_uzs == Decimal("500")
    assert active.is_payable is True
    assert past_due.is_payable is False
    assert paid.remaining_due_uzs == Decimal("0")
    assert paid.is_payable is False


def test_progress_and_receipt_projections_are_identifier_and_privacy_safe() -> None:
    assert tuple(field.name for field in fields(DebtPaymentProgressProjection)) == (
        "posted_total_uzs",
        "remaining_due_uzs",
        "status",
        "paid_at",
        "is_payable",
    )
    receipt_fields = {field.name for field in fields(PaymentReceiptProjection)}
    assert receipt_fields == {
        "amount",
        "method",
        "created_at",
        "historical_balance_after",
        "current_balance",
        "current_debt_status",
        "shop_display_name",
    }
    assert "recorded_by_user_id" not in receipt_fields
    assert "payment_id" not in receipt_fields


def test_history_is_revision_ordered_and_defends_corrupt_duplicate_revision() -> None:
    first = Payment(
        id=UUID("44444444-4444-4444-8444-444444444444"),
        debt_id=UUID("11111111-1111-4111-8111-111111111111"),
        recorded_by_user_id=UUID("33333333-3333-4333-8333-333333333333"),
        amount_uzs=Decimal("200"),
        method=PaymentMethod.CASH.value,
        debt_revision_after=2,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    duplicate = Payment(
        id=UUID("55555555-5555-4555-8555-555555555555"),
        debt_id=first.debt_id,
        recorded_by_user_id=first.recorded_by_user_id,
        amount_uzs=Decimal("700"),
        method=PaymentMethod.CARD.value,
        debt_revision_after=2,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    debt = _debt(status=DebtStatus.ACTIVE, due_date=date(2026, 1, 2))
    rows = (
        ScopedPaymentRow(payment=first, debt=debt, shop_name="Shop"),
        ScopedPaymentRow(payment=duplicate, debt=debt, shop_name="Shop"),
    )
    with pytest.raises(RuntimeError, match="strictly increasing"):
        _history(rows)


def test_detached_tenant_read_has_no_csrf_or_locking_surface() -> None:
    actor = DetachedPaymentReadActorContext(
        actor_user_id=UUID("11111111-1111-4111-8111-111111111111"),
        current_shop_id=UUID("22222222-2222-4222-8222-222222222222"),
        role_hint=ShopRole.CASHIER,
        language=DebtWebLanguage.UZ_LATN,
    )
    assert "11111111" not in repr(actor)
    assert "csrf" not in getsource(get_detached_current_shop_payment_read_actor_context)
    assert "with_for_update" not in getsource(resolve_tenant_payment_read_authority)


@pytest.mark.parametrize(
    "result_type",
    (TenantPaymentReadResult, CustomerPaymentReadResult),
)
def test_failed_read_results_cannot_carry_history(result_type) -> None:
    with pytest.raises(ValueError, match="must not carry data"):
        result_type(
            error=ErrorCode.PAYMENT_UNAVAILABLE,
            history=(object(),),  # type: ignore[arg-type]
        )


def test_read_service_has_no_lock_write_or_payment_detail_vocabulary() -> None:
    source = getsource(payment_read_service)

    for forbidden in (
        ".with_for_update",
        "session.add(",
        "session.flush(",
        "session.commit(",
        "session.rollback(",
        "card_number",
        "bank_reference",
        "terminal_id",
        "idempotency_key",
        "key_digest",
    ):
        assert forbidden not in source
