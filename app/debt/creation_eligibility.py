"""Locked ShopCustomer policy and open-set gate for debt creation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.debt.overdue_ports import LockedCustomerGlobalHardBlockReadPort
from app.debt.policy import (
    DebtCreationEligibilityDecision,
    DebtCreationEligibilityInput,
    decide_debt_creation_eligibility,
)
from app.debt.repository import (
    LockedCustomerHardBlockScope,
    LockedDebtPredecessor,
    SqlAlchemyDebtOpenSetReader,
    locked_customer_global_hard_block_reader_factory,
    mark_debt_predecessor_locked,
    mark_locked_customer_hard_block_scope,
)
from app.debt.targeting import (
    LockedDebtTarget,
    _validate_locked_debt_target,
    locked_debt_target_customer,
)
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

DebtOpenSetReaderFactory = Callable[
    [Session, LockedDebtPredecessor],
    SqlAlchemyDebtOpenSetReader,
]
HardBlockReaderFactory = Callable[
    [Session, LockedCustomerHardBlockScope], LockedCustomerGlobalHardBlockReadPort
]


@dataclass(frozen=True, slots=True)
class DebtCreationGateResult:
    decision: DebtCreationEligibilityDecision
    error: ErrorCode | None

    def __post_init__(self) -> None:
        expected = _DECISION_ERRORS.get(self.decision)
        if self.error is not expected:
            raise ValueError("Debt creation gate result is inconsistent")


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
    as_of_business_date: date,
    global_hard_block_reader: LockedCustomerGlobalHardBlockReadPort | None = None,
    open_set_reader_factory: DebtOpenSetReaderFactory | None = None,
    hard_block_reader_factory: HardBlockReaderFactory = (
        locked_customer_global_hard_block_reader_factory
    ),
) -> DebtCreationGateResult:
    """Apply blacklist, inclusive credit, and strict count limits."""

    if not isinstance(original_amount, OriginalAmountUZS):
        raise TypeError("original_amount must be an OriginalAmountUZS")
    target = _validate_locked_debt_target(session, locked_target)
    if global_hard_block_reader is None:
        if not callable(hard_block_reader_factory):
            raise TypeError("hard_block_reader_factory must be callable")
        scope = mark_locked_customer_hard_block_scope(
            session,
            locked_customer=locked_debt_target_customer(session, locked_target=target),
        )
        hard_block_reader = hard_block_reader_factory(session, scope)
    elif isinstance(global_hard_block_reader, LockedCustomerGlobalHardBlockReadPort):
        hard_block_reader = global_hard_block_reader
    else:
        raise TypeError("global_hard_block_reader must implement locked Customer port")
    row = target._locked_shop_customer.row
    predecessor = mark_debt_predecessor_locked(
        session, locked_shop_customer=target._locked_shop_customer
    )
    if open_set_reader_factory is None:
        reader = SqlAlchemyDebtOpenSetReader(
            session,
            locked_predecessor=predecessor,
        )
    elif callable(open_set_reader_factory):
        reader = open_set_reader_factory(session, predecessor)
    else:
        raise TypeError("open_set_reader_factory must be callable")
    shop_customer_id = ShopCustomerId(row.id)
    eligibility = DebtCreationEligibilityInput(
        policy=read_locked_debtless_policy(session, locked_target=target),
        open_exposure=reader.read_open_debt_exposure(shop_customer_id=shop_customer_id),
        open_count=reader.read_open_debt_count(shop_customer_id=shop_customer_id),
        global_hard_block=hard_block_reader.read_global_hard_block(
            customer_id=CustomerId(row.customer_id),
            as_of_business_date=as_of_business_date,
        ),
        original_amount=original_amount,
    )
    decision = decide_debt_creation_eligibility(eligibility)
    return DebtCreationGateResult(
        decision=decision,
        error=_DECISION_ERRORS.get(decision),
    )
