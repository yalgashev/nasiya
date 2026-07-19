from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import Customer
from app.customer.repository import (
    create_customer_draft_if_missing,
    get_customer_by_user_id,
)
from app.customer.view_model import CustomerDraftView, build_customer_draft_view


class CustomerDraftStartError(RuntimeError):
    pass


def start_customer_draft(session: Session, user_id: UUID) -> Customer:
    try:
        return create_customer_draft_if_missing(session, user_id, datetime.now(UTC))
    except (LookupError, SQLAlchemyError):
        raise CustomerDraftStartError("Customer draft could not be started") from None


def get_current_customer_draft_state(
    session: Session,
    current_user: User,
) -> CustomerDraftView | None:
    customer = get_customer_by_user_id(session, current_user.id)
    if customer is None:
        return None
    return build_customer_draft_view(customer, current_user.phone)
