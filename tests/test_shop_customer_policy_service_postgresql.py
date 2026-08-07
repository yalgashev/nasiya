from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import AuditEvent, AuditEventType
from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.error_codes import ErrorCode
from app.customer.models import Customer
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import ShopStaff
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    DetachedShopCustomerAuthority,
    ShopCustomerPathLocator,
    ShopCustomerPolicy,
    ShopCustomerPolicyUpdateOutcome,
    ShopCustomerRevision,
    UpdateShopCustomerPolicyCommand,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.models import ShopCustomer
from app.shop_customer.policy import (
    ShopCustomerAuthorizationContext,
    ShopCustomerCapability,
)
from app.shop_customer.service import (
    ShopCustomerMutationDenied,
    update_shop_customer_policy,
)
from app.shop_customer.values import (
    CreditLimitUzbekistanSom,
    MaxOpenDebts,
    ShopCustomerId,
)
from app.telegram.models import TelegramLink
from tests.test_shop_customer_repository_postgresql import (
    _add_active_customer,
    _add_shop,
    _add_user,
)

NOW = datetime(2026, 8, 7, 21, 0, tzinfo=UTC)


class InjectedAuditFailure(RuntimeError):
    pass


class FailingAuditWriter:
    def append(self, *, event: AuditEvent) -> None:
        _ = event
        raise InjectedAuditFailure("synthetic audit failure")


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session)


def _policy(
    *,
    credit: str = "2000000",
    debts: int = 3,
    status: ShopCustomerListStatus = ShopCustomerListStatus.NORMAL,
) -> ShopCustomerPolicy:
    return ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal(credit)),
        max_open_debts=MaxOpenDebts(debts),
        list_status=status,
    )


