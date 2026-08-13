from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import create_database_session_factory
from app.debt.business_time import tashkent_business_date
from app.debt.repository import lock_customer_hard_block_scope
from app.debt.values import CustomerId, DebtId
from app.rating.current_read_service import (
    CurrentRiskBandProjection,
    read_locked_current_rating_state,
    read_locked_current_risk_band,
)
from app.rating.enums import RatingEventType, RatingRecordingSource, RiskBand
from app.rating.models import RatingEvent
from app.rating.values import RatingEventId
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import ShopCustomerId
from tests.test_m15_migration_postgresql import NOW, _seed_parents
from tests.test_m16_rating_repository_postgresql import _debt


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _append_event(
    session: Session,
    *,
    debt_id,
    shop_customer_id,
    event_type: RatingEventType,
    occurred_at: datetime,
) -> None:
    session.add(
        RatingEvent(
            id=RatingEventId(uuid4()).as_uuid(),
            debt_id=DebtId(debt_id).as_uuid(),
            shop_customer_id=ShopCustomerId(shop_customer_id).as_uuid(),
            event_type=event_type.value,
            delta=5 if event_type is RatingEventType.ON_TIME_PAID else -15,
            occurred_at=occurred_at,
            business_date=tashkent_business_date(occurred_at),
            recording_source=RatingRecordingSource.LIVE.value,
            source_revision=3,
        )
    )
    session.flush()


@pytest.mark.integration
def test_current_rating_reads_all_pairs_in_two_queries_and_returns_safe_band(
    db_session: Session,
) -> None:
    actor, first_relation = _seed_parents(db_session)
    first_debt = _debt(db_session, relation=first_relation, actor=actor)
    second_shop = Shop(
        name=f"M16 current rating {uuid4().hex[:8]}",
        phone=f"+998{uuid4().int % 1_000_000_000:09d}",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(second_shop)
    db_session.flush()
    second_relation = ShopCustomer(
        shop_id=second_shop.id,
        customer_id=first_relation.customer_id,
        credit_limit_uzs=first_relation.credit_limit_uzs,
        max_open_debts=first_relation.max_open_debts,
        list_status=first_relation.list_status,
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(second_relation)
    db_session.flush()
    second_debt = _debt(
        db_session,
        relation=second_relation,
        actor=actor,
        offset=2,
    )
    _append_event(
        db_session,
        debt_id=first_debt.id,
        shop_customer_id=first_relation.id,
        event_type=RatingEventType.ON_TIME_PAID,
        occurred_at=datetime(2026, 8, 12, 8, tzinfo=UTC),
    )
    _append_event(
        db_session,
        debt_id=second_debt.id,
        shop_customer_id=second_relation.id,
        event_type=RatingEventType.OVERDUE,
        occurred_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
    )
    locked = lock_customer_hard_block_scope(
        db_session,
        customer_id=CustomerId(first_relation.customer_id),
    )
    assert locked is not None
    statements: list[str] = []

    def count_queries(*args) -> None:
        statements.append(args[2])

    event.listen(db_session.bind, "before_cursor_execute", count_queries)
    try:
        state = read_locked_current_rating_state(
            db_session,
            locked_customer=locked,
            as_of_business_date=date(2026, 8, 12),
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", count_queries)

    assert len(statements) == 2
    assert "rating_events" in statements[0]
    assert (state.current_score, state.has_history, state.band) == (
        50,
        True,
        RiskBand.YELLOW,
    )
    projection = read_locked_current_risk_band(
        db_session,
        locked_customer=locked,
        as_of_business_date=date(2026, 8, 12),
    )
    assert isinstance(projection, CurrentRiskBandProjection)
    assert projection.band is RiskBand.YELLOW


@pytest.mark.integration
def test_effective_active_past_due_blocks_then_paid_negative_history_is_numeric(
    db_session: Session,
) -> None:
    actor, relation = _seed_parents(db_session)
    debt = _debt(db_session, relation=relation, actor=actor)
    debt.due_date = date(2026, 8, 11)
    locked = lock_customer_hard_block_scope(
        db_session,
        customer_id=CustomerId(relation.customer_id),
    )
    assert locked is not None

    active_past_due = read_locked_current_risk_band(
        db_session,
        locked_customer=locked,
        as_of_business_date=date(2026, 8, 12),
    )
    assert active_past_due.band is RiskBand.BLOCKED

    overdue_at = datetime(2026, 8, 12, 8, tzinfo=UTC)
    debt.status = "paid"
    debt.revision = 4
    debt.overdue_at = overdue_at
    debt.overdue_revision = 3
    debt.paid_at = overdue_at + timedelta(hours=1)
    debt.updated_at = debt.paid_at
    _append_event(
        db_session,
        debt_id=debt.id,
        shop_customer_id=relation.id,
        event_type=RatingEventType.OVERDUE,
        occurred_at=overdue_at,
    )

    paid = read_locked_current_rating_state(
        db_session,
        locked_customer=locked,
        as_of_business_date=date(2026, 8, 13),
    )
    assert (paid.current_score, paid.has_history, paid.band) == (
        45,
        True,
        RiskBand.RED,
    )
