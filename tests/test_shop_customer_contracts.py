from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    DebtlessShopCustomerPolicyProjection,
    DetachedShopCustomerAuthority,
    ExpectedShopUpdatedAt,
    LinkShopCustomerCommand,
    LockedEligibleShopCustomerTarget,
    ShopCustomerAggregate,
    ShopCustomerCreationSnapshot,
    ShopCustomerLinkOutcome,
    ShopCustomerLinkResult,
    ShopCustomerPathLocator,
    ShopCustomerPolicy,
    ShopCustomerPolicyReadPort,
    ShopCustomerPolicyUpdateOutcome,
    ShopCustomerPolicyUpdateResult,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
    ShopDefaultCreditPolicyUpdate,
    ShopDefaultCreditPolicyUpdateResult,
    ShopDefaultPolicyUpdateOutcome,
    TransientCanonicalShopCustomerPhone,
    UpdateShopCustomerPolicyCommand,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.values import (
    CreditLimitUzbekistanSom,
    CustomerId,
    MaxOpenDebts,
    ShopCustomerId,
)


def _policy(
    *, status: ShopCustomerListStatus = ShopCustomerListStatus.NORMAL
) -> ShopCustomerPolicy:
    return ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal("1000000")),
        max_open_debts=MaxOpenDebts(2),
        list_status=status,
    )


def _aggregate(*, created_at: datetime, updated_at: datetime) -> ShopCustomerAggregate:
    return ShopCustomerAggregate(
        id=ShopCustomerId(uuid4()),
        shop_id=ShopId(uuid4()),
        customer_id=CustomerId(uuid4()),
        policy=_policy(),
        revision=ShopCustomerRevision(1),
        created_by_user_id=UserId(uuid4()),
        created_at=created_at,
        updated_at=updated_at,
    )


def test_aggregate_validates_aware_timestamps_positive_revision_and_frozen_state() -> (
    None
):
    now = datetime.now(UTC)
    aggregate = _aggregate(created_at=now, updated_at=now + timedelta(seconds=1))

    assert aggregate.to_projection().revision == ShopCustomerRevision(1)
    with pytest.raises(FrozenInstanceError):
        aggregate.revision = ShopCustomerRevision(2)  # type: ignore[misc]
    with pytest.raises(ValueError, match="revision must be positive"):
        ShopCustomerRevision(0)
    with pytest.raises(ValueError, match="timezone-aware"):
        _aggregate(created_at=now.replace(tzinfo=None), updated_at=now)
    with pytest.raises(ValueError, match="update time is invalid"):
        _aggregate(created_at=now, updated_at=now - timedelta(seconds=1))


def test_aggregate_and_link_result_representations_redact_all_identifiers() -> None:
    now = datetime.now(UTC)
    aggregate = _aggregate(created_at=now, updated_at=now)
    result = ShopCustomerLinkResult(
        shop_customer_id=aggregate.id,
        outcome=ShopCustomerLinkOutcome.CREATED,
    )

    for raw_identifier in (
        aggregate.id.as_uuid(),
        aggregate.shop_id,
        aggregate.customer_id,
        aggregate.created_by_user_id,
    ):
        assert str(raw_identifier) not in repr(aggregate)
        assert str(raw_identifier) not in repr(result)

    assert result.emits_audit_event is True
    assert result.is_idempotent_replay is False
    replay = ShopCustomerLinkResult(
        shop_customer_id=aggregate.id,
        outcome=ShopCustomerLinkOutcome.ALREADY_LINKED,
    )
    assert replay.emits_audit_event is False
    assert replay.is_idempotent_replay is True


def test_creation_snapshot_requires_complete_normal_initial_policy() -> None:
    snapshot = ShopCustomerCreationSnapshot(policy=_policy())
    assert snapshot.policy.list_status is ShopCustomerListStatus.NORMAL

    with pytest.raises(ValueError, match="start with normal"):
        ShopCustomerCreationSnapshot(
            policy=_policy(status=ShopCustomerListStatus.BLACKLISTED)
        )
    with pytest.raises(ValueError, match="creation policy is invalid"):
        ShopCustomerCreationSnapshot(policy=None)  # type: ignore[arg-type]


def test_policy_requires_the_exact_three_typed_values() -> None:
    with pytest.raises(ValueError, match="credit limit is invalid"):
        ShopCustomerPolicy(  # type: ignore[arg-type]
            credit_limit=Decimal("1000000"),
            max_open_debts=MaxOpenDebts(2),
            list_status=ShopCustomerListStatus.NORMAL,
        )


