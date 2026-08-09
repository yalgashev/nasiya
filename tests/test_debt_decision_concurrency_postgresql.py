from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.db import create_database_session_factory
from app.debt.customer_accept_service import (
    AcceptCustomerDebtCommand,
    accept_own_customer_debt,
)
from app.debt.customer_authority import resolve_own_customer_debt_authority
from app.debt.customer_reject_service import (
    RejectCustomerDebtCommand,
    reject_own_customer_debt,
)
from app.debt.enums import DebtStatus
from app.debt.expiry_service import expire_pending_debts
from app.debt.models import Debt
from app.debt.tenant_cancel_service import (
    CancelTenantDebtCommand,
    cancel_tenant_debt,
)
from app.debt.values import DebtId, DebtRevision
from app.offers.enums import OfferLanguage
from app.offers.models import OfferAcceptance, OfferText
from tests.test_debt_creation_gates_postgresql import (
    NOW,
    _add_complete_offer,
    _add_debt,
    _seed_target,
)

pytestmark = pytest.mark.integration

_BARRIER_TIMEOUT_SECONDS = 5
_FUTURE_TIMEOUT_SECONDS = 10


@pytest.mark.parametrize(
    "pair",
    (
        "accept_reject",
        "accept_cancel",
        "reject_cancel",
        "accept_batch_expiry",
        "inline_batch_expiry",
    ),
)
def test_parallel_terminal_decisions_have_one_atomic_winner(
    m2_test_database: Engine, pair: str
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session)
        _add_debt(session, seed=seed, amount="100", status=DebtStatus.PENDING)
        _add_complete_offer(session, actor=seed.actor)
        debt = session.scalar(
            select(Debt).where(Debt.shop_customer_id == seed.shop_customer.id)
        )
        offer_text_id = session.scalar(
            select(OfferText.id).where(OfferText.language == OfferLanguage.RU.value)
        )
        assert debt is not None and offer_text_id is not None
        customer_authority = resolve_own_customer_debt_authority(
            session, authenticated_user=seed.target
        )
        assert customer_authority is not None
        debt_id = debt.id
        expiry = debt.pending_expires_at
        staff_authority = seed.authority

    start = Barrier(2)

    def accept_once():
        start.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        with factory.begin() as session:
            return accept_own_customer_debt(
                session,
                authority=customer_authority,
                command=AcceptCustomerDebtCommand(
                    debt_id=DebtId(debt_id),
                    expected_revision=DebtRevision(1),
                    language=OfferLanguage.RU,
                    displayed_offer_text_id=offer_text_id,
                    now=NOW + timedelta(hours=1),
                ),
                hard_block_clock=lambda: NOW + timedelta(hours=1),
            )

    def reject_once(*, at_expiry: bool = False):
        start.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        with factory.begin() as session:
            return reject_own_customer_debt(
                session,
                authority=customer_authority,
                command=RejectCustomerDebtCommand(
                    debt_id=DebtId(debt_id),
                    expected_revision=DebtRevision(1),
                    now=expiry if at_expiry else NOW + timedelta(hours=1),
                    raw_reason="private",
                ),
            )

    def cancel_once():
        start.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        with factory.begin() as session:
            return cancel_tenant_debt(
                session,
                authority=staff_authority,
                command=CancelTenantDebtCommand(
                    debt_id=DebtId(debt_id),
                    expected_revision=DebtRevision(1),
                    now=NOW + timedelta(hours=1),
                    raw_reason="private",
                ),
            )

    def batch_once():
        start.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return expire_pending_debts(factory, now=expiry, batch_size=1)

    actions = {
        "accept_reject": (accept_once, reject_once),
        "accept_cancel": (accept_once, cancel_once),
        "reject_cancel": (reject_once, cancel_once),
        "accept_batch_expiry": (accept_once, batch_once),
        "inline_batch_expiry": (lambda: reject_once(at_expiry=True), batch_once),
    }[pair]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(action) for action in actions)
        tuple(future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures)

    with factory.begin() as session:
        stored = session.get(Debt, debt_id)
        assert stored is not None
        expected_statuses = {
            "accept_reject": {DebtStatus.ACTIVE, DebtStatus.REJECTED},
            "accept_cancel": {DebtStatus.ACTIVE, DebtStatus.CANCELLED},
            "reject_cancel": {DebtStatus.REJECTED, DebtStatus.CANCELLED},
            "accept_batch_expiry": {DebtStatus.ACTIVE, DebtStatus.EXPIRED},
            "inline_batch_expiry": {DebtStatus.EXPIRED},
        }[pair]
        assert DebtStatus(stored.status) in expected_statuses
        assert stored.revision == 2
        acceptance_count = session.scalar(
            select(func.count()).select_from(OfferAcceptance)
        )
        assert acceptance_count == int(stored.status == DebtStatus.ACTIVE.value)
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
