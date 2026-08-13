from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import create_database_session_factory
from app.debt.models import Debt
from app.debt.values import DebtId, DebtRevision
from app.rating.contracts import create_on_time_paid_rating_event
from app.rating.enums import (
    RatingEventAppendOutcome,
    RatingRecordingSource,
    RiskBand,
    RiskBandDisclosurePurpose,
)
from app.rating.ports import LockedRatingCustomerScope, RatingEventAppendError
from app.rating.repository import (
    append_locked_event,
    insert_disclosure_view_locked,
    positive_cap_used_locked,
    read_ordered_locked_events,
    read_tenant_disclosure_projection,
    source_event_exists_locked,
)
from app.rating.values import DisclosureViewId, RatingEventId
from app.shop_customer.values import CustomerId, ShopCustomerId
from tests.test_m15_migration_postgresql import NOW, _seed_parents


@pytest.fixture
def db_session(m2_test_database) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _debt(session: Session, *, relation, actor, offset: int = 0) -> Debt:
    created_at = NOW + timedelta(minutes=offset)
    debt = Debt(
        shop_customer_id=relation.id,
        created_by_user_id=actor.id,
        original_amount_uzs=Decimal("100000"),
        discount_basis_points=0,
        discounted_amount_uzs=Decimal("100000"),
        due_date=date(2026, 8, 20),
        pending_expires_at=created_at + timedelta(hours=72),
        status="active",
        revision=2,
        accepted_at=created_at + timedelta(hours=1),
        created_at=created_at,
        updated_at=created_at + timedelta(hours=1),
    )
    session.add(debt)
    session.flush()
    return debt


@pytest.mark.integration
def test_locked_repository_append_order_source_and_daily_cap(
    db_session: Session,
) -> None:
    actor, relation = _seed_parents(db_session)
    first_debt = _debt(db_session, relation=relation, actor=actor)
    second_debt = _debt(db_session, relation=relation, actor=actor, offset=2)
    locked = LockedRatingCustomerScope(
        customer_id=CustomerId(relation.customer_id),
        _session=db_session,
    )
    first = create_on_time_paid_rating_event(
        event_id=RatingEventId(uuid4()),
        shop_customer_id=ShopCustomerId(relation.id),
        debt_id=DebtId(first_debt.id),
        payment_created_at=datetime(2026, 8, 12, 8, tzinfo=UTC),
        recording_source=RatingRecordingSource.LIVE,
        source_revision=DebtRevision(3),
    )
    second = create_on_time_paid_rating_event(
        event_id=RatingEventId(uuid4()),
        shop_customer_id=ShopCustomerId(relation.id),
        debt_id=DebtId(second_debt.id),
        payment_created_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
        recording_source=RatingRecordingSource.LIVE,
        source_revision=DebtRevision(3),
    )

    assert (
        append_locked_event(db_session, locked_customer=locked, event=first).outcome
        is RatingEventAppendOutcome.APPENDED
    )
    assert (
        append_locked_event(db_session, locked_customer=locked, event=first).outcome
        is RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS
    )
    mismatched_replay = create_on_time_paid_rating_event(
        event_id=RatingEventId(uuid4()),
        shop_customer_id=ShopCustomerId(relation.id),
        debt_id=DebtId(first_debt.id),
        payment_created_at=datetime(2026, 8, 12, 8, 1, tzinfo=UTC),
        recording_source=RatingRecordingSource.LIVE,
        source_revision=DebtRevision(3),
    )
    with pytest.raises(RatingEventAppendError, match="Rating event append failed"):
        append_locked_event(
            db_session,
            locked_customer=locked,
            event=mismatched_replay,
        )
    assert (
        append_locked_event(db_session, locked_customer=locked, event=second).outcome
        is RatingEventAppendOutcome.POSITIVE_DAILY_CAP_ALREADY_USED
    )
    assert source_event_exists_locked(
        db_session,
        locked_customer=locked,
        debt_id=DebtId(first_debt.id),
        event_type=first.event_type,
    )
    assert positive_cap_used_locked(
        db_session,
        locked_customer=locked,
        shop_customer_id=ShopCustomerId(relation.id),
        business_date=first.business_date,
    )
    assert read_ordered_locked_events(db_session, locked_customer=locked) == (first,)


