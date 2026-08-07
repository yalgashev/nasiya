import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.auth.models import User
from app.auth.repository import lock_actor_and_target_users_for_update
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop.service import revoke_staff, suspend_shop
from app.shop.values import ShopId, ShopStaffId, UserId
from app.shop_customer.contracts import (
    DetachedShopCustomerAuthority,
    LinkShopCustomerCommand,
    ShopCustomerLinkOutcome,
    TransientCanonicalShopCustomerPhone,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.service import (
    coordinate_link_active_customer,
    link_active_customer,
)
from app.shop_customer.targeting import resolve_locked_eligible_target
from app.telegram.models import TelegramLink
from tests.test_shop_customer_link_service_postgresql import (
    NOW,
    _factory,
    _seed_eligible,
)
from tests.test_shop_customer_repository_postgresql import _add_user


def _run_pair(left, right):
    barrier = Barrier(2)

    def run(operation):
        barrier.wait()
        return operation()

    with ThreadPoolExecutor(max_workers=2) as executor:
        return tuple(executor.map(run, (left, right)))


@pytest.mark.integration
def test_parallel_same_pair_is_exactly_one_row_and_one_audit(
    m2_test_database: Engine,
) -> None:
    command, _ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)

    results = _run_pair(
        lambda: coordinate_link_active_customer(factory, command=command, now=NOW),
        lambda: coordinate_link_active_customer(factory, command=command, now=NOW),
    )

    assert {result.outcome for result in results} == {
        ShopCustomerLinkOutcome.CREATED,
        ShopCustomerLinkOutcome.ALREADY_LINKED,
    }
    with factory() as verification:
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 1
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.integration
def test_parallel_same_pair_across_two_staff_is_one_row_and_one_audit(
    m2_test_database: Engine,
) -> None:
    first_command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)
    with factory.begin() as setup:
        second_actor = _add_user(setup)
        target = setup.get(User, ids["target"])
        assert target is not None
        setup.add(
            ShopStaff(
                shop_id=ids["shop"],
                user_id=second_actor.id,
                role=ShopRole.MANAGER.value,
                is_active=True,
            )
        )
        second_command = LinkShopCustomerCommand(
            authority=DetachedShopCustomerAuthority(
                actor_user_id=UserId(second_actor.id),
                current_shop_id=ShopId(ids["shop"]),
            ),
            target_phone=TransientCanonicalShopCustomerPhone(target.phone),
        )

    results = _run_pair(
        lambda: coordinate_link_active_customer(
            factory,
            command=first_command,
            now=NOW,
        ),
        lambda: coordinate_link_active_customer(
            factory,
            command=second_command,
            now=NOW,
        ),
    )

    assert {result.outcome for result in results} == {
        ShopCustomerLinkOutcome.CREATED,
        ShopCustomerLinkOutcome.ALREADY_LINKED,
    }
    with factory() as verification:
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 1
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.integration
def test_parallel_same_customer_across_two_shops_creates_two_isolated_links(
    m2_test_database: Engine,
) -> None:
    first_command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)
    with factory.begin() as setup:
        second_actor = _add_user(setup)
        second_shop = Shop(
            name="Parallel second tenant",
            phone=f"+998{uuid4().int % 1_000_000_000:09d}",
            status=ShopStatus.ACTIVE.value,
        )
        setup.add(second_shop)
        setup.flush()
        setup.add(
            ShopStaff(
                shop_id=second_shop.id,
                user_id=second_actor.id,
                role=ShopRole.CASHIER.value,
                is_active=True,
            )
        )
        target = setup.get(User, ids["target"])
        assert target is not None
        second_command = LinkShopCustomerCommand(
            authority=DetachedShopCustomerAuthority(
                actor_user_id=UserId(second_actor.id),
                current_shop_id=ShopId(second_shop.id),
            ),
            target_phone=TransientCanonicalShopCustomerPhone(target.phone),
        )

    results = _run_pair(
        lambda: coordinate_link_active_customer(
            factory,
            command=first_command,
            now=NOW,
        ),
        lambda: coordinate_link_active_customer(
            factory,
            command=second_command,
            now=NOW,
        ),
    )

    assert all(result.outcome is ShopCustomerLinkOutcome.CREATED for result in results)
    with factory() as verification:
        rows = tuple(verification.scalars(select(ShopCustomer)))
        assert len(rows) == 2
        assert len({row.shop_id for row in rows}) == 2
        assert len({row.customer_id for row in rows}) == 1
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 2


@pytest.mark.integration
def test_link_vs_suspend_has_no_deadlock_and_valid_serial_outcome(
    m2_test_database: Engine,
) -> None:
    command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)

    def link():
        return coordinate_link_active_customer(factory, command=command, now=NOW)

    def suspend():
        with factory.begin() as session:
            return suspend_shop(
                session,
                shop_id=ShopId(ids["shop"]),
                actor_user_id=UserId(ids["actor"]),
                reason="M12 deterministic race",
                now=NOW + timedelta(minutes=1),
            )

    link_result, _suspend_result = _run_pair(link, suspend)

    assert link_result.outcome in {
        ShopCustomerLinkOutcome.CREATED,
        ShopCustomerLinkOutcome.CUSTOMER_LINK_UNAVAILABLE,
    }
    with factory() as verification:
        shop = verification.get(Shop, ids["shop"])
        assert shop is not None
        assert shop.status == ShopStatus.SUSPENDED.value
        row_count = verification.scalar(select(func.count()).select_from(ShopCustomer))
        audit_count = verification.scalar(select(func.count()).select_from(AuditLog))
        assert row_count in {0, 1}
        assert audit_count == row_count


