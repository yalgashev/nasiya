from inspect import getsource
from uuid import UUID

import pytest

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage
from app.payment import targeting
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.targeting import TenantPaymentTargetResult
from app.shop.enums import ShopRole


def _actor() -> DetachedPaymentActorContext:
    return DetachedPaymentActorContext(
        actor_user_id=UUID("11111111-1111-4111-8111-111111111111"),
        current_shop_id=UUID("22222222-2222-4222-8222-222222222222"),
        role_hint=ShopRole.OWNER,
        language=DebtWebLanguage.UZ_LATN,
    )


def test_payment_targeting_is_nonlocking_then_exact_forward_predecessor_order() -> None:
    discovery = getsource(targeting.discover_tenant_payment_target)
    locking = getsource(targeting.lock_tenant_payment_predecessors)

    assert "with_for_update" not in discovery
    assert locking.index("lock_shop_for_update") < locking.index(
        "lock_actor_shop_staff_for_update"
    )
    assert locking.index("lock_actor_shop_staff_for_update") < locking.index(
        "select(User)"
    )
    assert locking.index("select(User)") < locking.index(
        "lock_existing_own_customer_for_update"
    )
    assert locking.index("lock_existing_own_customer_for_update") < locking.index(
        "lock_shop_customer_by_tenant_locator"
    )
    assert "lock_active_customer_for_target_user" not in locking
    assert "Telegram" not in locking
    assert "role_hint" not in locking
    assert "session.add" not in locking
    assert "session.flush" not in locking
    assert "session.commit" not in locking
    assert "with_for_update" not in locking.split("still_current =", maxsplit=1)[1]


def test_payment_targeting_denies_unknown_roles_and_redacts_results() -> None:
    assert all(
        targeting._payment_staff_role_allowed(role.value)  # noqa: SLF001
        for role in (ShopRole.OWNER, ShopRole.MANAGER, ShopRole.CASHIER)
    )
    assert not targeting._payment_staff_role_allowed("auditor")  # noqa: SLF001
    assert not targeting._payment_staff_role_allowed(None)  # noqa: SLF001

    unavailable = TenantPaymentTargetResult(error=ErrorCode.DEBT_UNAVAILABLE)
    assert "DEBT_UNAVAILABLE" in repr(unavailable)
    assert "11111111" not in repr(unavailable)
    with pytest.raises(ValueError, match="result is invalid"):
        TenantPaymentTargetResult(error=None)

    with pytest.raises(TypeError, match="DetachedPaymentActorContext"):
        targeting.discover_tenant_payment_target(
            object(),  # type: ignore[arg-type]
            actor=object(),  # type: ignore[arg-type]
            debt_id=object(),  # type: ignore[arg-type]
        )

    assert _actor().role_hint is ShopRole.OWNER
