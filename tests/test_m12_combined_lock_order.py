import inspect

from app.customer_activation.service import _issue_registration_otp
from app.shop.service import (
    _transition_shop_status,
    add_staff,
    change_staff_role,
    revoke_staff,
    suspend_shop,
)
from app.shop_customer.dependencies import get_detached_shop_customer_authority
from app.shop_customer.rate_limit import record_shop_customer_link_attempt
from app.shop_customer.service import (
    link_active_customer,
    update_shop_customer_policy,
    update_shop_default_credit_policy,
)
from app.shop_customer.targeting import resolve_locked_eligible_target
from app.telegram.service import unlink


def _ordered(source: str, *needles: str) -> bool:
    positions = tuple(source.index(needle) for needle in needles)
    return positions == tuple(sorted(positions))


def test_m12_mutations_follow_the_shared_forward_order() -> None:
    link = inspect.getsource(link_active_customer)
    target = inspect.getsource(resolve_locked_eligible_target)
    assert _ordered(link, "lock_shop_for_update", "lock_actor_shop_staff_for_update")
    assert _ordered(
        target,
        "lock_actor_and_target_users_for_update",
        "get_telegram_link_by_user_for_update",
        "lock_active_customer_for_target_user",
    )
    assert _ordered(
        link,
        "resolve_locked_eligible_target",
        "lock_shop_customer_by_pair",
    )

    defaults = inspect.getsource(update_shop_default_credit_policy)
    policy = inspect.getsource(update_shop_customer_policy)
    assert _ordered(
        defaults,
        "lock_shop_for_update",
        "lock_actor_shop_staff_for_update",
    )
    assert _ordered(
        policy,
        "lock_shop_for_update",
        "lock_actor_shop_staff_for_update",
        "lock_shop_customer_by_tenant_locator",
    )


def test_inherited_shop_and_registration_paths_remain_forward_ordered() -> None:
    for operation in (add_staff, change_staff_role, revoke_staff):
        source = inspect.getsource(operation)
        assert _ordered(
            source,
            "lock_shop_for_update",
            "_lock_staff_for_user_for_update",
        )
    assert "_transition_shop_status" in inspect.getsource(suspend_shop)
    assert "lock_shop_for_update" in inspect.getsource(_transition_shop_status)

    activation = inspect.getsource(_issue_registration_otp)
    assert _ordered(
        activation,
        "lock_outstanding_challenge_set_by_user",
        "with_for_update=True",
        "get_telegram_link_by_user_for_update",
        "lock_existing_own_customer_for_update",
        "select_current_registration_acceptance",
        "lock_complete_identity_revision",
        "lock_current_available_document",
    )
    relink_guard = inspect.getsource(unlink)
    assert _ordered(
        relink_guard,
        "lock_outstanding_telegram_link_token_set_by_user",
        "_lock_link_change_otp_state",
        "_lock_active_user",
        "get_telegram_link_by_user_for_update",
        "lock_existing_own_customer_for_update",
    )


def test_closed_prephases_and_lock_paths_have_no_correctness_workaround() -> None:
    authority = inspect.getsource(get_detached_shop_customer_authority)
    rate = inspect.getsource(record_shop_customer_link_attempt)
    assert "with session_factory.begin()" in authority
    assert "with session_factory.begin()" in rate
    sources = "\n".join(
        inspect.getsource(operation)
        for operation in (
            link_active_customer,
            update_shop_default_credit_policy,
            update_shop_customer_policy,
            add_staff,
            change_staff_role,
            revoke_staff,
            suspend_shop,
            _issue_registration_otp,
            unlink,
        )
    ).casefold()
    for forbidden in (
        "sleep(",
        "retry",
        "nowait",
        "lock_timeout",
        "pg_advisory",
        "pg_try_advisory",
    ):
        assert forbidden not in sources
