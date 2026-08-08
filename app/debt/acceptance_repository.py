"""Debt-scoped legal acceptance composition over locked M13 predecessors."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.debt.customer_decision_targeting import (
    LockedCustomerDebt,
    LockedCustomerDebtOffer,
    _validate_locked_customer_debt,
    _validate_offer,
)
from app.debt.enums import DebtStatus
from app.debt.values import DebtId, DebtRevision
from app.offers.contracts import (
    DebtOfferAcceptanceSnapshot,
    StoredDebtOfferAcceptance,
)
from app.offers.enums import OfferLanguage
from app.offers.repository import SqlAlchemyOfferAcceptanceRepository
from app.offers.user_agent import normalize_offer_acceptance_user_agent

__all__ = (
    "append_locked_debt_acceptance",
    "get_locked_debt_acceptance_replay",
)


def append_locked_debt_acceptance(
    session: Session,
    *,
    locked_debt: LockedCustomerDebt,
    locked_offer: LockedCustomerDebtOffer,
    language: OfferLanguage,
    displayed_offer_text_id: UUID,
    accepted_at: datetime,
    raw_user_agent: str | None,
) -> StoredDebtOfferAcceptance:
    """Insert one exact snapshot; only the named debt unique is recoverable."""

    debt = _validate_locked_customer_debt(session, locked_debt)
    offer = _validate_offer(session, locked_offer)
    if offer._predecessors is not debt._predecessors:
        raise ValueError("Locked debt and offer predecessors do not match")
    if debt.row.status != DebtStatus.PENDING.value:
        raise ValueError("Debt acceptance requires a locked pending Debt")
    authority = debt._predecessors.authority
    candidate = debt._predecessors.candidate
    if (
        debt.row.id != candidate.debt_id
        or debt.row.shop_customer_id != debt.shop_customer.id
        or debt.shop_customer.customer_id != authority.customer_id
    ):
        raise ValueError("Locked debt ownership is invalid")
    snapshot = DebtOfferAcceptanceSnapshot.from_current_offer(
        user_id=authority.user_id,
        debt_id=DebtId(debt.row.id),
        resolved_offer=offer.resolved,
        language=language,
        displayed_offer_text_id=displayed_offer_text_id,
        accepted_at=accepted_at,
        user_agent=normalize_offer_acceptance_user_agent(raw_user_agent),
    )
    return SqlAlchemyOfferAcceptanceRepository(session).create_debt_acceptance(
        acceptance=snapshot
    )


def get_locked_debt_acceptance_replay(
    session: Session,
    *,
    locked_debt: LockedCustomerDebt,
    language: OfferLanguage,
    displayed_offer_text_id: UUID,
    expected_revision: DebtRevision,
) -> StoredDebtOfferAcceptance | None:
    """Read, but never row-lock, the acceptance protected by the locked Debt."""

    debt = _validate_locked_customer_debt(session, locked_debt)
    if not isinstance(language, OfferLanguage):
        raise TypeError("language must be an OfferLanguage")
    if not isinstance(displayed_offer_text_id, UUID):
        raise TypeError("displayed_offer_text_id must be a UUID")
    if not isinstance(expected_revision, DebtRevision):
        raise TypeError("expected_revision must be a DebtRevision")
    if debt.row.status != DebtStatus.ACTIVE.value:
        return None
    stored = SqlAlchemyOfferAcceptanceRepository(session).get_debt_acceptance(
        debt_id=DebtId(debt.row.id)
    )
    if stored is None:
        return None
    acceptance = stored.acceptance
    authority = debt._predecessors.authority
    exact = (
        debt.row.revision == expected_revision.value + 1
        and debt.row.accepted_at == acceptance.accepted_at
        and acceptance.user_id == authority.user_id
        and acceptance.debt_id.as_uuid() == debt.row.id
        and acceptance.language is language
        and acceptance.offer_text_id == displayed_offer_text_id
    )
    return stored if exact else None
