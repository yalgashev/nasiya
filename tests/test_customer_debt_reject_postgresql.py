from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.debt.customer_reject_service as reject_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.customer_authority import resolve_own_customer_debt_authority
from app.debt.customer_reject_service import (
    CustomerDebtRejectOutcome,
    RejectCustomerDebtCommand,
    reject_own_customer_debt,
)
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.values import DebtId, DebtRevision
from app.offers.models import OfferAcceptance
from app.shop.enums import ShopStatus
from tests.test_customer_debt_read_postgresql import NOW, _seed_owned_debt

pytestmark = pytest.mark.integration


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _command(
    *,
    debt_id,
    reason: str | None = "  Private customer reason  ",
    revision: int = 1,
    now=NOW + timedelta(hours=1),
) -> RejectCustomerDebtCommand:
    return RejectCustomerDebtCommand(
        debt_id=DebtId(debt_id),
        expected_revision=DebtRevision(revision),
        now=now,
        raw_reason=reason,
    )


@pytest.mark.parametrize(
    ("raw_reason", "stored_reason", "reason_provided"),
    (
        ("  Private customer reason  ", "Private customer reason", True),
        (None, None, False),
        ("   ", None, False),
    ),
)
def test_customer_reject_is_exact_replay_safe_and_never_creates_acceptance(
    db_session: Session,
    raw_reason: str | None,
    stored_reason: str | None,
    reason_provided: bool,
) -> None:
    seed = _seed_owned_debt(db_session)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    command = _command(debt_id=seed.debt.id, reason=raw_reason)

    rejected = reject_own_customer_debt(
        db_session,
        authority=authority,
        command=command,
    )
    replay = reject_own_customer_debt(
        db_session,
        authority=authority,
        command=command,
    )

    assert rejected.outcome is CustomerDebtRejectOutcome.REJECTED
    assert replay.outcome is CustomerDebtRejectOutcome.REPLAY
    db_session.refresh(seed.debt)
    assert seed.debt.status == DebtStatus.REJECTED.value
    assert seed.debt.revision == 2
    assert seed.debt.rejected_at == command.now
    assert seed.debt.rejection_reason == stored_reason
    assert db_session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
    audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.event_type == AuditEventType.DEBT_REJECTED.value
            )
        )
    )
    assert len(audits) == 1
    assert audits[0].payload == {"reason_provided": reason_provided}
    assert "Private customer reason" not in repr(command)
    assert "Private customer reason" not in repr(replay)
    assert "Private customer reason" not in repr(audits[0])


def test_customer_can_reject_a_suspended_shop_debt(db_session: Session) -> None:
    seed = _seed_owned_debt(db_session)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    seed.shop.status = ShopStatus.SUSPENDED.value
    db_session.flush()

    result = reject_own_customer_debt(
        db_session,
        authority=authority,
        command=_command(debt_id=seed.debt.id, reason=None),
    )

    assert result.outcome is CustomerDebtRejectOutcome.REJECTED
    assert seed.debt.status == DebtStatus.REJECTED.value
    assert db_session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        ("invalid_reason", ErrorCode.VALIDATION_ERROR),
        ("stale_revision", ErrorCode.DEBT_NOT_PENDING),
        ("expired", ErrorCode.DEBT_EXPIRED),
        ("already_active", ErrorCode.DEBT_NOT_PENDING),
        ("foreign", ErrorCode.DEBT_UNAVAILABLE),
    ),
)
def test_customer_reject_failures_are_deterministic_and_zero_write(
    db_session: Session,
    failure: str,
    expected: ErrorCode,
) -> None:
    owner = _seed_owned_debt(db_session)
    actor = owner
    if failure == "foreign":
        actor = _seed_owned_debt(db_session)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=actor.user
    )
    if failure == "already_active":
        owner.debt.status = DebtStatus.ACTIVE.value
        owner.debt.accepted_at = NOW + timedelta(minutes=1)
        owner.debt.updated_at = NOW + timedelta(minutes=1)
        owner.debt.revision = 2
    db_session.flush()
    reason = "bad\x00reason" if failure == "invalid_reason" else None
    revision = 2 if failure == "stale_revision" else 1
    now = (
        NOW + timedelta(hours=72) if failure == "expired" else NOW + timedelta(hours=1)
    )

    result = reject_own_customer_debt(
        db_session,
        authority=authority,
        command=_command(
            debt_id=owner.debt.id,
            reason=reason,
            revision=revision,
            now=now,
        ),
    )

    assert result.error is expected
    assert db_session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
    expected_audits = 1 if failure == "expired" else 0
    assert (
        db_session.scalar(select(func.count()).select_from(AuditLog)) == expected_audits
    )
    if failure == "expired":
        db_session.refresh(owner.debt)
        assert owner.debt.status == DebtStatus.EXPIRED.value
        assert owner.debt.revision == 2
        assert owner.debt.expired_at == now
    elif failure != "already_active":
        db_session.refresh(owner.debt)
        assert owner.debt.status == DebtStatus.PENDING.value
        assert owner.debt.revision == 1


def test_reject_audit_fault_rolls_back_private_reason_and_transition(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _seed_owned_debt(db_session)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    debt_id = seed.debt.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)

    def fail(*args, **kwargs):
        raise RuntimeError("reject audit fault")

    monkeypatch.setattr(reject_service, "append_audit_event", fail)
    with pytest.raises(RuntimeError, match="audit fault"):
        with factory.begin() as session:
            reject_own_customer_debt(
                session,
                authority=authority,
                command=_command(debt_id=debt_id),
            )

    with factory.begin() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == DebtStatus.PENDING.value
        assert debt.rejection_reason is None
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
