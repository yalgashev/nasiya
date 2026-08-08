from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.idempotency.contracts import (
    CreateDebtRequestHash,
    CreatePaymentRequestHash,
    IdempotencyEndpoint,
    IdempotencyResultType,
)
from app.idempotency.models import IdempotencyKey
from app.idempotency.repository import (
    _result_type_for_request,
    completed_idempotency_result_from_row,
)


def _row(*, endpoint: str, result_type: str) -> IdempotencyKey:
    return IdempotencyKey(
        id=uuid4(),
        actor_user_id=uuid4(),
        endpoint=endpoint,
        key_digest="a" * 64,
        request_hash="b" * 64,
        result_object_type=result_type,
        result_object_id=uuid4(),
        created_at=datetime(2026, 8, 9, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("endpoint", "result_type"),
    (
        ("shop.debts.create", "debt"),
        ("shop.debt_payments.create", "payment"),
    ),
)
def test_completed_row_mapper_preserves_the_typed_result_pair(
    endpoint: str, result_type: str
) -> None:
    row = _row(endpoint=endpoint, result_type=result_type)

    completed = completed_idempotency_result_from_row(row)

    assert completed.result_type.value == result_type
    assert completed.result_object_id == row.result_object_id
    assert completed.completed_at == row.created_at


def test_completed_row_mapper_fails_closed_for_unknown_result_type() -> None:
    with pytest.raises(ValueError):
        completed_idempotency_result_from_row(
            _row(endpoint="shop.debts.create", result_type="unknown")
        )


@pytest.mark.parametrize(
    ("endpoint", "request_hash", "expected"),
    (
        (
            IdempotencyEndpoint.SHOP_DEBTS_CREATE,
            CreateDebtRequestHash("a" * 64),
            IdempotencyResultType.DEBT,
        ),
        (
            IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
            CreatePaymentRequestHash("b" * 64),
            IdempotencyResultType.PAYMENT,
        ),
    ),
)
def test_insert_contract_selects_only_the_lawful_endpoint_result_pair(
    endpoint: IdempotencyEndpoint,
    request_hash: CreateDebtRequestHash | CreatePaymentRequestHash,
    expected: IdempotencyResultType,
) -> None:
    assert (
        _result_type_for_request(endpoint=endpoint, request_hash=request_hash)
        is expected
    )


@pytest.mark.parametrize(
    ("endpoint", "request_hash"),
    (
        (
            IdempotencyEndpoint.SHOP_DEBTS_CREATE,
            CreatePaymentRequestHash("a" * 64),
        ),
        (
            IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
            CreateDebtRequestHash("b" * 64),
        ),
    ),
)
def test_insert_contract_rejects_crossed_endpoint_result_pairs(
    endpoint: IdempotencyEndpoint,
    request_hash: CreateDebtRequestHash | CreatePaymentRequestHash,
) -> None:
    with pytest.raises(TypeError, match="request_hash"):
        _result_type_for_request(endpoint=endpoint, request_hash=request_hash)
