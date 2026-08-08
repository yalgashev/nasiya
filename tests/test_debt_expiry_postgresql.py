from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

import app.debt.expiry_service as expiry_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.customer_authority import resolve_own_customer_debt_authority
from app.debt.customer_reject_service import (
    RejectCustomerDebtCommand,
    reject_own_customer_debt,
)
from app.debt.enums import DebtStatus
from app.debt.expiry_service import expire_pending_debts
from app.debt.models import Debt
from app.debt.values import DebtId, DebtRevision
from app.shop.enums import ShopStatus
from tests.test_customer_debt_read_postgresql import NOW, _seed_owned_debt

pytestmark = pytest.mark.integration


def _reject_command(debt_id, *, now):
    return RejectCustomerDebtCommand(
        debt_id=DebtId(debt_id),
        expected_revision=DebtRevision(1),
        now=now,
        raw_reason=None,
    )


@pytest.mark.parametrize(
    ("offset", "expected_error", "expected_status"),
    (
        (
            timedelta(microseconds=-1),
            None,
            DebtStatus.REJECTED,
        ),
        (timedelta(), ErrorCode.DEBT_EXPIRED, DebtStatus.EXPIRED),
        (
            timedelta(microseconds=1),
            ErrorCode.DEBT_EXPIRED,
            DebtStatus.EXPIRED,
        ),
    ),
)
def test_inline_expiry_has_exact_microsecond_boundary_and_one_audit(
    m2_test_database: Engine,
    offset: timedelta,
    expected_error: ErrorCode | None,
    expected_status: DebtStatus,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_owned_debt(session)
        authority = resolve_own_customer_debt_authority(
            session, authenticated_user=seed.user
        )
        debt_id = seed.debt.id
        expiry = seed.debt.pending_expires_at
    with factory.begin() as session:
        result = reject_own_customer_debt(
            session,
            authority=authority,
            command=_reject_command(debt_id, now=expiry + offset),
        )
        assert result.error is expected_error
    with factory.begin() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == expected_status.value
        event_type = (
            AuditEventType.DEBT_EXPIRED
            if expected_status is DebtStatus.EXPIRED
            else AuditEventType.DEBT_REJECTED
        )
        audit = session.scalar(
            select(AuditLog).where(AuditLog.event_type == event_type.value)
        )
        assert audit is not None
        if expected_status is DebtStatus.EXPIRED:
            assert audit.payload == {"source": "inline"}


def test_inline_expiry_works_for_customer_reject_when_shop_is_suspended(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_owned_debt(session)
        seed.shop.status = ShopStatus.SUSPENDED.value
        authority = resolve_own_customer_debt_authority(
            session, authenticated_user=seed.user
        )
        debt_id = seed.debt.id
        expiry = seed.debt.pending_expires_at
    with factory.begin() as session:
        result = reject_own_customer_debt(
            session,
            authority=authority,
            command=_reject_command(debt_id, now=expiry),
        )
        assert result.error is ErrorCode.DEBT_EXPIRED
    with factory.begin() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None and debt.status == DebtStatus.EXPIRED.value


def test_batch_is_bounded_ordered_idempotent_and_works_for_suspended_shop(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        first = _seed_owned_debt(session)
        second = _seed_owned_debt(session)
        first.shop.status = ShopStatus.SUSPENDED.value
        first_id = first.debt.id
        second_id = second.debt.id
        now = max(first.debt.pending_expires_at, second.debt.pending_expires_at)

    first_batch = expire_pending_debts(factory, now=now, batch_size=1)
    second_batch = expire_pending_debts(factory, now=now, batch_size=1)
    replay = expire_pending_debts(factory, now=now, batch_size=2)

    assert first_batch.candidates_considered == first_batch.expired_count == 1
    assert second_batch.candidates_considered == second_batch.expired_count == 1
    assert replay.candidates_considered == replay.expired_count == 0
    with factory.begin() as session:
        debts = list(
            session.scalars(select(Debt).where(Debt.id.in_((first_id, second_id))))
        )
        assert {debt.status for debt in debts} == {DebtStatus.EXPIRED.value}
        audits = list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.event_type == AuditEventType.DEBT_EXPIRED.value)
                .order_by(AuditLog.occurred_at, AuditLog.id)
            )
        )
        assert len(audits) == 2
        assert all(audit.payload == {"source": "batch"} for audit in audits)


@pytest.mark.parametrize("batch_size", (0, 101, True))
def test_batch_size_is_strictly_bounded(
    m2_test_database: Engine, batch_size: int
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with pytest.raises(ValueError, match="batch size"):
        expire_pending_debts(factory, now=NOW, batch_size=batch_size)


def test_inline_and_batch_expiry_have_one_winner(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_owned_debt(session)
        authority = resolve_own_customer_debt_authority(
            session, authenticated_user=seed.user
        )
        debt_id = seed.debt.id
        expiry = seed.debt.pending_expires_at
    start = Event()

    def inline_once():
        assert start.wait(timeout=5)
        with factory.begin() as session:
            return reject_own_customer_debt(
                session,
                authority=authority,
                command=_reject_command(debt_id, now=expiry),
            )

    def batch_once():
        assert start.wait(timeout=5)
        return expire_pending_debts(factory, now=expiry, batch_size=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        inline_future = executor.submit(inline_once)
        batch_future = executor.submit(batch_once)
        start.set()
        inline_result = inline_future.result(timeout=10)
        batch_result = batch_future.result(timeout=10)

    assert inline_result.error is ErrorCode.DEBT_EXPIRED
    assert batch_result.expired_count in {0, 1}
    with factory.begin() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None and debt.status == DebtStatus.EXPIRED.value
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.event_type == AuditEventType.DEBT_EXPIRED.value
                )
            )
        )
        assert len(audits) == 1
        assert audits[0].payload in ({"source": "inline"}, {"source": "batch"})


def test_expiry_audit_fault_rolls_back_transition(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_owned_debt(session)
        debt_id = seed.debt.id
        expiry = seed.debt.pending_expires_at

    def fail(*args, **kwargs):
        raise RuntimeError("expiry audit fault")

    monkeypatch.setattr(expiry_service, "append_audit_event", fail)
    with pytest.raises(RuntimeError, match="audit fault"):
        expire_pending_debts(factory, now=expiry, batch_size=1)

    with factory.begin() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == DebtStatus.PENDING.value
        assert debt.revision == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
