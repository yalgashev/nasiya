from dataclasses import fields
from inspect import getsource
from uuid import UUID

import pytest

from app.debt.presentation import DebtWebLanguage
from app.payment.dependencies import (
    DetachedPaymentActorContext,
    get_detached_current_shop_payment_actor_context,
)
from app.shop.enums import ShopRole


def test_detached_payment_actor_context_is_scalar_redacted_and_exact() -> None:
    actor_id = UUID("11111111-1111-4111-8111-111111111111")
    shop_id = UUID("22222222-2222-4222-8222-222222222222")
    context = DetachedPaymentActorContext(
        actor_user_id=actor_id,
        current_shop_id=shop_id,
        role_hint=ShopRole.CASHIER,
        language=DebtWebLanguage.RU,
    )

    assert tuple(field.name for field in fields(context)) == (
        "actor_user_id",
        "current_shop_id",
        "role_hint",
        "language",
    )
    assert str(actor_id) not in repr(context)
    assert str(shop_id) not in repr(context)
    assert "csrf" not in repr(context).casefold()
    assert "session" not in repr(context).casefold()

    with pytest.raises(ValueError, match="actor user ID"):
        DetachedPaymentActorContext(
            actor_user_id="not-a-uuid",  # type: ignore[arg-type]
            current_shop_id=shop_id,
            role_hint=ShopRole.CASHIER,
            language=DebtWebLanguage.RU,
        )


def test_payment_actor_prephase_is_closed_and_client_shop_is_not_an_authority() -> None:
    source = getsource(get_detached_current_shop_payment_actor_context)

    assert "with session_factory.begin()" in source
    assert "get_current_session_context" in source
    assert "await validate_csrf" in source
    assert "resolve_current_shop" in source
    assert "request.query_params" not in source
    assert "request.path_params" not in source
    assert "request.form" not in source
    assert source.index("with session_factory.begin()") < source.index(
        "if current.status is not"
    )
