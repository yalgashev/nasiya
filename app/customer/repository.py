from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer


def get_customer_by_user_id(session: Session, user_id: UUID) -> Customer | None:
    statement = select(Customer).where(Customer.user_id == user_id)
    return session.execute(statement).scalar_one_or_none()


def create_customer_draft_if_missing(
    session: Session,
    user_id: UUID,
    now: datetime,
) -> Customer:
    current_time = _as_utc(now)
    insert_statement = insert(Customer).values(
        user_id=user_id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
        created_at=current_time,
        updated_at=current_time,
    )
    session.execute(
        insert_statement.on_conflict_do_nothing(index_elements=[Customer.user_id])
    )
    customer = get_customer_by_user_id(session, user_id)
    if customer is None:
        raise LookupError("customer draft was not created or found")
    return customer


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Customer timestamps must be timezone-aware")
    return value.astimezone(UTC)
