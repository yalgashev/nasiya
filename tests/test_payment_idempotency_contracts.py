import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.debt.values import DebtId, DebtRevision, ShopId, UserId
from app.idempotency.contracts import (
    CompletedIdempotencyResult,
    IdempotencyResultType,
)
from app.payment.contracts import (
    create_payment_request_hash,
    payment_id_from_completed_result,
)
from app.payment.enums import PaymentMethod
from app.payment.values import PaymentAmountUZS

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
SHOP_ID = UUID("22222222-2222-4222-8222-222222222222")
DEBT_ID = UUID("33333333-3333-4333-8333-333333333333")


def _payment_hash(
    *,
    actor_id: UUID = ACTOR_ID,
    shop_id: UUID = SHOP_ID,
    debt_id: UUID = DEBT_ID,
    amount: str = "1000",
    method: PaymentMethod = PaymentMethod.CASH,
    revision: int = 2,
):
    return create_payment_request_hash(
        actor_user_id=UserId(actor_id),
        shop_id=ShopId(shop_id),
        debt_id=DebtId(debt_id),
        amount=PaymentAmountUZS(Decimal(amount)),
        method=method,
        expected_revision=DebtRevision(revision),
    )


def test_create_payment_hash_is_deterministic_redacted_and_complete() -> None:
    baseline = _payment_hash()

    assert baseline == _payment_hash()
    assert baseline.value not in repr(baseline)
    changed_hashes = {
        _payment_hash(actor_id=uuid4()),
        _payment_hash(shop_id=uuid4()),
        _payment_hash(debt_id=uuid4()),
        _payment_hash(amount="1001"),
        _payment_hash(method=PaymentMethod.CARD),
        _payment_hash(revision=3),
    }
    assert len(changed_hashes) == 6
    assert baseline not in changed_hashes

    parts = (
        b"nasiya.m14.create-payment.request.v1",
        ACTOR_ID.bytes,
        SHOP_ID.bytes,
        DEBT_ID.bytes,
        b"1000",
        b"cash",
        b"2",
    )
    encoded = b"".join(len(part).to_bytes(4, byteorder="big") + part for part in parts)
    assert baseline.value == hashlib.sha256(encoded).hexdigest()


def test_completed_payment_result_has_one_typed_redacted_accessor() -> None:
    result_id = uuid4()
    payment_result = CompletedIdempotencyResult(
        result_type=IdempotencyResultType.PAYMENT,
        result_object_id=result_id,
        completed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert payment_id_from_completed_result(payment_result).as_uuid() == result_id
    assert str(result_id) not in repr(payment_result)
    with pytest.raises(ValueError, match="does not match accessor"):
        payment_id_from_completed_result(
            CompletedIdempotencyResult(
                result_type=IdempotencyResultType.DEBT,
                result_object_id=uuid4(),
                completed_at=datetime(2026, 5, 1, tzinfo=UTC),
            )
        )


def test_payment_hash_rejects_untyped_fields() -> None:
    with pytest.raises(ValueError, match="Payment method"):
        create_payment_request_hash(
            actor_user_id=UserId(ACTOR_ID),
            shop_id=ShopId(SHOP_ID),
            debt_id=DebtId(DEBT_ID),
            amount=PaymentAmountUZS(Decimal("1000")),
            method="cash",  # type: ignore[arg-type]
            expected_revision=DebtRevision(2),
        )
