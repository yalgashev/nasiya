"""Narrow M16 persistence primitives over a caller-owned Session."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Select, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.debt.values import DebtId
from app.rating.contracts import (
    RatingEvent as RatingEventContract,
)
from app.rating.contracts import (
    RatingEventAppendResult,
    RiskBandDisclosureProjection,
)
from app.rating.enums import (
    RatingEventAppendOutcome,
    RatingEventType,
    RatingRecordingSource,
    RiskBand,
    RiskBandDisclosurePurpose,
)
from app.rating.models import DisclosureViewLog, RatingEvent
from app.rating.ports import (
    LockedRatingCustomerScope,
    RatingEventAppendError,
    validate_locked_rating_customer_scope,
)
from app.rating.values import DisclosureViewId, RatingEventId
from app.shop.values import ShopId, UserId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import ShopCustomerId

_SOURCE_UNIQUE = "uq_rating_events_debt_id_event_type"
_PRIMARY_KEY = "pk_rating_events"
_POSITIVE_CAP_UNIQUE = "ux_rating_events_positive_shop_customer_business_date"

type OrderedRatingEventTuple = tuple[datetime, UUID, str, int]


def _owned_shop_customer_query(
    *, locked_customer: LockedRatingCustomerScope, shop_customer_id: UUID
) -> Select[tuple[UUID]]:
    return select(ShopCustomer.id).where(
        ShopCustomer.id == shop_customer_id,
        ShopCustomer.customer_id == locked_customer.customer_id,
    )


def read_ordered_locked_events(
    session: Session,
    *,
    locked_customer: LockedRatingCustomerScope,
) -> tuple[RatingEventContract, ...]:
    locked = validate_locked_rating_customer_scope(session, locked_customer)
    rows = session.execute(
        select(
            RatingEvent.id,
            RatingEvent.shop_customer_id,
            RatingEvent.debt_id,
            RatingEvent.event_type,
            RatingEvent.delta,
            RatingEvent.occurred_at,
            RatingEvent.business_date,
            RatingEvent.recording_source,
        )
        .join(ShopCustomer, ShopCustomer.id == RatingEvent.shop_customer_id)
        .where(ShopCustomer.customer_id == locked.customer_id)
        .order_by(
            RatingEvent.occurred_at,
            RatingEvent.debt_id,
            RatingEvent.event_type,
        )
    ).all()
    return tuple(
        RatingEventContract(
            id=RatingEventId(row.id),
            shop_customer_id=ShopCustomerId(row.shop_customer_id),
            debt_id=DebtId(row.debt_id),
            event_type=RatingEventType(row.event_type),
            delta=row.delta,
            occurred_at=row.occurred_at,
            business_date=row.business_date,
            recording_source=RatingRecordingSource(row.recording_source),
        )
        for row in rows
    )


def read_ordered_locked_event_tuples(
    session: Session,
    *,
    locked_customer: LockedRatingCustomerScope,
) -> tuple[OrderedRatingEventTuple, ...]:
    """Read only scalar fold inputs for every ShopCustomer of one Customer."""

    locked = validate_locked_rating_customer_scope(session, locked_customer)
    rows = session.execute(
        select(
            RatingEvent.occurred_at,
            RatingEvent.debt_id,
            RatingEvent.event_type,
            RatingEvent.delta,
        )
        .join(ShopCustomer, ShopCustomer.id == RatingEvent.shop_customer_id)
        .where(ShopCustomer.customer_id == locked.customer_id)
        .order_by(
            RatingEvent.occurred_at,
            RatingEvent.debt_id,
            RatingEvent.event_type,
            RatingEvent.delta,
        )
    ).tuples()
    return tuple(tuple(row) for row in rows)


def source_event_exists_locked(
    session: Session,
    *,
    locked_customer: LockedRatingCustomerScope,
    debt_id: DebtId,
    event_type: RatingEventType,
) -> bool:
    locked = validate_locked_rating_customer_scope(session, locked_customer)
    return bool(
        session.scalar(
            select(
                exists().where(
                    RatingEvent.debt_id == debt_id.as_uuid(),
                    RatingEvent.event_type == event_type.value,
                    RatingEvent.shop_customer_id.in_(
                        select(ShopCustomer.id).where(
                            ShopCustomer.customer_id == locked.customer_id
                        )
                    ),
                )
            )
        )
    )


def _exact_source_event_exists_locked(
    session: Session,
    *,
    locked_customer: LockedRatingCustomerScope,
    event: RatingEventContract,
) -> bool:
    row = session.execute(
        select(
            RatingEvent.shop_customer_id,
            RatingEvent.delta,
            RatingEvent.occurred_at,
            RatingEvent.business_date,
            RatingEvent.recording_source,
        )
        .join(ShopCustomer, ShopCustomer.id == RatingEvent.shop_customer_id)
        .where(
            ShopCustomer.customer_id == locked_customer.customer_id,
            RatingEvent.debt_id == event.debt_id.as_uuid(),
            RatingEvent.event_type == event.event_type.value,
        )
    ).one_or_none()
    return row is not None and (
        row.shop_customer_id == event.shop_customer_id.as_uuid()
        and row.delta == event.delta
        and row.occurred_at == event.occurred_at
        and row.business_date == event.business_date
        and row.recording_source == event.recording_source.value
    )


def positive_cap_used_locked(
    session: Session,
    *,
    locked_customer: LockedRatingCustomerScope,
    shop_customer_id: ShopCustomerId,
    business_date: date,
) -> bool:
    locked = validate_locked_rating_customer_scope(session, locked_customer)
    owned_id = session.scalar(
        _owned_shop_customer_query(
            locked_customer=locked,
            shop_customer_id=shop_customer_id.as_uuid(),
        )
    )
    if owned_id is None:
        return False
    return bool(
        session.scalar(
            select(
                exists().where(
                    RatingEvent.shop_customer_id == owned_id,
                    RatingEvent.business_date == business_date,
                    RatingEvent.event_type == RatingEventType.ON_TIME_PAID.value,
                )
            )
        )
    )


def append_locked_event(
    session: Session,
    *,
    locked_customer: LockedRatingCustomerScope,
    event: RatingEventContract,
) -> RatingEventAppendResult:
    locked = validate_locked_rating_customer_scope(session, locked_customer)
    if not isinstance(event, RatingEventContract):
        raise TypeError("event must be a RatingEvent")
    owned_id = session.scalar(
        _owned_shop_customer_query(
            locked_customer=locked,
            shop_customer_id=event.shop_customer_id.as_uuid(),
        )
    )
    if owned_id is None:
        raise RatingEventAppendError()
    row = RatingEvent(
        id=event.id.as_uuid(),
        shop_customer_id=owned_id,
        debt_id=event.debt_id.as_uuid(),
        event_type=event.event_type.value,
        delta=event.delta,
        occurred_at=event.occurred_at,
        business_date=event.business_date,
        recording_source=event.recording_source.value,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        constraint = _constraint_name(exc)
        if constraint in {_SOURCE_UNIQUE, _PRIMARY_KEY}:
            if _exact_source_event_exists_locked(
                session,
                locked_customer=locked,
                event=event,
            ):
                return RatingEventAppendResult(
                    RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS
                )
            raise RatingEventAppendError() from None
        if constraint == _POSITIVE_CAP_UNIQUE:
            return RatingEventAppendResult(
                RatingEventAppendOutcome.POSITIVE_DAILY_CAP_ALREADY_USED
            )
        raise RatingEventAppendError() from None
    return RatingEventAppendResult(RatingEventAppendOutcome.APPENDED)


def insert_disclosure_view_locked(
    session: Session,
    *,
    locked_customer: LockedRatingCustomerScope,
    disclosure_view_id: DisclosureViewId,
    actor_user_id: UserId,
    current_shop_id: ShopId,
    shop_customer_id: ShopCustomerId,
    purpose: RiskBandDisclosurePurpose,
    band: RiskBand,
    viewed_at: datetime,
) -> DisclosureViewId:
    locked = validate_locked_rating_customer_scope(session, locked_customer)
    owned = session.scalar(
        select(ShopCustomer.id).where(
            ShopCustomer.id == shop_customer_id.as_uuid(),
            ShopCustomer.shop_id == current_shop_id,
            ShopCustomer.customer_id == locked.customer_id,
        )
    )
    if owned is None:
        raise RuntimeError("Disclosure persistence failed")
    try:
        with session.begin_nested():
            session.add(
                DisclosureViewLog(
                    id=disclosure_view_id.as_uuid(),
                    actor_user_id=actor_user_id,
                    shop_id=current_shop_id,
                    shop_customer_id=owned,
                    purpose=purpose.value,
                    band=band.value,
                    created_at=viewed_at,
                )
            )
            session.flush()
    except IntegrityError:
        raise RuntimeError("Disclosure persistence failed") from None
    return disclosure_view_id


def read_tenant_disclosure_projection(
    session: Session,
    *,
    actor_user_id: UserId,
    current_shop_id: ShopId,
    disclosure_view_id: DisclosureViewId,
) -> RiskBandDisclosureProjection | None:
    row = session.execute(
        select(
            DisclosureViewLog.band,
            DisclosureViewLog.purpose,
            DisclosureViewLog.created_at,
        )
        .join(
            ShopCustomer,
            (ShopCustomer.id == DisclosureViewLog.shop_customer_id)
            & (ShopCustomer.shop_id == DisclosureViewLog.shop_id),
        )
        .where(
            DisclosureViewLog.id == disclosure_view_id.as_uuid(),
            DisclosureViewLog.actor_user_id == actor_user_id,
            DisclosureViewLog.shop_id == current_shop_id,
        )
    ).one_or_none()
    if row is None:
        return None
    return RiskBandDisclosureProjection(
        band=RiskBand(row.band),
        purpose=RiskBandDisclosurePurpose(row.purpose),
        viewed_at=row.created_at,
    )


class SqlAlchemyRatingRepository:
    """Structural adapter; every operation borrows the supplied Session."""

    read_ordered_locked_events = staticmethod(read_ordered_locked_events)
    read_ordered_locked_event_tuples = staticmethod(read_ordered_locked_event_tuples)
    source_event_exists_locked = staticmethod(source_event_exists_locked)
    positive_cap_used_locked = staticmethod(positive_cap_used_locked)
    append_locked_event = staticmethod(append_locked_event)
    insert_disclosure_view_locked = staticmethod(insert_disclosure_view_locked)
    read_tenant_disclosure_projection = staticmethod(read_tenant_disclosure_projection)


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
