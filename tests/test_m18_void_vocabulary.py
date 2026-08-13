from app.auth.error_codes import ERROR_CATALOG, ErrorCode, get_error_http_status
from app.debt.enums import DebtOverdueSource
from app.payment.enums import PaymentVoidOutcome, PaymentVoidReason
from app.rating.enums import (
    RatingEventType,
    RatingRecordingSource,
    rating_event_allowed_recording_sources,
    rating_event_delta,
)


def test_m18_forward_vocabulary_is_exact_and_future_vocabulary_is_absent() -> None:
    assert {reason.value for reason in PaymentVoidReason} == {
        "duplicate_payment",
        "incorrect_amount",
        "incorrect_method",
        "payment_not_received",
        "wrong_debt",
    }
    assert {outcome.value for outcome in PaymentVoidOutcome} == {"new", "replay"}
    assert {
        DebtOverdueSource.INLINE_PAYMENT.value,
        DebtOverdueSource.BATCH.value,
        DebtOverdueSource.PAYMENT_VOID.value,
    } == {"inline_payment", "batch", "payment_void"}

    values = {event_type.value for event_type in RatingEventType}
    assert values == {
        "on_time_paid",
        "overdue",
        "written_off",
        "written_off_settled",
        "on_time_paid_voided",
        "written_off_settled_voided",
    }
    assert {
        RatingEventType.ON_TIME_PAID_VOIDED: rating_event_delta(
            RatingEventType.ON_TIME_PAID_VOIDED
        ),
        RatingEventType.WRITTEN_OFF_SETTLED_VOIDED: rating_event_delta(
            RatingEventType.WRITTEN_OFF_SETTLED_VOIDED
        ),
    } == {
        RatingEventType.ON_TIME_PAID_VOIDED: -5,
        RatingEventType.WRITTEN_OFF_SETTLED_VOIDED: -10,
    }
    for event_type in (
        RatingEventType.ON_TIME_PAID_VOIDED,
        RatingEventType.WRITTEN_OFF_SETTLED_VOIDED,
    ):
        assert rating_event_allowed_recording_sources(event_type) == frozenset(
            {RatingRecordingSource.LIVE}
        )

    assert not values & {
        "refund",
        "unvoid",
        "reversal",
        "correction",
        "override",
        "overdue_voided",
        "written_off_voided",
    }


def test_m18_payment_not_voidable_is_a_stable_generic_conflict() -> None:
    assert ErrorCode.PAYMENT_NOT_VOIDABLE.value == "PAYMENT_NOT_VOIDABLE"
    assert ErrorCode.PAYMENT_NOT_VOIDABLE in ERROR_CATALOG
    assert get_error_http_status(ErrorCode.PAYMENT_NOT_VOIDABLE) == 409


def test_m18_payment_void_has_no_generic_overdue_source_sibling() -> None:
    assert {source.value for source in DebtOverdueSource} == {
        "inline_payment",
        "batch",
        "payment_void",
    }
