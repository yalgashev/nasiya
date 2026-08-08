import inspect
from decimal import Decimal

import pytest

from app.debt.policy import (
    DebtCreationEligibilityDecision,
    DebtCreationEligibilityInput,
    GlobalHardBlockProjection,
    GlobalHardBlockReadPort,
    OpenDebtCount,
    OpenDebtExposure,
    decide_debt_creation_eligibility,
)
from app.debt.values import OriginalAmountUZS
from app.shop_customer.contracts import (
    DebtlessShopCustomerPolicyProjection,
    ShopCustomerPolicy,
    ShopCustomerRevision,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.values import CreditLimitUzbekistanSom, MaxOpenDebts


def _input(
    *,
    status: ShopCustomerListStatus = ShopCustomerListStatus.NORMAL,
    blocked: bool = False,
    exposure: str = "0",
    count: int = 0,
    credit_limit: str = "1000",
    max_open_debts: int = 2,
    original_amount: str = "100",
) -> DebtCreationEligibilityInput:
    return DebtCreationEligibilityInput(
        policy=DebtlessShopCustomerPolicyProjection(
            policy=ShopCustomerPolicy(
                credit_limit=CreditLimitUzbekistanSom(Decimal(credit_limit)),
                max_open_debts=MaxOpenDebts(max_open_debts),
                list_status=status,
            ),
            revision=ShopCustomerRevision(1),
        ),
        open_exposure=OpenDebtExposure(Decimal(exposure)),
        open_count=OpenDebtCount(count),
        global_hard_block=GlobalHardBlockProjection(blocked),
        original_amount=OriginalAmountUZS(Decimal(original_amount)),
    )


def test_eligibility_uses_original_exposure_and_inclusive_credit_limit() -> None:
    assert decide_debt_creation_eligibility(_input(exposure="900")) is (
        DebtCreationEligibilityDecision.ALLOWED
    )
    assert decide_debt_creation_eligibility(_input(exposure="901")) is (
        DebtCreationEligibilityDecision.CREDIT_LIMIT_EXCEEDED
    )


def test_blacklist_denies_and_whitelist_does_not_bypass_any_gate() -> None:
    assert (
        decide_debt_creation_eligibility(
            _input(status=ShopCustomerListStatus.BLACKLISTED)
        )
        is DebtCreationEligibilityDecision.CUSTOMER_BLACKLISTED
    )
    assert (
        decide_debt_creation_eligibility(
            _input(status=ShopCustomerListStatus.WHITELISTED, blocked=True)
        )
        is DebtCreationEligibilityDecision.CUSTOMER_RATING_BLOCKED
    )
    assert (
        decide_debt_creation_eligibility(
            _input(status=ShopCustomerListStatus.WHITELISTED, exposure="901")
        )
        is DebtCreationEligibilityDecision.CREDIT_LIMIT_EXCEEDED
    )


def test_hard_block_and_strict_max_open_debt_count_deny_creation() -> None:
    assert decide_debt_creation_eligibility(_input(blocked=True)) is (
        DebtCreationEligibilityDecision.CUSTOMER_RATING_BLOCKED
    )
    assert decide_debt_creation_eligibility(_input(count=2)) is (
        DebtCreationEligibilityDecision.MAX_OPEN_DEBTS
    )
    assert decide_debt_creation_eligibility(_input(count=3)) is (
        DebtCreationEligibilityDecision.MAX_OPEN_DEBTS
    )
    assert decide_debt_creation_eligibility(_input(count=1)) is (
        DebtCreationEligibilityDecision.ALLOWED
    )


def test_eligibility_value_contracts_reject_non_domain_values() -> None:
    for exposure in (Decimal("-1"), Decimal("1.0"), Decimal("NaN")):
        with pytest.raises(ValueError):
            OpenDebtExposure(exposure)
    with pytest.raises(TypeError, match="must be a Decimal"):
        OpenDebtExposure(0)  # type: ignore[arg-type]
    for count in (-1, True, 1.0):
        with pytest.raises(ValueError, match="cannot be negative"):
            OpenDebtCount(count)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="hard-block state"):
        GlobalHardBlockProjection(1)  # type: ignore[arg-type]


def test_global_hard_block_port_is_customer_scoped_not_tenant_link_scoped() -> None:
    parameters = inspect.signature(
        GlobalHardBlockReadPort.read_global_hard_block
    ).parameters
    assert tuple(parameters) == ("self", "customer_id")
