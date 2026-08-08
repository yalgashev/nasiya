"""Locked ShopCustomer policy and open-set gate for debt creation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.debt.policy import (
    DebtCreationEligibilityDecision,
    DebtCreationEligibilityInput,
    GlobalHardBlockProjection,
    GlobalHardBlockReadPort,
    decide_debt_creation_eligibility,
)
from app.debt.repository import (
    SqlAlchemyDebtOpenSetReader,
    mark_debt_predecessor_locked,
)
from app.debt.targeting import LockedDebtTarget, _validate_locked_debt_target
from app.debt.values import CustomerId, OriginalAmountUZS, ShopCustomerId
from app.shop_customer.contracts import (
    DebtlessShopCustomerPolicyProjection,
    ShopCustomerPolicy,
    ShopCustomerRevision,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.values import CreditLimitUzbekistanSom, MaxOpenDebts

_DECISION_ERRORS = {
    DebtCreationEligibilityDecision.CUSTOMER_BLACKLISTED: (
        ErrorCode.CUSTOMER_BLACKLISTED
    ),
    DebtCreationEligibilityDecision.CUSTOMER_RATING_BLOCKED: (
        ErrorCode.CUSTOMER_RATING_BLOCKED
    ),
    DebtCreationEligibilityDecision.CREDIT_LIMIT_EXCEEDED: (
        ErrorCode.CREDIT_LIMIT_EXCEEDED
    ),
    DebtCreationEligibilityDecision.MAX_OPEN_DEBTS: ErrorCode.MAX_OPEN_DEBTS,
}


@dataclass(frozen=True, slots=True)
class DebtCreationGateResult:
    decision: DebtCreationEligibilityDecision
    error: ErrorCode | None

    def __post_init__(self) -> None:
        expected = _DECISION_ERRORS.get(self.decision)
        if self.error is not expected:
            raise ValueError("Debt creation gate result is inconsistent")


class _LockedCustomerNoHardBlockReader:
    """M13's authoritative read seam: no reachable row can assert a block."""

    def __init__(self, session: Session, *, locked_target: LockedDebtTarget) -> None:
        self._session = session
        self._locked_target = _validate_locked_debt_target(session, locked_target)

    def read_global_hard_block(
        self, *, customer_id: CustomerId
    ) -> GlobalHardBlockProjection:
        if not isinstance(customer_id, UUID):
            raise TypeError("customer_id must be a CustomerId")
        target = _validate_locked_debt_target(self._session, self._locked_target)
        if customer_id != target._locked_shop_customer.row.customer_id:
            raise ValueError("Hard-block customer is not the locked target")
        return GlobalHardBlockProjection(is_blocked=False)


def read_locked_debtless_policy(
    session: Session, *, locked_target: LockedDebtTarget
) -> DebtlessShopCustomerPolicyProjection:
    """Project the complete policy without mutating the locked relationship."""

    target = _validate_locked_debt_target(session, locked_target)
    row = target._locked_shop_customer.row
    return DebtlessShopCustomerPolicyProjection(
        policy=ShopCustomerPolicy(
            credit_limit=CreditLimitUzbekistanSom(row.credit_limit_uzs),
            max_open_debts=MaxOpenDebts(row.max_open_debts),
            list_status=ShopCustomerListStatus(row.list_status),
        ),
        revision=ShopCustomerRevision(row.revision),
    )


def evaluate_locked_debt_creation(
    session: Session,
    *,
    locked_target: LockedDebtTarget,
    original_amount: OriginalAmountUZS,
    global_hard_block_reader: GlobalHardBlockReadPort | None = None,
) -> DebtCreationGateResult:
    """Apply blacklist, inclusive credit, and strict count limits."""

    if not isinstance(original_amount, OriginalAmountUZS):
        raise TypeError("original_amount must be an OriginalAmountUZS")
    target = _validate_locked_debt_target(session, locked_target)
    if global_hard_block_reader is None:
        hard_block_reader: GlobalHardBlockReadPort = _LockedCustomerNoHardBlockReader(
            session, locked_target=target
        )
    elif isinstance(global_hard_block_reader, GlobalHardBlockReadPort):
        hard_block_reader = global_hard_block_reader
    else:
        raise TypeError(
            "global_hard_block_reader must implement GlobalHardBlockReadPort"
        )
    row = target._locked_shop_customer.row
    predecessor = mark_debt_predecessor_locked(
        session, locked_shop_customer=target._locked_shop_customer
    )
    reader = SqlAlchemyDebtOpenSetReader(session, locked_predecessor=predecessor)
    shop_customer_id = ShopCustomerId(row.id)
    eligibility = DebtCreationEligibilityInput(
        policy=read_locked_debtless_policy(session, locked_target=target),
        open_exposure=reader.read_open_debt_exposure(shop_customer_id=shop_customer_id),
        open_count=reader.read_open_debt_count(shop_customer_id=shop_customer_id),
        global_hard_block=hard_block_reader.read_global_hard_block(
            customer_id=CustomerId(row.customer_id)
        ),
        original_amount=original_amount,
    )
    decision = decide_debt_creation_eligibility(eligibility)
    return DebtCreationGateResult(
        decision=decision,
        error=_DECISION_ERRORS.get(decision),
    )
