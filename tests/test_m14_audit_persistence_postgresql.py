from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.contracts import DebtPaidAuditPayload, PaymentRecordedAuditPayload
from app.audit.models import AuditLog
from app.audit.repository import (
    append_audit_event,
    append_debt_paid_audit,
    append_payment_recorded_audit,
)
from app.auth.models import User
from app.db import create_database_session_factory
from app.debt.enums import DebtStatus
from app.debt.values import DebtRevision
from app.payment.enums import PaymentMethod
from app.payment.values import PaymentAmountUZS

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _actor(session: Session) -> User:
    actor = User(
        id=uuid4(),
        phone=f"+998{uuid4().int % 1_000_000_000:09d}",
        is_active=True,
    )
    session.add(actor)
    session.flush()
    return actor


def _payment_payload() -> PaymentRecordedAuditPayload:
    return PaymentRecordedAuditPayload(
        amount=PaymentAmountUZS(Decimal("250000")),
        method=PaymentMethod.CARD,
        from_status=DebtStatus.ACTIVE,
        to_status=DebtStatus.PAID,
        debt_revision_after=DebtRevision(4),
    )


@pytest.mark.integration
def test_m14_audit_adapters_persist_exact_two_rows_without_committing(
    db_session: Session,
) -> None:
    actor = _actor(db_session)
    payment_id = UUID("11111111-1111-4111-8111-111111111111")
    debt_id = UUID("22222222-2222-4222-8222-222222222222")

    append_payment_recorded_audit(
        db_session,
        payment_id=payment_id,
        actor_user_id=actor.id,
        occurred_at=NOW,
        payload=_payment_payload(),
    )
    append_debt_paid_audit(
        db_session,
        debt_id=debt_id,
        actor_user_id=actor.id,
        occurred_at=NOW,
        payload=DebtPaidAuditPayload(debt_revision_after=DebtRevision(4)),
    )

    rows = tuple(db_session.scalars(select(AuditLog).order_by(AuditLog.event_type)))
    assert [(row.event_type, row.object_type, row.object_id) for row in rows] == [
        ("debt.paid", "debt", debt_id),
        ("payment.recorded", "payment", payment_id),
    ]
    assert rows[0].payload == {"source": "payment", "debt_revision_after": 4}
    assert rows[1].payload == {
        "amount_uzs": 250_000,
        "method": "card",
        "from_status": "active",
        "to_status": "paid",
        "debt_revision_after": 4,
    }
    assert db_session.in_transaction()


@pytest.mark.integration
def test_m14_audit_redaction_failure_rolls_back_the_outer_transaction(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    actor_id = uuid4()

    with pytest.raises(ValueError, match="unexpected metadata"):
        with factory.begin() as session:
            actor = User(
                id=actor_id,
                phone=f"+998{uuid4().int % 1_000_000_000:09d}",
                is_active=True,
            )
            session.add(actor)
            session.flush()
            from app.audit.contracts import (
                AuditActorKind,
                AuditEvent,
                AuditEventType,
                AuditObjectType,
            )

            append_audit_event(
                session,
                AuditEvent(
                    event_type=AuditEventType.PAYMENT_RECORDED,
                    actor_kind=AuditActorKind.USER,
                    actor_user_id=actor.id,
                    object_type=AuditObjectType.PAYMENT,
                    object_id=uuid4(),
                    occurred_at=NOW,
                    candidate_metadata={
                        **dict(_payment_payload().as_candidate_metadata()),
                        "idempotency_key": "private",
                    },
                ),
            )

    with factory() as verification:
        assert verification.get(User, actor_id) is None
        assert verification.scalar(select(AuditLog).limit(1)) is None
