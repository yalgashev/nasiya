from pathlib import Path

import pytest

from app.shop.enums import ShopStatus
from app.shop.policy import can_mutate_shop, can_read_shop


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ShopStatus.ACTIVE, True),
        (ShopStatus.SUSPENDED, True),
    ],
)
def test_can_read_shop(status: ShopStatus, expected: bool) -> None:
    assert can_read_shop(status) is expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ShopStatus.ACTIVE, True),
        (ShopStatus.SUSPENDED, False),
    ],
)
def test_can_mutate_shop(status: ShopStatus, expected: bool) -> None:
    assert can_mutate_shop(status) is expected


def test_policy_does_not_mix_role_or_session_context_mutation() -> None:
    source = Path("app/shop/policy.py").read_text()

    assert "POST /shop/select" in source
    assert "role authorization" in source
    assert "ShopRole" not in source
    assert "from sqlalchemy" not in source
    assert "active_shop_id" not in source