def test_debtless_policy_projection_is_minimal_read_only_and_signal_only() -> None:
    now = datetime.now(UTC)
    aggregate = _aggregate(created_at=now, updated_at=now)
    projection = aggregate.to_debtless_policy_projection()

    assert isinstance(projection, DebtlessShopCustomerPolicyProjection)
    assert tuple(projection.__dataclass_fields__) == ("policy", "revision")
    assert projection.policy == aggregate.policy
    assert projection.revision == aggregate.revision
    assert projection.has_blacklist_signal is False
    for raw_identifier in (
        aggregate.id.as_uuid(),
        aggregate.shop_id,
        aggregate.customer_id,
        aggregate.created_by_user_id,
    ):
        assert str(raw_identifier) not in repr(projection)

    blacklisted = DebtlessShopCustomerPolicyProjection(
        policy=_policy(status=ShopCustomerListStatus.BLACKLISTED),
        revision=ShopCustomerRevision(3),
    )
    whitelisted = DebtlessShopCustomerPolicyProjection(
        policy=_policy(status=ShopCustomerListStatus.WHITELISTED),
        revision=ShopCustomerRevision(4),
    )
    assert blacklisted.has_blacklist_signal is True
    assert whitelisted.has_blacklist_signal is False
    assert {
        "allows",
        "bypass",
        "eligibility",
        "exposure",
        "open_debt_count",
    }.isdisjoint(vars(DebtlessShopCustomerPolicyProjection))


def test_debtless_policy_read_port_is_only_a_scoped_read_contract() -> None:
    projection = DebtlessShopCustomerPolicyProjection(
        policy=_policy(),
        revision=ShopCustomerRevision(1),
    )

    class ReadPort:
        def read_debtless_policy(
            self,
            *,
            shop_customer_id: ShopCustomerId,
        ) -> DebtlessShopCustomerPolicyProjection | None:
            _ = shop_customer_id
            return projection

    assert isinstance(ReadPort(), ShopCustomerPolicyReadPort)
    with pytest.raises(ValueError, match="list status is invalid"):
        ShopCustomerPolicy(  # type: ignore[arg-type]
            credit_limit=CreditLimitUzbekistanSom(Decimal("1000000")),
            max_open_debts=MaxOpenDebts(2),
            list_status="normal",
        )


def test_default_policy_is_a_complete_future_link_snapshot_only() -> None:
    defaults = ShopDefaultCreditPolicy()
    snapshot = defaults.for_new_link()

    assert snapshot.policy.credit_limit == CreditLimitUzbekistanSom(Decimal("1000000"))
    assert snapshot.policy.max_open_debts == MaxOpenDebts(2)
    assert snapshot.policy.list_status is ShopCustomerListStatus.NORMAL

    changed = ShopDefaultCreditPolicyUpdateResult(
        outcome=ShopDefaultPolicyUpdateOutcome.CHANGED,
        defaults=defaults,
    )
    no_change = ShopDefaultCreditPolicyUpdateResult(
        outcome=ShopDefaultPolicyUpdateOutcome.NO_CHANGE,
        defaults=defaults,
    )
    assert changed.emits_audit_event is True
    assert no_change.emits_audit_event is False
    assert changed.applies_to_new_links_only is True
    assert changed.changes_existing_shop_customers is False