@pytest.mark.integration
def test_disclosure_insert_and_read_are_actor_and_shop_tenant_bound(
    db_session: Session,
) -> None:
    actor, relation = _seed_parents(db_session)
    locked = LockedRatingCustomerScope(
        customer_id=CustomerId(relation.customer_id),
        _session=db_session,
    )
    disclosure_id = DisclosureViewId(uuid4())
    viewed_at = datetime(2026, 8, 12, 12, tzinfo=UTC)

    result = insert_disclosure_view_locked(
        db_session,
        locked_customer=locked,
        disclosure_view_id=disclosure_id,
        actor_user_id=actor.id,
        current_shop_id=relation.shop_id,
        shop_customer_id=ShopCustomerId(relation.id),
        purpose=RiskBandDisclosurePurpose.CREDIT_LIMIT_REVIEW,
        band=RiskBand.YELLOW,
        viewed_at=viewed_at,
    )
    assert result == disclosure_id
    projection = read_tenant_disclosure_projection(
        db_session,
        actor_user_id=actor.id,
        current_shop_id=relation.shop_id,
        disclosure_view_id=disclosure_id,
    )
    assert projection is not None
    assert projection.band is RiskBand.YELLOW
    assert projection.purpose is RiskBandDisclosurePurpose.CREDIT_LIMIT_REVIEW
    assert projection.viewed_at == viewed_at
    assert (
        read_tenant_disclosure_projection(
            db_session,
            actor_user_id=uuid4(),
            current_shop_id=relation.shop_id,
            disclosure_view_id=disclosure_id,
        )
        is None
    )
    assert (
        read_tenant_disclosure_projection(
            db_session,
            actor_user_id=actor.id,
            current_shop_id=uuid4(),
            disclosure_view_id=disclosure_id,
        )
        is None
    )


@pytest.mark.integration
def test_composite_parent_chain_rejects_mismatched_rating_insert(
    db_session: Session,
) -> None:
    actor, relation = _seed_parents(db_session)
    debt = _debt(db_session, relation=relation, actor=actor)
    _other_actor, other_relation = _seed_parents(db_session)

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO rating_events "
                    "(id,shop_customer_id,debt_id,event_type,delta,"
                    "occurred_at,business_date,recording_source,"
                    "source_revision) VALUES "
                    "(:id,:shop_customer_id,:debt_id,'overdue',-15,"
                    ":occurred_at,:business_date,'live',3)"
                ),
                {
                    "id": uuid4(),
                    "shop_customer_id": other_relation.id,
                    "debt_id": debt.id,
                    "occurred_at": NOW,
                    "business_date": date(2026, 8, 9),
                },
            )
    assert caught.value.orig.diag.constraint_name == (
        "fk_rating_events_debt_shop_customer"
    )


@pytest.mark.integration
def test_composite_parent_chain_rejects_mismatched_disclosure_insert(
    db_session: Session,
) -> None:
    actor, relation = _seed_parents(db_session)
    _other_actor, other_relation = _seed_parents(db_session)

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO disclosure_view_logs "
                    "(id,actor_user_id,shop_id,shop_customer_id,purpose,band,"
                    "created_at) VALUES (:id,:actor_user_id,:shop_id,"
                    ":shop_customer_id,'credit_limit_review','green',:created_at)"
                ),
                {
                    "id": uuid4(),
                    "actor_user_id": actor.id,
                    "shop_id": other_relation.shop_id,
                    "shop_customer_id": relation.id,
                    "created_at": NOW,
                },
            )
    assert caught.value.orig.diag.constraint_name == (
        "fk_disclosure_logs_shop_customer_shop"
    )
