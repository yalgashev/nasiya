"""Privacy-bounded M15 overdue read contracts after Customer serialization."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.debt.policy import GlobalHardBlockProjection
from app.debt.values import CustomerId

__all__ = (
    "GlobalHardBlockConsumer",
    "LockedCustomerGlobalHardBlockReadPort",
    "require_hard_block_business_date",
)


class GlobalHardBlockConsumer(StrEnum):
    DEBT_CREATE = "debt_create"
    DEBT_ACCEPT = "debt_accept"


@runtime_checkable
class LockedCustomerGlobalHardBlockReadPort(Protocol):
    """Return only a boolean after the caller locks the authoritative Customer."""

    def read_global_hard_block(
        self,
        *,
        customer_id: CustomerId,
        as_of_business_date: date,
    ) -> GlobalHardBlockProjection: ...


def require_hard_block_business_date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("Hard-block business date is invalid")
    return value
