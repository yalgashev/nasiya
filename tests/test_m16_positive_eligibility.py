from dataclasses import fields, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.debt.enums import DebtStatus
from app.debt.values import (
    MAX_DEBT_AMOUNT_UZS,
    DebtRevision,
    OriginalAmountUZS,
)
from app.payment.values import PaymentAmountUZS, RemainingDueUZS
from app.rating.eligibility import (
    MIN_ON_TIME_RATING_ORIGINAL_AMOUNT_UZS,
    OnTimePaidEligibilityFacts,
    evaluate_on_time_paid_eligibility,
)
from app.rating.enums import PositiveRatingDecision
from app.shop_customer.values import ShopCustomerId


def _facts(**changes: object) -> OnTimePaidEligibilityFacts:
    values = {
        "shop_customer_id": ShopCustomerId(UUID(int=1)),
        "pre_status": DebtStatus.ACTIVE,
        "post_status": DebtStatus.PAID,
        "payment_amount": PaymentAmountUZS(Decimal("1000")),
        "discounted_remaining": RemainingDueUZS(Decimal("1000")),
        "original_amount": OriginalAmountUZS(Decimal("100000")),
        "accepted_at": datetime(2026, 5, 1, 10, tzinfo=UTC),
        "payment_created_at": datetime(2026, 5, 2, 10, tzinfo=UTC),
        "due_date": date(2026, 5, 2),
    }
    return OnTimePaidEligibilityFacts(**(values | changes))


def test_exact_threshold_and_due_date_boundary_award_one_bonus() -> None:
    result = evaluate_on_time_paid_eligibility(_facts())

    assert MIN_ON_TIME_RATING_ORIGINAL_AMOUNT_UZS == Decimal("100000")
    assert result.decision is PositiveRatingDecision.AWARD
    assert result.awards_bonus
    assert result.cap_key is not None
    assert result.cap_key.business_date == date(2026, 5, 2)
    assert "100000" not in repr(result)

    maximum = evaluate_on_time_paid_eligibility(
        _facts(original_amount=OriginalAmountUZS(MAX_DEBT_AMOUNT_UZS))
    )
    assert maximum.awards_bonus


@pytest.mark.parametrize(
    "changes",
    (
        {"payment_amount": PaymentAmountUZS(Decimal("999"))},
        {"original_amount": OriginalAmountUZS(Decimal("99999"))},
        {"accepted_at": datetime(2026, 5, 2, 1, tzinfo=UTC)},
        {"due_date": date(2026, 5, 1)},
        {"overdue_at": datetime(2026, 5, 2, 9, tzinfo=UTC)},
        {"overdue_revision": DebtRevision(2)},
        {"is_completed_replay": True},
        {"pre_status": DebtStatus.PAID},
        {"post_status": DebtStatus.ACTIVE},
    ),
)
def test_partial_threshold_same_day_late_marker_replay_and_state_matrix_get_no_bonus(
    changes: dict[str, object],
) -> None:
    result = evaluate_on_time_paid_eligibility(_facts(**changes))

    assert result.decision is PositiveRatingDecision.NO_BONUS
    assert not result.awards_bonus
    assert result.cap_key is None


def test_daily_cap_loser_keeps_a_non_failure_no_bonus_result() -> None:
    result = evaluate_on_time_paid_eligibility(_facts(daily_cap_already_used=True))

    assert result.decision is PositiveRatingDecision.DAILY_CAP_ALREADY_USED
    assert not result.awards_bonus
    assert result.cap_key is not None
    assert {field.name for field in fields(result)} == {"decision", "cap_key"}
    assert not hasattr(result, "error")
    assert not hasattr(result, "payment_denied")


def test_daily_cap_key_is_per_shop_customer_pair_and_payment_business_date() -> None:
    first = evaluate_on_time_paid_eligibility(_facts())
    other_pair = evaluate_on_time_paid_eligibility(
        replace(
            _facts(),
            shop_customer_id=ShopCustomerId(UUID(int=2)),
        )
    )
    next_day = evaluate_on_time_paid_eligibility(
        replace(
            _facts(),
            payment_created_at=datetime(2026, 5, 3, 10, tzinfo=UTC),
            due_date=date(2026, 5, 3),
        )
    )

    assert first.cap_key != other_pair.cap_key
    assert first.cap_key != next_day.cap_key
    assert first.cap_key == evaluate_on_time_paid_eligibility(_facts()).cap_key


def test_eligibility_contract_has_no_client_or_request_clock() -> None:
    field_names = {field.name for field in fields(OnTimePaidEligibilityFacts)}

    assert {"accepted_at", "payment_created_at", "due_date"} <= field_names
    assert field_names.isdisjoint(
        {
            "now",
            "client_now",
            "request_now",
            "created_at",
            "shop_id",
            "customer_id",
        }
    )
    with pytest.raises(ValueError, match="Payment created at"):
        _facts(payment_created_at=datetime(2026, 5, 2, 10))
