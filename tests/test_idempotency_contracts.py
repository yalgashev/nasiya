import hashlib
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.debt.values import (
    DebtId,
    DiscountBasisPoints,
    OriginalAmountUZS,
    ShopCustomerId,
    ShopId,
    UserId,
)
from app.idempotency.contracts import (
    CanonicalIdempotencyKey,
    CompletedIdempotencyResult,
    CreatePaymentRequestHash,
    IdempotencyEndpoint,
    IdempotencyKeyDigest,
    IdempotencyOutcome,
    IdempotencyResolution,
    IdempotencyResultType,
    canonical_idempotency_key_digest,
    create_debt_request_hash,
    parse_idempotency_key,
    require_matching_idempotency_keys,
)

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
SHOP_ID = UUID("22222222-2222-4222-8222-222222222222")
SHOP_CUSTOMER_ID = UUID("33333333-3333-4333-8333-333333333333")


def _create_hash(
    *,
    actor_id: UUID = ACTOR_ID,
    shop_id: UUID = SHOP_ID,
    shop_customer_id: UUID = SHOP_CUSTOMER_ID,
    due_date: date = date(2026, 5, 4),
    amount: str = "1000",
    basis_points: int = 100,
):
    return create_debt_request_hash(
        actor_user_id=UserId(actor_id),
        shop_id=ShopId(shop_id),
        shop_customer_id=ShopCustomerId(shop_customer_id),
        original_amount=OriginalAmountUZS(Decimal(amount)),
        discount_basis_points=DiscountBasisPoints(basis_points),
        due_date=due_date,
    )


def test_key_is_canonical_uuid_redacted_and_form_header_must_match() -> None:
    raw_key = uuid4()
    canonical = str(raw_key)
    parsed = parse_idempotency_key(canonical)

    assert parsed.as_uuid() == raw_key
    assert str(raw_key) not in repr(parsed)
    assert str(raw_key) not in str(parsed)
    assert (
        require_matching_idempotency_keys(form_value=canonical, header_value=canonical)
        == parsed
    )
    assert (
        require_matching_idempotency_keys(form_value=canonical, header_value=None)
        == parsed
    )

    for malformed in (str(raw_key).upper(), raw_key.hex, "not-a-uuid", "", None):
        with pytest.raises(ValueError, match="Idempotency key is invalid"):
            parse_idempotency_key(malformed)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="do not match"):
        require_matching_idempotency_keys(
            form_value=canonical, header_value=str(uuid4())
        )


def test_key_digest_and_request_hash_are_sha256_and_collision_safe() -> None:
    key = CanonicalIdempotencyKey(uuid4())
    digest = canonical_idempotency_key_digest(key)
    assert (
        digest.value == hashlib.sha256(str(key.as_uuid()).encode("ascii")).hexdigest()
    )
    assert digest.value not in repr(digest)
    assert digest.value == canonical_idempotency_key_digest(key).value

    hash_one = _create_hash()
    assert hash_one == _create_hash()
    assert hash_one.value not in repr(hash_one)
    changed_hashes = {
        _create_hash(actor_id=uuid4()),
        _create_hash(shop_id=uuid4()),
        _create_hash(shop_customer_id=uuid4()),
        _create_hash(due_date=date(2026, 5, 5)),
        _create_hash(amount="1001"),
        _create_hash(basis_points=101),
    }
    assert len(changed_hashes) == 6
    assert hash_one not in changed_hashes

    for malformed in ("A" * 64, "a" * 63, "g" * 64, None):
        with pytest.raises(ValueError, match="lowercase SHA-256"):
            IdempotencyKeyDigest(malformed)  # type: ignore[arg-type]


def test_new_replay_and_conflict_outcomes_do_not_leak_key_or_debt_identifier() -> None:
    result = CompletedIdempotencyResult(
        result_type=IdempotencyResultType.DEBT,
        debt_id=DebtId(uuid4()),
        completed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert IdempotencyResolution.new().outcome is IdempotencyOutcome.NEW
    assert IdempotencyResolution.replay(result).completed_result is result
    assert IdempotencyResolution.conflict().outcome is IdempotencyOutcome.CONFLICT
    assert str(result.debt_id.as_uuid()) not in repr(result)
    plus_five = timezone(timedelta(hours=5))
    normalized = CompletedIdempotencyResult(
        result_type=IdempotencyResultType.DEBT,
        debt_id=DebtId(uuid4()),
        completed_at=datetime(2026, 5, 1, 5, tzinfo=plus_five),
    )
    assert normalized.completed_at == datetime(2026, 5, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="replay requires"):
        IdempotencyResolution(outcome=IdempotencyOutcome.REPLAY)
    with pytest.raises(ValueError, match="Only idempotency replay"):
        IdempotencyResolution(outcome=IdempotencyOutcome.NEW, completed_result=result)


def test_m16_endpoint_result_vocabularies_and_generic_result_are_exact() -> None:
    assert tuple(IdempotencyEndpoint) == (
        IdempotencyEndpoint.SHOP_DEBTS_CREATE,
        IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
        IdempotencyEndpoint.SHOP_RISK_BAND_DISCLOSURES_CREATE,
        IdempotencyEndpoint.ADMIN_DEBTS_WRITE_OFF,
    )
    assert tuple(IdempotencyResultType) == (
        IdempotencyResultType.DEBT,
        IdempotencyResultType.PAYMENT,
        IdempotencyResultType.DISCLOSURE_VIEW,
    )

    result_id = uuid4()
    payment_result = CompletedIdempotencyResult(
        result_type=IdempotencyResultType.PAYMENT,
        result_object_id=result_id,
        completed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert (
        payment_result.require_result_object_id(
            expected_type=IdempotencyResultType.PAYMENT
        )
        == result_id
    )
    assert str(result_id) not in repr(payment_result)
    disclosure_result = CompletedIdempotencyResult(
        result_type=IdempotencyResultType.DISCLOSURE_VIEW,
        result_object_id=result_id,
        completed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert (
        disclosure_result.require_result_object_id(
            expected_type=IdempotencyResultType.DISCLOSURE_VIEW
        )
        == result_id
    )
    with pytest.raises(ValueError, match="does not match accessor"):
        _ = payment_result.debt_id

    for malformed in ("shop.payments.create", "", None):
        with pytest.raises((TypeError, ValueError)):
            IdempotencyEndpoint(malformed)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="result type is invalid"):
        CompletedIdempotencyResult(
            result_type="payment",  # type: ignore[arg-type]
            result_object_id=result_id,
            completed_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
    debt_result = CompletedIdempotencyResult(
        result_type=IdempotencyResultType.DEBT,
        debt_id=DebtId(uuid4()),
        completed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="does not match accessor"):
        debt_result.require_result_object_id(
            expected_type=IdempotencyResultType.PAYMENT
        )

    request_hash = CreatePaymentRequestHash("a" * 64)
    assert request_hash.value not in repr(request_hash)
