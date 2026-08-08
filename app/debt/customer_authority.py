"""Server-resolved own-customer authority for M13 debt reads and decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.debt.values import CustomerId, UserId
from app.telegram.models import TelegramLink

__all__ = (
    "CustomerDebtAuthority",
    "resolve_own_customer_debt_authority",
)


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtAuthority:
    """Immutable server-derived identity; callers never supply customer IDs."""

    user_id: UserId = field(repr=False)
    customer_id: CustomerId = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise ValueError("Customer debt user identity is invalid")
        if not isinstance(self.customer_id, UUID):
            raise ValueError("Customer debt customer identity is invalid")

    def __repr__(self) -> str:
        return "CustomerDebtAuthority(<redacted>)"


def resolve_own_customer_debt_authority(
    session: Session,
    *,
    authenticated_user: User,
) -> CustomerDebtAuthority | None:
    """Resolve the current active, phone-verified Customer owned by this User."""

    if not isinstance(authenticated_user, User):
        raise TypeError("authenticated_user must be a User")
    if not isinstance(authenticated_user.id, UUID) or not authenticated_user.is_active:
        return None
    statement = (
        select(User.id, Customer.id)
        .join(Customer, Customer.user_id == User.id)
        .join(TelegramLink, TelegramLink.user_id == User.id)
        .where(
            User.id == authenticated_user.id,
            User.is_active.is_(True),
            Customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_ACTIVE,
            TelegramLink.telegram_chat_id.is_not(None),
            TelegramLink.unlinked_at.is_(None),
            TelegramLink.phone_verified_at.is_not(None),
            TelegramLink.phone_verified_at == TelegramLink.linked_at,
        )
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        return None
    return CustomerDebtAuthority(user_id=UserId(row.id), customer_id=CustomerId(row[1]))
