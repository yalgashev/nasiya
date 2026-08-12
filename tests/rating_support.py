"""Explicit test composition for the required M16 rating append boundary."""

from __future__ import annotations

from typing import Any

from app.debt.overdue_service import (
    materialize_overdue_candidate as _materialize_overdue_candidate,
)
from app.debt.overdue_service import (
    materialize_overdue_debts as _materialize_overdue_debts,
)
from app.payment.service import record_debt_payment as _record_debt_payment
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter


def record_debt_payment(*args: Any, **kwargs: Any) -> Any:
    kwargs["rating_append_port"] = SqlAlchemyLockedRatingAppendAdapter()
    return _record_debt_payment(*args, **kwargs)


def materialize_overdue_candidate(*args: Any, **kwargs: Any) -> Any:
    kwargs["rating_append_port"] = SqlAlchemyLockedRatingAppendAdapter()
    return _materialize_overdue_candidate(*args, **kwargs)


def materialize_overdue_debts(*args: Any, **kwargs: Any) -> Any:
    kwargs["rating_append_port"] = SqlAlchemyLockedRatingAppendAdapter()
    return _materialize_overdue_debts(*args, **kwargs)
