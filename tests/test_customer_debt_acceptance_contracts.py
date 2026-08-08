from datetime import UTC, datetime
from inspect import getsource
from uuid import UUID

from app.debt.acceptance_repository import get_locked_debt_acceptance_replay
from app.debt.customer_accept_service import (
    AcceptCustomerDebtCommand,
    accept_own_customer_debt,
)
from app.debt.customer_decision_targeting import (
    lock_customer_debt_predecessors,
)
from app.debt.values import DebtId, DebtRevision
from app.offers.enums import OfferLanguage
from app.offers.repository import SqlAlchemyOfferAcceptanceRepository


def test_accept_command_redacts_debt_offer_and_user_agent_inputs() -> None:
    debt_id = UUID("11111111-1111-4111-8111-111111111111")
    text_id = UUID("22222222-2222-4222-8222-222222222222")
    command = AcceptCustomerDebtCommand(
        debt_id=DebtId(debt_id),
        expected_revision=DebtRevision(1),
        language=OfferLanguage.UZ_LATN,
        displayed_offer_text_id=text_id,
        now=datetime(2026, 8, 8, tzinfo=UTC),
        raw_user_agent="SECRET-UA",
    )

    rendered = repr(command)
    assert str(debt_id) not in rendered
    assert str(text_id) not in rendered
    assert "SECRET-UA" not in rendered


def test_customer_accept_lock_order_is_forward_and_session_is_borrowed() -> None:
    predecessors = getsource(lock_customer_debt_predecessors)
    service = getsource(accept_own_customer_debt)

    assert (
        predecessors.index("lock_shop_for_update")
        < predecessors.index("select(User)")
        < predecessors.index("get_telegram_link_by_user_for_update")
        < predecessors.index("select(Customer)")
    )
    assert service.index("lock_customer_debt_offer") < service.index(
        "lock_customer_debt_after_offer"
    )
    for forbidden in (".commit(", ".rollback(", ".close("):
        assert forbidden not in service


def test_debt_replay_reads_acceptance_without_out_of_order_row_lock() -> None:
    composition = getsource(get_locked_debt_acceptance_replay)
    repository = getsource(SqlAlchemyOfferAcceptanceRepository.get_debt_acceptance)

    assert "get_debt_acceptance" in composition
    assert "with_for_update" not in composition
    assert "with_for_update" not in repository
