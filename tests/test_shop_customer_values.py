from decimal import Decimal
from uuid import uuid4

import pytest

from app.shop.values import ShopId, UserId
from app.shop_customer.enums import (
    ShopCustomerListStatus,
    parse_shop_customer_list_status,
)
from app.shop_customer.values import (
    DEFAULT_CREDIT_LIMIT_UZS,
    DEFAULT_MAX_OPEN_DEBTS,
    MAX_CREDIT_LIMIT_UZS,
    MAX_OPEN_DEBTS,
    MIN_CREDIT_LIMIT_UZS,
    CreditLimitUzbekistanSom,
    CustomerId,
    MaxOpenDebts,
    ShopCustomerId,
    parse_credit_limit_uzs,
)


def test_list_status_vocabulary_and_parser_are_exact() -> None:
    assert tuple(ShopCustomerListStatus) == (
        ShopCustomerListStatus.NORMAL,
        ShopCustomerListStatus.WHITELISTED,
        ShopCustomerListStatus.BLACKLISTED,
    )
    assert parse_shop_customer_list_status("normal") is ShopCustomerListStatus.NORMAL
    assert (
        parse_shop_customer_list_status("whitelisted")
        is ShopCustomerListStatus.WHITELISTED
    )
    assert (
        parse_shop_customer_list_status("blacklisted")
        is ShopCustomerListStatus.BLACKLISTED
    )

    for malformed in ("NORMAL", "blocked", "", None):
        with pytest.raises(ValueError, match="list status is invalid"):
            parse_shop_customer_list_status(malformed)  # type: ignore[arg-type]


def test_identifiers_reuse_shop_aliases_and_redact_shop_customer_identity() -> None:
    raw_identifier = uuid4()
    shop_customer_id = ShopCustomerId(raw_identifier)

    assert ShopId(raw_identifier) == raw_identifier
    assert UserId(raw_identifier) == raw_identifier
    assert CustomerId(raw_identifier) == raw_identifier
    assert shop_customer_id.as_uuid() == raw_identifier
    assert str(raw_identifier) not in repr(shop_customer_id)
    assert str(raw_identifier) not in str(shop_customer_id)

    with pytest.raises(ValueError, match="identity is invalid"):
        ShopCustomerId("not-a-uuid")  # type: ignore[arg-type]


def test_credit_limit_accepts_only_bounded_whole_decimal_values() -> None:
    assert MIN_CREDIT_LIMIT_UZS == Decimal("0")
    assert MAX_CREDIT_LIMIT_UZS == Decimal("1000000000000")
    assert CreditLimitUzbekistanSom(Decimal("0")).value == Decimal("0")
    assert (
        CreditLimitUzbekistanSom(Decimal("1000000000000")).value == MAX_CREDIT_LIMIT_UZS
    )
    assert DEFAULT_CREDIT_LIMIT_UZS.value == Decimal("1000000")

    for malformed in (
        Decimal("-1"),
        Decimal("1000000000001"),
        Decimal("0.0"),
        Decimal("1.25"),
        Decimal("1E+3"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ):
        with pytest.raises(ValueError):
            CreditLimitUzbekistanSom(malformed)

    for non_decimal in (0, 1.0, "1000000", True):
        with pytest.raises(TypeError, match="must be a Decimal"):
            CreditLimitUzbekistanSom(non_decimal)  # type: ignore[arg-type]


def test_credit_limit_parser_accepts_ascii_whole_uzs_only() -> None:
    assert parse_credit_limit_uzs("0").value == Decimal("0")
    assert parse_credit_limit_uzs("0001000000").value == Decimal("1000000")
    assert parse_credit_limit_uzs("1000000000000").value == MAX_CREDIT_LIMIT_UZS

    for malformed in (
        "",
        " 1",
        "1 ",
        "+1",
        "-1",
        "1.0",
        "1e3",
        "1E3",
        "1_000",
        "1,000",
        "１２３",
        "1000000000001",
        None,
        1,
        1.0,
    ):
        with pytest.raises(ValueError):
            parse_credit_limit_uzs(malformed)  # type: ignore[arg-type]


def test_max_open_debts_is_bounded_integer_contract() -> None:
    assert MAX_OPEN_DEBTS == 100
    assert MaxOpenDebts(1).value == 1
    assert MaxOpenDebts(100).value == 100
    assert DEFAULT_MAX_OPEN_DEBTS.value == 2

    for malformed in (0, 101, -1, True, 1.0, "1"):
        with pytest.raises(ValueError, match="between 1 and 100"):
            MaxOpenDebts(malformed)  # type: ignore[arg-type]
