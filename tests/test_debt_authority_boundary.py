from dataclasses import fields
from inspect import getsource
from uuid import UUID

from app.auth.deps import CurrentSessionStatus
from app.debt.dependencies import (
    DebtRequestContext,
    DetachedDebtActorAuthority,
    get_detached_current_shop_debt_actor_authority,
    lock_live_debt_actor,
)


def test_detached_debt_authority_is_server_derived_and_safe() -> None:
    authority = DetachedDebtActorAuthority(
        status=CurrentSessionStatus.AUTHENTICATED,
        actor_user_id=UUID("11111111-1111-4111-8111-111111111111"),
        current_shop_id=UUID("22222222-2222-4222-8222-222222222222"),
        request_context=DebtRequestContext(is_htmx=True),
    )

    assert tuple(field.name for field in fields(authority)) == (
        "status",
        "actor_user_id",
        "current_shop_id",
        "request_context",
    )
    assert authority.is_authenticated
    rendered = repr(authority)
    assert "11111111" not in rendered
    assert "22222222" not in rendered
    assert "session" not in rendered.casefold()
    assert "is_platform_admin" not in getsource(DetachedDebtActorAuthority)


def test_debt_authority_prephase_closes_before_live_domain_recheck() -> None:
    prephase = getsource(get_detached_current_shop_debt_actor_authority)
    recheck = getsource(lock_live_debt_actor)

    assert "with session_factory.begin()" in prephase
    assert "get_current_session_context" in prephase
    assert "await validate_csrf" in prephase
    assert "resolve_current_shop" in prephase
    assert "lock_shop_for_update" in recheck
    assert "lock_actor_shop_staff_for_update" in recheck
    assert "is_platform_admin" not in recheck
