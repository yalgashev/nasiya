from dataclasses import replace
from datetime import UTC, datetime, timedelta
from inspect import getsource
from pathlib import Path
from uuid import UUID

import pytest

from app.debt.enums import DebtStatus
from app.debt.values import DebtId, DebtRevision
from app.payment.rating_ports import (
    PaymentRatingPositiveSource,
    PaymentVoidRatingAppendPort,
    PaymentVoidRatingSourceReadPort,
    PreTransitionRatingSourceToken,
)
from app.payment.values import PaymentId
from app.rating.contracts import (
    RatingCompensationSourceProof,
    RatingEvent,
    RatingEventOrderKey,
    create_on_time_paid_rating_event,
    create_overdue_rating_event,
    create_rating_compensation_event,
    create_written_off_rating_event,
    create_written_off_settled_rating_event,
)
from app.rating.enums import RatingEventType, RatingRecordingSource
from app.rating.service import IncoherentRatingHistoryError, derive_rating_snapshot
from app.rating.values import RatingEventId
from app.shop_customer.values import ShopCustomerId

ROOT = Path(__file__).resolve().parents[1]
PAIR = ShopCustomerId(UUID(int=2))
DEBT = DebtId(UUID(int=3))
BASE = datetime(2026, 8, 13, tzinfo=UTC)


def _positive(
    event_type: RatingEventType,
    revision: int,
    *,
    occurred_at: datetime,
) -> RatingEvent:
    common = {
        "event_id": RatingEventId(UUID(int=10_000 + revision)),
        "shop_customer_id": PAIR,
        "debt_id": DEBT,
        "source_revision": DebtRevision(revision),
    }
    if event_type is RatingEventType.ON_TIME_PAID:
        return create_on_time_paid_rating_event(
            **common,
            payment_created_at=occurred_at,
            recording_source=RatingRecordingSource.LIVE,
        )
    if event_type is RatingEventType.OVERDUE:
        return create_overdue_rating_event(
            **common,
            overdue_at=occurred_at,
            recording_source=RatingRecordingSource.LIVE,
        )
    if event_type is RatingEventType.WRITTEN_OFF:
        return create_written_off_rating_event(
            **common,
            written_off_at=occurred_at,
        )
    return create_written_off_settled_rating_event(
        **common,
        written_off_settled_at=occurred_at,
    )


def _proof(positive: RatingEvent) -> RatingCompensationSourceProof:
    audit_type = {
        RatingEventType.ON_TIME_PAID: "debt.paid",
        RatingEventType.WRITTEN_OFF_SETTLED: "debt.written_off_settled",
    }.get(positive.event_type, "debt.paid")
    return RatingCompensationSourceProof(
        payment_id=UUID(int=100 + positive.source_revision.value),
        payment_debt_id=positive.debt_id,
        payment_shop_customer_id=positive.shop_customer_id,
        payment_revision=positive.source_revision,
        payment_created_at=positive.occurred_at,
        positive_event=positive,
        audit_event_type=audit_type,
        audit_debt_id=positive.debt_id,
        audit_revision=positive.source_revision,
        audit_occurred_at=positive.occurred_at,
    )


def _compensate(positive: RatingEvent, *, minute: int) -> RatingEvent:
    return create_rating_compensation_event(
        event_id=RatingEventId(UUID(int=20_000 + positive.source_revision.value)),
        source=_proof(positive),
        voided_at=BASE + timedelta(minutes=minute),
    )


def test_every_event_has_positive_source_revision_and_exact_four_field_order() -> None:
    first = RatingEventOrderKey(
        occurred_at=BASE,
        debt_id=DEBT,
        event_type=RatingEventType.ON_TIME_PAID,
        source_revision=DebtRevision(9),
    )
    later_revision = replace(first, source_revision=DebtRevision(10))
    later_lexical_type = replace(
        first,
        event_type=RatingEventType.ON_TIME_PAID_VOIDED,
        source_revision=DebtRevision(1),
    )

    assert len(first.as_sort_key()) == 4
    assert first.as_sort_key() < later_revision.as_sort_key()
    assert later_revision.as_sort_key() < later_lexical_type.as_sort_key()
    with pytest.raises(ValueError):
        RatingEventOrderKey(
            occurred_at=BASE,
            debt_id=DEBT,
            event_type=RatingEventType.ON_TIME_PAID,
            source_revision=0,  # type: ignore[arg-type]
        )


def test_compensation_factory_requires_exact_payment_event_audit_chain() -> None:
    positive = _positive(RatingEventType.ON_TIME_PAID, 4, occurred_at=BASE)
    compensation = _compensate(positive, minute=1)

    assert compensation.event_type is RatingEventType.ON_TIME_PAID_VOIDED
    assert compensation.delta == -5
    assert compensation.source_revision == positive.source_revision
    assert compensation.debt_id == positive.debt_id
    assert compensation.shop_customer_id == positive.shop_customer_id
    assert "UUID" not in repr(_proof(positive))

    valid = _proof(positive)
    corruptions = (
        {"payment_revision": DebtRevision(5)},
        {"payment_debt_id": DebtId(UUID(int=99))},
        {"payment_shop_customer_id": ShopCustomerId(UUID(int=99))},
        {"audit_event_type": "payment.recorded"},
        {"audit_debt_id": DebtId(UUID(int=99))},
        {"audit_revision": DebtRevision(5)},
        {"audit_occurred_at": BASE + timedelta(seconds=1)},
    )
    for changed in corruptions:
        with pytest.raises(ValueError, match="incoherent"):
            replace(valid, **changed)