@pytest.mark.integration
def test_link_vs_actor_revoke_has_no_deadlock_and_valid_serial_outcome(
    m2_test_database: Engine,
) -> None:
    command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)
    with factory.begin() as setup:
        owner = _add_user(setup)
        owner_staff = ShopStaff(
            shop_id=ids["shop"],
            user_id=owner.id,
            role=ShopRole.OWNER.value,
            is_active=True,
        )
        setup.add(owner_staff)
        setup.flush()
        owner_id = owner.id

    def link():
        return coordinate_link_active_customer(factory, command=command, now=NOW)

    def revoke():
        with factory.begin() as session:
            return revoke_staff(
                session,
                shop_id=ShopId(ids["shop"]),
                actor_user_id=UserId(owner_id),
                target_staff_id=ShopStaffId(ids["staff"]),
                now=NOW + timedelta(minutes=1),
            )

    link_result, _revoke_result = _run_pair(link, revoke)

    assert link_result.outcome in {
        ShopCustomerLinkOutcome.CREATED,
        ShopCustomerLinkOutcome.CUSTOMER_LINK_UNAVAILABLE,
    }
    with factory() as verification:
        staff = verification.get(ShopStaff, ids["staff"])
        assert staff is not None
        assert staff.is_active is False
        row_count = verification.scalar(select(func.count()).select_from(ShopCustomer))
        audit_count = verification.scalar(select(func.count()).select_from(AuditLog))
        assert row_count in {0, 1}
        assert audit_count == row_count


@pytest.mark.integration
def test_link_vs_m11_ordered_activation_has_no_deadlock_and_valid_outcome(
    m2_test_database: Engine,
) -> None:
    command, ids = _seed_eligible(m2_test_database, customer_active=False)
    factory = _factory(m2_test_database)

    def link():
        return coordinate_link_active_customer(factory, command=command, now=NOW)

    def activate():
        with factory.begin() as session:
            user = session.scalar(
                select(User).where(User.id == ids["target"]).with_for_update()
            )
            assert user is not None
            link = session.scalar(
                select(TelegramLink)
                .where(TelegramLink.user_id == user.id)
                .with_for_update()
            )
            assert link is not None
            customer = session.scalar(
                select(Customer).where(Customer.id == ids["customer"]).with_for_update()
            )
            assert customer is not None
            activated_at = NOW + timedelta(minutes=1)
            customer.onboarding_status = CUSTOMER_ONBOARDING_STATUS_ACTIVE
            customer.activated_at = activated_at
            customer.updated_at = activated_at

    link_result, _activation_result = _run_pair(link, activate)

    assert link_result.outcome in {
        ShopCustomerLinkOutcome.CREATED,
        ShopCustomerLinkOutcome.CUSTOMER_LINK_UNAVAILABLE,
    }
    with factory() as verification:
        customer = verification.get(Customer, ids["customer"])
        assert customer is not None
        assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_ACTIVE
        row_count = verification.scalar(select(func.count()).select_from(ShopCustomer))
        audit_count = verification.scalar(select(func.count()).select_from(AuditLog))
        assert row_count in {0, 1}
        assert audit_count == row_count


@pytest.mark.integration
def test_link_vs_protected_relink_has_no_deadlock_and_remains_verified(
    m2_test_database: Engine,
) -> None:
    command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)

    def link_customer():
        return coordinate_link_active_customer(factory, command=command, now=NOW)

    def protected_relink():
        with factory.begin() as session:
            user = session.scalar(
                select(User).where(User.id == ids["target"]).with_for_update()
            )
            assert user is not None
            link = session.scalar(
                select(TelegramLink)
                .where(TelegramLink.user_id == user.id)
                .with_for_update()
            )
            assert link is not None
            next_generation = NOW + timedelta(minutes=1)
            link.linked_at = next_generation
            link.phone_verified_at = next_generation
            link.updated_at = next_generation

    link_result, _relink_result = _run_pair(link_customer, protected_relink)

    assert link_result.outcome is ShopCustomerLinkOutcome.CREATED
    with factory() as verification:
        link = verification.get(TelegramLink, ids["link"])
        assert link is not None
        assert link.phone_verified_at == link.linked_at
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 1
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_static_trace_preserves_total_and_same_class_uuid_order() -> None:
    service_source = inspect.getsource(coordinate_link_active_customer)
    target_source = inspect.getsource(resolve_locked_eligible_target)
    auth_source = inspect.getsource(lock_actor_and_target_users_for_update)
    full_service_source = inspect.getsource(link_active_customer)

    assert full_service_source.index("lock_shop_for_update") < (
        full_service_source.index("lock_actor_shop_staff_for_update")
    )
    assert (
        target_source.index("lock_actor_and_target_users_for_update")
        < (target_source.index("get_telegram_link_by_user_for_update"))
        < target_source.index("lock_active_customer_for_target_user")
    )
    assert full_service_source.index("resolve_locked_eligible_target") < (
        full_service_source.index("lock_shop_customer_by_pair")
    )
    assert ".order_by(User.id.asc())" in auth_source
    combined = service_source + full_service_source + target_source + auth_source
    for forbidden in ("retry", "sleep(", "nowait", "lock_timeout", "advisory"):
        assert forbidden not in combined.casefold()
