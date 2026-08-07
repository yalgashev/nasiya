from enum import StrEnum


class ShopCustomerListStatus(StrEnum):
    NORMAL = "normal"
    WHITELISTED = "whitelisted"
    BLACKLISTED = "blacklisted"


def parse_shop_customer_list_status(value: str) -> ShopCustomerListStatus:
    try:
        return ShopCustomerListStatus(value)
    except (TypeError, ValueError):
        raise ValueError("Shop customer list status is invalid") from None