def _seed(
    engine: Engine,
    *,
    role: ShopRole = ShopRole.OWNER,
    shop_status: ShopStatus = ShopStatus.ACTIVE,
    include_membership: bool = True,
    membership_active: bool = True,
    actor_platform_admin: bool = False,
    initial_list_status: ShopCustomerListStatus = ShopCustomerListStatus.NORMAL,
) -> dict[str, object]:
    factory = _factory(engine)
    with factory.begin() as session:
        actor = _add_user(session)
        actor.is_platform_admin = actor_platform_admin
        target = _add_user(session)
        customer = _add_active_customer(session, user=target)
        link = TelegramLink(
            user_id=target.id,
            telegram_chat_id=target.id.int % 8_000_000_000 + 1,
            linked_at=NOW,
            unlinked_at=None,
            phone_verified_at=NOW,
            updated_at=NOW,
        )
        session.add(link)
        shop = _add_shop(session, name="Policy service tenant")
        shop.status = shop_status.value
        if include_membership:
            session.add(
                ShopStaff(
                    shop_id=shop.id,
                    user_id=actor.id,
                    role=role.value,
                    is_active=membership_active,
                    revoked_at=None if membership_active else NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        row = ShopCustomer(
            shop_id=shop.id,
            customer_id=customer.id,
            credit_limit_uzs=Decimal("1000000"),
            max_open_debts=2,
            list_status=initial_list_status.value,
            revision=1,
            created_by_user_id=actor.id,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(row)
        session.flush()
        return {
            "authority": DetachedShopCustomerAuthority(
                actor_user_id=UserId(actor.id),
                current_shop_id=ShopId(shop.id),
            ),
            "shop": shop.id,
            "row": row.id,
            "customer": customer.id,
            "link": link.id,
        }


def _command(
    row_id,
    *,
    revision: int = 1,
    policy: ShopCustomerPolicy | None = None,
) -> UpdateShopCustomerPolicyCommand:
    return UpdateShopCustomerPolicyCommand(
        locator=ShopCustomerPathLocator(ShopCustomerId(row_id)),
        expected_revision=ShopCustomerRevision(revision),
        new_policy=policy or _policy(),
    )


@pytest.mark.integration
@pytest.mark.parametrize("role", (ShopRole.OWNER, ShopRole.MANAGER))
def test_owner_and_manager_update_the_complete_tenant_scoped_policy(
    m2_test_database: Engine,
    role: ShopRole,
) -> None:
    seeded = _seed(m2_test_database, role=role)
    factory = _factory(m2_test_database)

    with factory.begin() as session:
        result = update_shop_customer_policy(
            session,
            authority=seeded["authority"],
            command=_command(seeded["row"]),
            now=NOW + timedelta(minutes=1),
            audit_writer=SqlAlchemyAuditWriter(session),
        )

    assert result.outcome is ShopCustomerPolicyUpdateOutcome.CHANGED
    assert result.revision == ShopCustomerRevision(2)
    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        audit = verification.scalar(select(AuditLog))
        assert row is not None and audit is not None
        assert (
            row.credit_limit_uzs,
            row.max_open_debts,
            row.list_status,
            row.revision,
            row.updated_at,
        ) == (Decimal("2000000"), 3, "normal", 2, NOW + timedelta(minutes=1))
        assert audit.event_type == AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED.value
        assert audit.object_id == row.id
        assert audit.payload == {
            "old_credit_limit_uzs": 1_000_000,
            "new_credit_limit_uzs": 2_000_000,
            "old_max_open_debts": 2,
            "new_max_open_debts": 3,
            "old_list_status": "normal",
            "new_list_status": "normal",
            "revision": 2,
        }


@pytest.mark.integration
def test_noop_and_stale_policy_submissions_never_change_revision_timestamp_or_audit(
    m2_test_database: Engine,
) -> None:
    seeded = _seed(m2_test_database)
    factory = _factory(m2_test_database)
    original = _policy(credit="1000000", debts=2)

    with factory.begin() as session:
        no_change = update_shop_customer_policy(
            session,
            authority=seeded["authority"],
            command=_command(seeded["row"], policy=original),
            now=NOW + timedelta(minutes=1),
            audit_writer=SqlAlchemyAuditWriter(session),
        )
    assert no_change.outcome is ShopCustomerPolicyUpdateOutcome.NO_CHANGE
    assert no_change.revision == ShopCustomerRevision(1)

    with factory.begin() as session:
        changed = update_shop_customer_policy(
            session,
            authority=seeded["authority"],
            command=_command(seeded["row"]),
            now=NOW + timedelta(minutes=2),
            audit_writer=SqlAlchemyAuditWriter(session),
        )
    assert changed.outcome is ShopCustomerPolicyUpdateOutcome.CHANGED

    with factory.begin() as session:
        stale = update_shop_customer_policy(
            session,
            authority=seeded["authority"],
            command=_command(seeded["row"], revision=1),
            now=NOW + timedelta(minutes=3),
            audit_writer=SqlAlchemyAuditWriter(session),
        )
    assert stale.outcome is ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_CHANGED
    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        assert row is not None
        assert (row.revision, row.updated_at) == (2, NOW + timedelta(minutes=2))
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "shop_status", "include_membership", "platform_admin", "error_code"),
    (
        (ShopRole.CASHIER, ShopStatus.ACTIVE, True, False, ErrorCode.FORBIDDEN),
        (
            ShopRole.OWNER,
            ShopStatus.SUSPENDED,
            True,
            False,
            ErrorCode.SHOP_SUSPENDED,
        ),
        (ShopRole.OWNER, ShopStatus.ACTIVE, False, True, ErrorCode.FORBIDDEN),
    ),
)
def test_unprivileged_live_authority_returns_one_safe_unavailable_result(
    m2_test_database: Engine,
    role: ShopRole,
    shop_status: ShopStatus,
    include_membership: bool,
    platform_admin: bool,
    error_code: ErrorCode,
) -> None:
    seeded = _seed(
        m2_test_database,
        role=role,
        shop_status=shop_status,
        include_membership=include_membership,
        actor_platform_admin=platform_admin,
    )
    factory = _factory(m2_test_database)

    with pytest.raises(ShopCustomerMutationDenied) as caught:
        with factory.begin() as session:
            update_shop_customer_policy(
                session,
                authority=seeded["authority"],
                command=_command(seeded["row"]),
                now=NOW + timedelta(minutes=1),
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    assert caught.value.error_code is error_code
    assert str(seeded["shop"]) not in repr(caught.value)
    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        assert row is not None
        assert (row.revision, row.updated_at) == (1, NOW)
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_cross_tenant_path_locator_is_not_authority_and_creates_no_audit(
    m2_test_database: Engine,
) -> None:
    current = _seed(m2_test_database)
    foreign = _seed(m2_test_database)
    factory = _factory(m2_test_database)

    with factory.begin() as session:
        result = update_shop_customer_policy(
            session,
            authority=current["authority"],
            command=_command(foreign["row"]),
            now=NOW + timedelta(minutes=1),
            audit_writer=SqlAlchemyAuditWriter(session),
        )

    assert result.outcome is ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_UNAVAILABLE
    with factory() as verification:
        foreign_row = verification.get(ShopCustomer, foreign["row"])
        assert foreign_row is not None
        assert (foreign_row.revision, foreign_row.updated_at) == (1, NOW)
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_audit_failure_rolls_back_complete_policy_update_in_outer_transaction(
    m2_test_database: Engine,
) -> None:
    seeded = _seed(m2_test_database)
    factory = _factory(m2_test_database)

    with pytest.raises(InjectedAuditFailure):
        with factory.begin() as session:
            update_shop_customer_policy(
                session,
                authority=seeded["authority"],
                command=_command(seeded["row"]),
                now=NOW + timedelta(minutes=1),
                audit_writer=FailingAuditWriter(),
            )

    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        assert row is not None
        assert (
            row.credit_limit_uzs,
            row.max_open_debts,
            row.list_status,
            row.revision,
            row.updated_at,
        ) == (Decimal("1000000"), 2, "normal", 1, NOW)
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_policy_service_is_tenant_scoped_and_caller_owned() -> None:
    source = Path("app/shop_customer/service.py").read_text(encoding="utf-8")
    start = source.index("def update_shop_customer_policy")
    end = source.index("def coordinate_link_active_customer")
    policy_service = source[start:end]
    assert ".commit(" not in policy_service
    assert ".rollback(" not in policy_service
    assert ".close(" not in policy_service
    assert "lock_shop_for_update" in policy_service
    assert "lock_actor_shop_staff_for_update" in policy_service
    assert "lock_shop_customer_by_tenant_locator" in policy_service


@pytest.mark.integration
@pytest.mark.parametrize("old_status", tuple(ShopCustomerListStatus))
@pytest.mark.parametrize("new_status", tuple(ShopCustomerListStatus))
def test_every_list_status_transition_is_complete_and_debtless(
    m2_test_database: Engine,
    old_status: ShopCustomerListStatus,
    new_status: ShopCustomerListStatus,
) -> None:
    seeded = _seed(m2_test_database, initial_list_status=old_status)
    factory = _factory(m2_test_database)
    replacement = _policy(
        credit="1000000",
        debts=2,
        status=new_status,
    )

    with factory() as before:
        customer = before.get(Customer, seeded["customer"])
        link = before.get(TelegramLink, seeded["link"])
        assert customer is not None and link is not None
        lifecycle_before = (
            customer.onboarding_status,
            customer.activated_at,
            customer.updated_at,
            link.linked_at,
            link.unlinked_at,
            link.phone_verified_at,
            link.updated_at,
        )

    with factory.begin() as session:
        result = update_shop_customer_policy(
            session,
            authority=seeded["authority"],
            command=_command(seeded["row"], policy=replacement),
            now=NOW + timedelta(minutes=1),
            audit_writer=SqlAlchemyAuditWriter(session),
        )

    expected_changed = old_status is not new_status
    assert result.outcome is (
        ShopCustomerPolicyUpdateOutcome.CHANGED
        if expected_changed
        else ShopCustomerPolicyUpdateOutcome.NO_CHANGE
    )
    assert result.revision == ShopCustomerRevision(2 if expected_changed else 1)
    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        customer = verification.get(Customer, seeded["customer"])
        link = verification.get(TelegramLink, seeded["link"])
        assert row is not None and customer is not None and link is not None
        assert (
            row.credit_limit_uzs,
            row.max_open_debts,
            row.list_status,
            row.revision,
            row.updated_at,
        ) == (
            Decimal("1000000"),
            2,
            new_status.value,
            2 if expected_changed else 1,
            NOW + timedelta(minutes=1) if expected_changed else NOW,
        )
        assert (
            customer.onboarding_status,
            customer.activated_at,
            customer.updated_at,
            link.linked_at,
            link.unlinked_at,
            link.phone_verified_at,
            link.updated_at,
        ) == lifecycle_before
        audits = tuple(verification.scalars(select(AuditLog)))
        assert len(audits) == int(expected_changed)
        if expected_changed:
            assert audits[0].event_type == AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED
            assert audits[0].payload == {
                "old_credit_limit_uzs": 1_000_000,
                "new_credit_limit_uzs": 1_000_000,
                "old_max_open_debts": 2,
                "new_max_open_debts": 2,
                "old_list_status": old_status.value,
                "new_list_status": new_status.value,
                "revision": 2,
            }


@pytest.mark.parametrize("list_status", tuple(ShopCustomerListStatus))
def test_list_status_never_changes_shop_capabilities(
    list_status: ShopCustomerListStatus,
) -> None:
    _ = list_status
    context = ShopCustomerAuthorizationContext(
        role=ShopRole.MANAGER,
        shop_status=ShopStatus.ACTIVE,
        membership_active=True,
        is_platform_admin=False,
    )
    assert context.allows(ShopCustomerCapability.READ_ROSTER) is True
    assert context.allows(ShopCustomerCapability.LINK_CUSTOMER) is True
    assert context.allows(ShopCustomerCapability.UPDATE_DEFAULTS) is False
    assert context.allows(ShopCustomerCapability.UPDATE_POLICY) is True
