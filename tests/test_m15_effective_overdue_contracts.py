from dataclasses import fields
from datetime import UTC, date, datetime
from inspect import getsource, signature

import pytest

import app.debt.business_time as business_time
import app.debt.overdue_ports as overdue_ports
from app.debt.business_time import is_effectively_overdue
from app.debt.enums import DebtStatus
from app.debt.overdue_ports import (
    GlobalHardBlockConsumer,
    LockedCustomerGlobalHardBlockReadPort,
    require_hard_block_business_date,
)
from app.debt.policy import GlobalHardBlockProjection


@pytest.mark.parametrize(
    ("status", "due_date", "server_now", "expected"),
    (
        (
            DebtStatus.ACTIVE,
            date(2026, 8, 9),
            datetime(2026, 8, 9, 18, 59, tzinfo=UTC),
            False,
        ),
        (
            DebtStatus.ACTIVE,
            date(2026, 8, 9),
            datetime(2026, 8, 9, 19, 0, tzinfo=UTC),
            True,
        ),
        (DebtStatus.OVERDUE, date(2026, 8, 10), datetime(2026, 8, 9, tzinfo=UTC), True),
        (DebtStatus.PAID, date(2026, 8, 8), datetime(2026, 8, 9, tzinfo=UTC), False),
        (DebtStatus.PENDING, date(2026, 8, 8), datetime(2026, 8, 9, tzinfo=UTC), False),
    ),
)
def test_effective_overdue_is_trusted_tashkent_date_boolean_only(
    status: DebtStatus,
    due_date: date,
    server_now: datetime,
    expected: bool,
) -> None:
    assert (
        is_effectively_overdue(
            status=status,
            due_date=due_date,
            server_now=server_now,
        )
        is expected
    )


def test_effective_overdue_rejects_untrusted_time_and_future_statuses() -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        is_effectively_overdue(
            status=DebtStatus.ACTIVE,
            due_date=date(2026, 8, 9),
            server_now=datetime(2026, 8, 10),
        )
    with pytest.raises(ValueError, match="outside the M15 persisted subset"):
        is_effectively_overdue(
            status=DebtStatus.WRITTEN_OFF,
            due_date=date(2026, 8, 9),
            server_now=datetime(2026, 8, 10, tzinfo=UTC),
        )


def test_locked_customer_hard_block_port_discloses_only_boolean() -> None:
    assert tuple(field.name for field in fields(GlobalHardBlockProjection)) == (
        "is_blocked",
    )
    assert tuple(GlobalHardBlockConsumer) == (
        GlobalHardBlockConsumer.DEBT_CREATE,
        GlobalHardBlockConsumer.DEBT_ACCEPT,
    )
    assert tuple(
        signature(
            LockedCustomerGlobalHardBlockReadPort.read_global_hard_block
        ).parameters
    ) == ("self", "customer_id", "as_of_business_date")
    assert require_hard_block_business_date(date(2026, 8, 10)) == date(2026, 8, 10)
    with pytest.raises(ValueError):
        require_hard_block_business_date(datetime(2026, 8, 10, tzinfo=UTC))


def test_effective_overdue_contract_has_no_get_mutation_or_payment_consumer() -> None:
    source = (getsource(business_time) + getsource(overdue_ports)).casefold()

    for forbidden in (
        "app.payment",
        "session.add",
        "session.flush",
        "session.commit",
        "with_for_update",
    ):
        assert forbidden not in source
