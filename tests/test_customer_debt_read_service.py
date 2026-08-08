from uuid import UUID

from app.auth.error_codes import ErrorCode
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.customer_read_service import (
    get_own_customer_debt_detail,
    list_own_customer_debts,
)
from app.debt.values import DebtId
from app.offers.enums import OfferLanguage


def test_missing_own_customer_authority_has_only_generic_read_outcomes() -> None:
    result = get_own_customer_debt_detail(
        object(),
        authority=None,
        debt_id=DebtId(UUID("11111111-1111-4111-8111-111111111111")),
        language=OfferLanguage.UZ_LATN,
    )

    assert list_own_customer_debts(object(), authority=None) == ()
    assert result.error is ErrorCode.DEBT_UNAVAILABLE
    assert result.detail is None


def test_customer_debt_authority_cannot_be_constructed_from_non_uuid_values() -> None:
    for value in ("not-a-uuid", None):
        try:
            CustomerDebtAuthority(user_id=value, customer_id=UUID(int=1))  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError("Customer debt authority accepted an unsafe identity")
