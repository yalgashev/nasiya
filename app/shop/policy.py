"""Shop status policy for tenant business operations.

Session/context mutation, including POST /shop/select, is outside this policy.
This module does not perform role authorization.
"""

from app.shop.enums import ShopStatus


def can_read_shop(status: ShopStatus) -> bool:
    return status in {ShopStatus.ACTIVE, ShopStatus.SUSPENDED}


def can_mutate_shop(status: ShopStatus) -> bool:
    return status is ShopStatus.ACTIVE
