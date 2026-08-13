from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.db import create_database_session_factory
from app.debt.models import Debt
from app.debt.values import DebtId, DebtRevision
from app.payment.contracts import PaymentVoidAggregate
from app.payment.enums import PaymentVoidReason
from app.payment.models import Payment, PaymentVoid
from app.payment.repository import (
    insert_payment_void,
    latest_non_voided_payment_for_tenant_debt,
    non_voided_posted_payment_total,
    payment_void_exists_for_tenant_debt,
)
from app.payment.values import PaymentId
from app.shop.values import ShopId
from app.shop_customer.values import ShopCustomerId
from tests.test_payment_targeting_postgresql import NOW, _seed_one

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_non_voided_current_as_of_latest_and_tenant_chain_are_exact(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    first_id, latest_id, void_id = uuid4(), uuid4(), uuid4()

    with factory.begin() as session:
        debt = session.get_one(Debt, debt_id, with_for_update=True)
        debt.revision = 5
        debt.updated_at = NOW + timedelta(hours=4)
        session.add_all(
            (
                Payment(
                    id=first_id,
                    debt_id=debt_id,
                    recorded_by_user_id=actor_id,
                    amount_uzs=Decimal("300"),
                    method="cash",
                    debt_revision_after=3,
                    created_at=NOW + timedelta(hours=2),
                ),
                Payment(
                    id=latest_id,
                    debt_id=debt_id,
                    recorded_by_user_id=actor_id,
                    amount_uzs=Decimal("400"),
                    method="card",
                    debt_revision_after=4,
                    created_at=NOW + timedelta(hours=3),
                ),
            )
        )
        session.flush()
        appended = insert_payment_void(
            session,
            locked_debt=debt,
            payment_void=PaymentVoidAggregate(
                id=void_id,
                payment_id=PaymentId(latest_id),
                debt_id=DebtId(debt_id),
                shop_customer_id=ShopCustomerId(relation_id),
                source_payment_revision=DebtRevision(4),
                debt_revision_after=DebtRevision(5),
                voided_by_user_id=actor_id,
                reason=PaymentVoidReason.DUPLICATE_PAYMENT,
                voided_at=NOW + timedelta(hours=4),
            ),
        )
        assert appended == void_id

    with factory() as session:
        arguments = {
            "shop_id": ShopId(shop_id),
            "shop_customer_id": ShopCustomerId(relation_id),
            "debt_id": DebtId(debt_id),
        }
        assert non_voided_posted_payment_total(session, **arguments).value == 300
        assert (
            non_voided_posted_payment_total(
                session, **arguments, as_of_revision=DebtRevision(4)
            ).value
            == 700
        )
        assert (
            non_voided_posted_payment_total(
                session, **arguments, as_of_revision=DebtRevision(5)
            ).value
            == 300
        )
        latest = latest_non_voided_payment_for_tenant_debt(session, **arguments)
        assert latest is not None and latest.id == PaymentId(first_id)
        assert payment_void_exists_for_tenant_debt(
            session,
            **arguments,
            payment_id=PaymentId(latest_id),
        )
        foreign = {**arguments, "shop_id": ShopId(uuid4())}
        assert non_voided_posted_payment_total(session, **foreign).value == 0
        assert latest_non_voided_payment_for_tenant_debt(session, **foreign) is None
        assert not payment_void_exists_for_tenant_debt(
            session,
            **foreign,
            payment_id=PaymentId(latest_id),
        )
        assert session.scalar(select(func.count()).select_from(PaymentVoid)) == 1


def test_new_repositories_never_own_session_or_expose_raw_tokens() -> None:
    source = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "app/payment/repository.py",
            "app/rating/repository.py",
            "app/idempotency/repository.py",
        )
    )
    for forbidden in ("session.commit(", "session.rollback(", "session.close("):
        assert forbidden not in source
    assert "raw_key" not in source
    assert "PaymentVoid(<redacted>)" in (ROOT / "app/payment/models.py").read_text(
        encoding="utf-8"
    )