def test_default_update_contract_requires_aware_token_and_hides_stale_defaults() -> (
    None
):
    now = datetime.now(UTC)
    command = ShopDefaultCreditPolicyUpdate(
        expected_updated_at=ExpectedShopUpdatedAt(now),
        new_defaults=ShopDefaultCreditPolicy(),
    )
    assert command.expected_updated_at.value == now
    assert (
        ShopDefaultCreditPolicyUpdateResult(
            outcome=ShopDefaultPolicyUpdateOutcome.STALE
        ).defaults
        is None
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        ExpectedShopUpdatedAt(now.replace(tzinfo=None))
    with pytest.raises(ValueError, match="cannot disclose defaults"):
        ShopDefaultCreditPolicyUpdateResult(
            outcome=ShopDefaultPolicyUpdateOutcome.STALE,
            defaults=ShopDefaultCreditPolicy(),
        )
    with pytest.raises(ValueError, match="requires a complete policy pair"):
        ShopDefaultCreditPolicyUpdateResult(
            outcome=ShopDefaultPolicyUpdateOutcome.CHANGED
        )


def test_link_command_uses_only_transient_phone_and_server_derived_authority() -> None:
    raw_phone = "+998 90-123 45 67"
    phone = TransientCanonicalShopCustomerPhone(raw_phone)
    authority = DetachedShopCustomerAuthority(
        actor_user_id=UserId(uuid4()),
        current_shop_id=ShopId(uuid4()),
    )
    command = LinkShopCustomerCommand(authority=authority, target_phone=phone)

    assert phone.for_server_lookup() == "+998901234567"
    assert tuple(command.__dataclass_fields__) == ("authority", "target_phone")
    for value in (raw_phone, phone.for_server_lookup(), str(authority.actor_user_id)):
        assert value not in repr(command)
        assert value not in repr(phone)
        assert value not in repr(authority)

    with pytest.raises(ValueError, match="phone is invalid"):
        TransientCanonicalShopCustomerPhone("not-a-phone")


def test_locked_target_and_every_ineligible_state_share_one_safe_outcome() -> None:
    target = LockedEligibleShopCustomerTarget(
        user_id=UserId(uuid4()),
        customer_id=CustomerId(uuid4()),
    )
    unavailable = ShopCustomerLinkResult.unavailable()

    assert str(target.user_id) not in repr(target)
    assert str(target.customer_id) not in repr(target)
    assert unavailable.outcome is ShopCustomerLinkOutcome.CUSTOMER_LINK_UNAVAILABLE
    assert unavailable.shop_customer_id is None
    assert unavailable.emits_audit_event is False
    assert unavailable.is_idempotent_replay is False

    for ineligible_state in (
        "invalid",
        "missing",
        "disabled",
        "draft",
        "unverified",
    ):
        assert ShopCustomerLinkResult.unavailable().outcome.value == (
            "CUSTOMER_LINK_UNAVAILABLE"
        )
        assert ineligible_state not in repr(ShopCustomerLinkResult.unavailable())

    with pytest.raises(ValueError, match="result identity is invalid"):
        ShopCustomerLinkResult(
            shop_customer_id=None,
            outcome=ShopCustomerLinkOutcome.CREATED,
        )


def test_policy_update_contract_has_complete_values_and_exact_revision_outcomes() -> (
    None
):
    raw_id = uuid4()
    command = UpdateShopCustomerPolicyCommand(
        locator=ShopCustomerPathLocator(ShopCustomerId(raw_id)),
        expected_revision=ShopCustomerRevision(7),
        new_policy=ShopCustomerPolicy(
            credit_limit=CreditLimitUzbekistanSom(Decimal("999999")),
            max_open_debts=MaxOpenDebts(3),
            list_status=ShopCustomerListStatus.WHITELISTED,
        ),
    )

    changed = ShopCustomerPolicyUpdateResult.changed(command)
    no_change = ShopCustomerPolicyUpdateResult.no_change(command)
    assert changed.outcome is ShopCustomerPolicyUpdateOutcome.CHANGED
    assert changed.revision == ShopCustomerRevision(8)
    assert changed.emits_audit_event is True
    assert changed.updates_timestamp is True
    assert no_change.outcome is ShopCustomerPolicyUpdateOutcome.NO_CHANGE
    assert no_change.revision == ShopCustomerRevision(7)
    assert no_change.emits_audit_event is False
    assert no_change.updates_timestamp is False
    assert str(raw_id) not in repr(command)


def test_policy_stale_and_unavailable_results_are_generic_and_mutation_free() -> None:
    stale = ShopCustomerPolicyUpdateResult.stale()
    missing = ShopCustomerPolicyUpdateResult.unavailable()
    cross_tenant = ShopCustomerPolicyUpdateResult.unavailable()

    assert stale.outcome is ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_CHANGED
    assert stale.revision is None
    assert stale.emits_audit_event is False
    assert stale.updates_timestamp is False
    assert missing.outcome is ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_UNAVAILABLE
    assert cross_tenant == missing
    assert missing.revision is None
    assert missing.emits_audit_event is False

    with pytest.raises(ValueError, match="update revision is invalid"):
        ShopCustomerPolicyUpdateResult(
            outcome=ShopCustomerPolicyUpdateOutcome.NO_CHANGE
        )
    with pytest.raises(ValueError, match="update revision is invalid"):
        ShopCustomerPolicyUpdateResult(
            outcome=ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_CHANGED,
            revision=ShopCustomerRevision(1),
        )
