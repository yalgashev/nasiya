from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.debt.tenant_cancel_service as cancel_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.tenant_cancel_service import (
    CancelTenantDebtCommand,
    TenantDebtCancelOutcome,
    cancel_tenant_debt,
)
from app.debt.values import DebtId, DebtRevision
from app.offers.models import OfferAcceptance
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import ShopStaff
from tests.test_debt_creation_gates_postgresql import (
    NOW,
    _add_debt,
    _seed_target,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _pending_debt(session: Session, *, role: ShopRole = ShopRole.OWNER):
    seed = _seed_target(session, role=role)
    _add_debt(session, seed=seed, amount="100", status=DebtStatus.PENDING)
    debt = session.scalar(
        select(Debt).where(Debt.shop_customer_id == seed.shop_customer.id)
    )
    assert debt is not None
    return seed, debt


def _command(
    *,
    debt_id,
    reason: str = "  Private staff correction  ",
    revision: int = 1,
    now=NOW + timedelta(hours=1),
) -> CancelTenantDebtCommand:
    return CancelTenantDebtCommand(
        debt_id=DebtId(debt_id),
        expected_revision=DebtRevision(revision),
        now=now,
        raw_reason=reason,
    )


@pytest.mark.parametrize("role", tuple(ShopRole))
def test_every_live_shop_role_can_cancel_with_one_private_reason_audit(
    db_session: Session, role: ShopRole
) -> None:
    seed, debt = _pending_debt(db_session, role=role)
    command = _command(debt_id=debt.id)

    result = cancel_tenant_debt(
        db_session,
        authority=seed.authority,
        command=command,
    )

    assert result.outcome is TenantDebtCancelOutcome.CANCELLED
    db_session.refresh(debt)
    assert debt.status == DebtStatus.CANCELLED.value
    assert debt.revision == 2
    assert debt.cancelled_at == command.now
    assert debt.cancellation_reason == "Private staff correction"
    assert db_session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.DEBT_CANCELLED.value
        )
    )
    assert audit is not None and audit.payload == {"reason_provided": True}
    assert "Private staff correction" not in repr(command)
    assert "Private staff correction" not in repr(result)
    assert "Private staff correction" not in repr(audit)


@pytest.mark.parametrize(
    ("denial", "expected"),
    (
        ("wrong_tenant", ErrorCode.DEBT_UNAVAILABLE),
        ("suspended", ErrorCode.SHOP_SUSPENDED),
        ("revoked", ErrorCode.FORBIDDEN),
        ("platform_admin_only", ErrorCode.FORBIDDEN),
        ("actor_inactive", ErrorCode.FORBIDDEN),
    ),
)
def test_cancel_denies_wrong_tenant_and_non_live_staff_without_writes(
    db_session: Session,
    denial: str,
    expected: ErrorCode,
) -> None:
    owner, debt = _pending_debt(db_session)
    authority = owner.authority
    if denial == "wrong_tenant":
        other = _seed_target(db_session)
        authority = other.authority
    elif denial == "suspended":
        owner.shop.status = ShopStatus.SUSPENDED.value
    elif denial in {"revoked", "platform_admin_only"}:
        staff = db_session.scalar(
            select(ShopStaff).where(
                ShopStaff.shop_id == owner.shop.id,
                ShopStaff.user_id == owner.actor.id,
            )
        )
        assert staff is not None
        staff.is_active = False
        staff.revoked_at = NOW
        if denial == "platform_admin_only":
            owner.actor.is_platform_admin = True
    else:
        owner.actor.is_active = False
    db_session.flush()

    result = cancel_tenant_debt(
        db_session,
        authority=authority,
        command=_command(debt_id=debt.id),
    )

    assert result.error is expected
    assert db_session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 0
    db_session.refresh(debt)
    assert debt.status == DebtStatus.PENDING.value
    assert debt.revision == 1


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        ("missing_reason", ErrorCode.REASON_REQUIRED),
        ("invalid_reason", ErrorCode.VALIDATION_ERROR),
        ("stale_revision", ErrorCode.DEBT_NOT_PENDING),
        ("expired", ErrorCode.DEBT_EXPIRED),
        ("terminal", ErrorCode.DEBT_NOT_PENDING),
    ),
)
def test_cancel_reason_revision_expiry_and_terminal_failures_are_zero_write(
    db_session: Session,
    failure: str,
    expected: ErrorCode,
) -> None:
    seed, debt = _pending_debt(db_session)
    if failure == "terminal":
        debt.status = DebtStatus.REJECTED.value
        debt.rejected_at = NOW + timedelta(minutes=1)
        debt.updated_at = NOW + timedelta(minutes=1)
        debt.revision = 2
    db_session.flush()
    reason = {
        "missing_reason": "   ",
        "invalid_reason": "bad\x00reason",
    }.get(failure, "valid reason")
    revision = 2 if failure == "stale_revision" else 1
    now = (
        NOW + timedelta(hours=72) if failure == "expired" else NOW + timedelta(hours=1)
    )

    result = cancel_tenant_debt(
        db_session,
        authority=seed.authority,
        command=_command(
            debt_id=debt.id,
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
        db_session.refresh(debt)
        assert debt.status == DebtStatus.EXPIRED.value
        assert debt.revision == 2
        assert debt.expired_at == now
        assert debt.cancellation_reason is None
    elif failure != "terminal":
        db_session.refresh(debt)
        assert debt.status == DebtStatus.PENDING.value
        assert debt.cancellation_reason is None


def test_cancel_audit_fault_rolls_back_reason_and_transition(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, debt = _pending_debt(db_session)
    authority = seed.authority
    debt_id = debt.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)

    def fail(*args, **kwargs):
        raise RuntimeError("cancel audit fault")

    monkeypatch.setattr(cancel_service, "append_audit_event", fail)
    with pytest.raises(RuntimeError, match="audit fault"):
        with factory.begin() as session:
            cancel_tenant_debt(
                session,
                authority=authority,
                command=_command(debt_id=debt_id),
            )

    with factory.begin() as session:
        stored = session.get(Debt, debt_id)
        assert stored is not None
        assert stored.status == DebtStatus.PENDING.value
        assert stored.cancellation_reason is None
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