def test_lawful_multi_cycle_sequence_and_per_event_clamp() -> None:
    first = _positive(RatingEventType.ON_TIME_PAID, 3, occurred_at=BASE)
    first_void = _compensate(first, minute=1)
    second = _positive(
        RatingEventType.ON_TIME_PAID,
        5,
        occurred_at=BASE + timedelta(days=1),
    )
    second_void = _compensate(second, minute=24 * 60 + 1)
    overdue = _positive(
        RatingEventType.OVERDUE,
        7,
        occurred_at=BASE + timedelta(days=2),
    )
    written_off = _positive(
        RatingEventType.WRITTEN_OFF,
        8,
        occurred_at=BASE + timedelta(days=3),
    )
    settled = _positive(
        RatingEventType.WRITTEN_OFF_SETTLED,
        9,
        occurred_at=BASE + timedelta(days=4),
    )
    settled_void = _compensate(settled, minute=5 * 24 * 60)
    resettled = _positive(
        RatingEventType.WRITTEN_OFF_SETTLED,
        11,
        occurred_at=BASE + timedelta(days=6),
    )

    history = (
        resettled,
        first_void,
        written_off,
        second,
        settled_void,
        first,
        overdue,
        settled,
        second_void,
    )
    snapshot = derive_rating_snapshot(history, global_hard_block=False)

    assert snapshot.event_count == 9
    assert snapshot.current_score == 15


@pytest.mark.parametrize(
    "history",
    (
        lambda positive, negative: (negative,),
        lambda positive, negative: (positive, negative, negative),
        lambda positive, negative: (
            positive,
            replace(negative, source_revision=DebtRevision(99)),
        ),
    ),
)
def test_compensation_before_positive_duplicate_or_wrong_revision_fails_closed(
    history: object,
) -> None:
    positive = _positive(RatingEventType.ON_TIME_PAID, 3, occurred_at=BASE)
    negative = _compensate(positive, minute=1)

    with pytest.raises(IncoherentRatingHistoryError):
        derive_rating_snapshot(
            history(positive, negative),  # type: ignore[operator]
            global_hard_block=False,
        )


def test_no_compensation_for_cap_loser_partial_late_or_noncompensable_events() -> None:
    for event_type in (RatingEventType.OVERDUE, RatingEventType.WRITTEN_OFF):
        source = _positive(event_type, 4, occurred_at=BASE)
        with pytest.raises(ValueError, match="not compensable"):
            _proof(source)

    assert "NO_BONUS" not in getsource(create_rating_compensation_event)
    assert "DAILY_CAP_ALREADY_USED" not in getsource(create_rating_compensation_event)
    assert RatingEventType.OVERDUE.value not in {
        RatingEventType.ON_TIME_PAID_VOIDED.value,
        RatingEventType.WRITTEN_OFF_SETTLED_VOIDED.value,
    }


def test_pre_transition_token_is_closed_to_current_terminal_marker() -> None:
    paid = PreTransitionRatingSourceToken(
        payment_id=PaymentId(UUID(int=1)),
        debt_id=DEBT,
        shop_customer_id=PAIR,
        positive_source=PaymentRatingPositiveSource.ON_TIME_PAID,
        terminal_status=DebtStatus.PAID,
        source_revision=DebtRevision(4),
        source_occurred_at=BASE,
    )
    settled = replace(
        paid,
        positive_source=PaymentRatingPositiveSource.WRITTEN_OFF_SETTLED,
        terminal_status=DebtStatus.WRITTEN_OFF_SETTLED,
    )

    assert paid.source_revision == DebtRevision(4)
    assert settled.terminal_status is DebtStatus.WRITTEN_OFF_SETTLED
    assert repr(paid) == "PreTransitionRatingSourceToken(<redacted>)"
    with pytest.raises(ValueError, match="marker is incoherent"):
        replace(paid, terminal_status=DebtStatus.ACTIVE)


def test_local_structural_ports_have_no_inverse_import_or_lock_default() -> None:
    class Reader:
        def read_pre_transition_source(self, session, **kwargs):
            return None

    class Writer:
        def append_source_compensation(self, session, **kwargs):
            raise RuntimeError

    assert isinstance(Reader(), PaymentVoidRatingSourceReadPort)
    assert isinstance(Writer(), PaymentVoidRatingAppendPort)
    local_source = (ROOT / "app/payment/rating_ports.py").read_text(encoding="utf-8")
    for forbidden in (
        "app.rating.models",
        "app.rating.repository",
        "NoOp",
        "lock_customer",
        "commit(",
        "rollback(",
        "close(",
    ):
        assert forbidden not in local_source
