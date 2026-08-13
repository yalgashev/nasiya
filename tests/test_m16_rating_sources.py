from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from inspect import getsource
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.debt.rating_ports import (
    LockedOverdueRatingAppendPort,
    OverdueRatingAppendOutcome,
    PendingOverdueRatingEffect,
)
from app.debt.values import CustomerId, DebtId
from app.payment.rating_ports import (
    LockedPaymentRatingAppendPort,
    PaymentRatingAppendOutcome,
    PendingOnTimePaidRatingEffect,
)
from app.rating.contracts import (
    RatingEventAppendResult,
    create_on_time_paid_rating_event,
    create_overdue_rating_event,
)
from app.rating.enums import (
    RatingEventAppendOutcome,
    RatingEventType,
    RatingRecordingSource,
    rating_event_delta,
)
from app.rating.ports import (
    LockedRatingCustomerScope,
    RatingEventAppendError,
    RatingEventReadPort,
    RatingEventWriterPort,
    validate_locked_rating_customer_scope,
)
from app.rating.values import RatingEventId
from app.shop_customer.values import ShopCustomerId

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source_event(
    event_type: RatingEventType,
    source: RatingRecordingSource,
):
    common = {
        "event_id": RatingEventId(UUID(int=1)),
        "shop_customer_id": ShopCustomerId(UUID(int=2)),
        "debt_id": DebtId(UUID(int=3)),
        "recording_source": source,
    }
    occurred_at = datetime(2026, 5, 1, 19, tzinfo=UTC)
    if event_type is RatingEventType.ON_TIME_PAID:
        return create_on_time_paid_rating_event(
            **common,
            payment_created_at=occurred_at,
        )
    return create_overdue_rating_event(
        **common,
        overdue_at=occurred_at,
    )


@pytest.mark.parametrize(
    "event_type",
    (RatingEventType.ON_TIME_PAID, RatingEventType.OVERDUE),
)
@pytest.mark.parametrize("source", tuple(RatingRecordingSource))
def test_factories_allow_only_exact_source_event_matrix(
    event_type: RatingEventType,
    source: RatingRecordingSource,
) -> None:
    event = _source_event(event_type, source)

    assert event.event_type is event_type
    assert event.recording_source is source
    assert event.delta == rating_event_delta(event_type)
    assert event.business_date == date(2026, 5, 2)
    assert event.source_key == (event.debt_id, event_type)


def test_rating_event_is_immutable_and_fully_redacted() -> None:
    event = _source_event(
        RatingEventType.ON_TIME_PAID,
        RatingRecordingSource.LIVE,
    )

    with pytest.raises(FrozenInstanceError):
        event.delta = -15  # type: ignore[misc]
    representation = repr(event)
    for secret in (
        str(event.id.as_uuid()),
        str(event.debt_id.as_uuid()),
        str(event.shop_customer_id.as_uuid()),
        "on_time_paid",
        "2026",
    ):
        assert secret not in representation


def test_customer_lock_scope_enforces_exact_session_ownership() -> None:
    owner_session = Session()
    other_session = Session()
    token = LockedRatingCustomerScope(
        customer_id=CustomerId(uuid4()),
        _session=owner_session,
    )

    assert validate_locked_rating_customer_scope(owner_session, token) is token
    with pytest.raises(RuntimeError, match="another session"):
        validate_locked_rating_customer_scope(other_session, token)
    with pytest.raises(TypeError, match="lock token"):
        validate_locked_rating_customer_scope(owner_session, object())
    assert "CustomerId" not in repr(token)


def test_append_and_read_ports_are_narrow_structural_protocols() -> None:
    class Writer:
        def append_locked_event(
            self,
            session,
            *,
            locked_customer,
            event,
        ):
            return RatingEventAppendResult(outcome=RatingEventAppendOutcome.APPENDED)

    class Reader:
        def read_ordered_locked_events(
            self,
            session,
            *,
            locked_customer,
        ):
            return ()

    assert isinstance(Writer(), RatingEventWriterPort)
    assert isinstance(Reader(), RatingEventReadPort)
    for protocol in (
        RatingEventWriterPort,
        RatingEventReadPort,
        LockedOverdueRatingAppendPort,
        LockedPaymentRatingAppendPort,
    ):
        for forbidden_method in (
            "update",
            "delete",
            "reverse",
            "commit",
            "rollback",
            "close",
        ):
            assert not hasattr(protocol, forbidden_method)


def test_pending_payment_and_overdue_effects_are_typed_and_redacted() -> None:
    debt_id = DebtId(uuid4())
    shop_customer_id = ShopCustomerId(uuid4())
    occurred_at = datetime(2026, 5, 1, 19, tzinfo=UTC)
    overdue = PendingOverdueRatingEffect(
        event_id=uuid4(),
        debt_id=debt_id,
        shop_customer_id=shop_customer_id,
        overdue_at=occurred_at,
    )
    on_time = PendingOnTimePaidRatingEffect(
        event_id=uuid4(),
        debt_id=debt_id,
        shop_customer_id=shop_customer_id,
        payment_created_at=occurred_at,
        payment_business_date=date(2026, 5, 2),
    )

    assert overdue.overdue_at == occurred_at
    assert on_time.payment_business_date == date(2026, 5, 2)
    assert "2026" not in repr(overdue)
    assert str(debt_id.as_uuid()) not in repr(on_time)
    with pytest.raises(ValueError, match="Tashkent payment date"):
        PendingOnTimePaidRatingEffect(
            event_id=uuid4(),
            debt_id=debt_id,
            shop_customer_id=shop_customer_id,
            payment_created_at=occurred_at,
            payment_business_date=date(2026, 5, 1),
        )


def test_append_outcomes_and_faults_expose_no_source_identifiers() -> None:
    assert tuple(RatingEventAppendOutcome) == (
        RatingEventAppendOutcome.APPENDED,
        RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS,
        RatingEventAppendOutcome.POSITIVE_DAILY_CAP_ALREADY_USED,
    )
    assert tuple(OverdueRatingAppendOutcome) == (
        OverdueRatingAppendOutcome.APPENDED,
        OverdueRatingAppendOutcome.SOURCE_ALREADY_EXISTS,
    )
    assert tuple(PaymentRatingAppendOutcome) == (
        PaymentRatingAppendOutcome.APPENDED,
        PaymentRatingAppendOutcome.DAILY_CAP_ALREADY_USED,
        PaymentRatingAppendOutcome.SOURCE_ALREADY_EXISTS,
    )
    failure = RatingEventAppendError()
    assert str(failure) == "Rating event append failed"
    assert "UUID" not in repr(failure)


def test_debt_and_payment_local_ports_do_not_import_concrete_rating_code() -> None:
    local_sources = "\n".join(
        (PROJECT_ROOT / path).read_text(encoding="utf-8")
        for path in (
            "app/debt/rating_ports.py",
            "app/payment/rating_ports.py",
        )
    )

    assert "from app.rating" not in local_sources
    assert "import app.rating" not in local_sources
    assert "models" not in local_sources
    assert "repository" not in local_sources
    assert "append_pending_overdue" in getsource(LockedOverdueRatingAppendPort)
    assert "append_pending_on_time_paid" in getsource(LockedPaymentRatingAppendPort)
