"""Session-bound append/read protocols behind an inherited Customer lock."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from app.debt.values import DebtId
from app.rating.contracts import RatingEvent, RatingEventAppendResult
from app.shop_customer.values import CustomerId, ShopCustomerId

__all__ = (
    "LockedRatingCustomerScope",
    "LockedRatingSourceScope",
    "RatingEventAppendError",
    "RatingEventReadPort",
    "RatingEventWriterPort",
    "validate_locked_rating_customer_scope",
    "validate_locked_rating_source_scope",
)


class RatingEventAppendError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Rating event append failed")


@dataclass(frozen=True, slots=True, repr=False)
class LockedRatingCustomerScope:
    """Opaque proof that a coordinator owns the Customer lock in this Session."""

    customer_id: CustomerId = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.customer_id, UUID):
            raise ValueError("Locked rating Customer is invalid")
        if not isinstance(self._session, Session):
            raise ValueError("Locked rating Session is invalid")

    def __repr__(self) -> str:
        return "LockedRatingCustomerScope(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedRatingSourceScope:
    """Semantic proof derived from an already locked source chain."""

    customer_id: CustomerId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.customer_id, UUID):
            raise ValueError("Locked rating source Customer is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Locked rating source ShopCustomer is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Locked rating source Debt is invalid")
        if not isinstance(self._session, Session):
            raise ValueError("Locked rating source Session is invalid")

    @property
    def customer_scope(self) -> LockedRatingCustomerScope:
        return LockedRatingCustomerScope(
            customer_id=self.customer_id,
            _session=self._session,
        )

    def __repr__(self) -> str:
        return "LockedRatingSourceScope(<redacted>)"


def validate_locked_rating_customer_scope(
    session: Session,
    token: object,
) -> LockedRatingCustomerScope:
    if not isinstance(token, LockedRatingCustomerScope):
        raise TypeError("Rating Customer lock token is invalid")
    if token._session is not session:
        raise RuntimeError("Rating Customer lock belongs to another session")
    return token


def validate_locked_rating_source_scope(
    session: Session,
    token: object,
) -> LockedRatingSourceScope:
    if not isinstance(token, LockedRatingSourceScope):
        raise TypeError("Rating source lock token is invalid")
    if token._session is not session:
        raise RuntimeError("Rating source lock belongs to another session")
    return token


@runtime_checkable
class RatingEventWriterPort(Protocol):
    """Append only; no update, delete, reversal, or transaction ownership."""

    def append_locked_event(
        self,
        session: Session,
        *,
        locked_customer: LockedRatingCustomerScope,
        event: RatingEvent,
    ) -> RatingEventAppendResult: ...


@runtime_checkable
class RatingEventReadPort(Protocol):
    """Return detached events in occurred_at/debt_id/event_type order."""

    def read_ordered_locked_events(
        self,
        session: Session,
        *,
        locked_customer: LockedRatingCustomerScope,
    ) -> tuple[RatingEvent, ...]: ...
