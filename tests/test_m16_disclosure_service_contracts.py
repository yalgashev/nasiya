from inspect import getsource
from uuid import uuid4

from app.auth.error_codes import ErrorCode
from app.rating.disclosure_service import (
    DisclosureMutationRejected,
    DisclosurePersistenceError,
    read_risk_band_disclosure_snapshot,
    record_risk_band_disclosure,
)
from app.rating.targeting import (
    DetachedDisclosureActorContext,
    discover_tenant_disclosure_target,
    lock_tenant_disclosure_target,
)
from app.shop.enums import ShopRole


def test_disclosure_targeting_is_scalar_and_uses_exact_forward_lock_order() -> None:
    discovery = getsource(discover_tenant_disclosure_target)
    locking = getsource(lock_tenant_disclosure_target)

    assert "select(" in discovery
    assert "ShopCustomer.id" in discovery
    assert "Customer.user_id" in discovery
    assert "select(Customer)" not in discovery
    assert "select(ShopCustomer)" not in discovery
    predecessors = (
        "lock_shop_for_update",
        "lock_actor_shop_staff_for_update",
        "select(User)",
        "lock_existing_own_customer_for_update",
        "lock_shop_customer_by_tenant_locator",
    )
    positions = [locking.index(item) for item in predecessors]
    assert positions == sorted(positions)
    assert "is_platform_admin" not in locking
    assert "list_status" not in locking


def test_disclosure_mutation_orders_authority_key_clock_band_log_and_audit() -> None:
    source = getsource(record_risk_band_disclosure)
    ordered = (
        "lock_tenant_disclosure_target",
        "find_completed_key",
        "insert_or_resolve_key",
        "disclosure_clock()",
        "read_locked_current_risk_band",
        "insert_disclosure_view_locked",
        "append_risk_band_disclosure_audit",
    )
    positions = [source.index(item) for item in ordered]
    assert positions == sorted(positions)
    assert "hard_block_reader_factory=" not in source
    assert "global_hard_block_reader=" not in source
    for forbidden in (
        "session.commit(",
        "session.rollback(",
        "session.close(",
        "score",
        "history",
        "rating.changed",
        "notification",
    ):
        assert forbidden not in source


def test_historical_reader_is_stored_projection_only() -> None:
    source = getsource(read_risk_band_disclosure_snapshot)
    assert "read_tenant_disclosure_projection" in source
    for forbidden in (
        "lock_tenant_disclosure_target",
        "read_locked_current_risk_band",
        "tashkent_business_date",
        "disclosure_clock",
        "insert_",
        "append_",
    ):
        assert forbidden not in source


def test_disclosure_tokens_and_failures_are_redacted() -> None:
    actor = DetachedDisclosureActorContext(
        actor_user_id=uuid4(),
        current_shop_id=uuid4(),
        role_hint=ShopRole.CASHIER,
    )
    rejected = DisclosureMutationRejected(ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)
    fault = DisclosurePersistenceError()

    assert repr(actor) == "DetachedDisclosureActorContext(<redacted>)"
    assert str(rejected) == "Risk-band disclosure is unavailable"
    assert repr(rejected) == "DisclosureMutationRejected(<redacted>)"
    assert str(fault) == "Risk-band disclosure persistence failed"
    assert repr(fault) == "DisclosurePersistenceError(<redacted>)"
