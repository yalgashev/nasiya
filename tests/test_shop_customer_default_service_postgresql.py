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
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    DetachedShopCustomerAuthority,
    ExpectedShopUpdatedAt,
    ShopDefaultCreditPolicy,
    ShopDefaultCreditPolicyUpdate,
    ShopDefaultPolicyUpdateOutcome,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.service import (
    ShopCustomerMutationDenied,
    update_shop_default_credit_policy,
)
from app.shop_customer.values import (
    CreditLimitUzbekistanSom,
    MaxOpenDebts,
)
from tests.test_shop_customer_repository_postgresql import (
    _add_active_customer,
    _add_shop,
    _add_user,
)

NOW = datetime(2026, 8, 7, 20, 0, tzinfo=UTC)


class InjectedAuditFailure(RuntimeError):
    pass


class FailingAuditWriter:
    def append(self, *, event: AuditEvent) -> None:
        _ = event
        raise InjectedAuditFailure("synthetic audit failure")


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session)


def _defaults(*, credit: str, debts: int) -> ShopDefaultCreditPolicy:
    return ShopDefaultCreditPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal(credit)),
        max_open_debts=MaxOpenDebts(debts),
    )


def _seed(
    engine: Engine,
    *,
    role: ShopRole = ShopRole.OWNER,
    shop_status: ShopStatus = ShopStatus.ACTIVE,
    actor_platform_admin: bool = False,
    include_membership: bool = True,
    membership_active: bool = True,
) -> dict[str, object]:
    factory = _factory(engine)
    with factory.begin() as session:
        actor = _add_user(session)
        actor.is_platform_admin = actor_platform_admin
        shop = _add_shop(session, name="Defaults service tenant")
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
        target = _add_user(session)
        customer = _add_active_customer(session, user=target)
        row = ShopCustomer(
            shop_id=shop.id,
            customer_id=customer.id,
            credit_limit_uzs=Decimal("1000000"),
            max_open_debts=2,
            list_status="normal",
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
            "updated_at": shop.updated_at,
        }


def _command(
    *,
    expected_updated_at: datetime,
    credit: str = "4000000",
    debts: int = 4,
) -> ShopDefaultCreditPolicyUpdate:
    return ShopDefaultCreditPolicyUpdate(
        expected_updated_at=ExpectedShopUpdatedAt(expected_updated_at),
        new_defaults=_defaults(credit=credit, debts=debts),
    )


@pytest.mark.integration
def test_owner_changes_only_future_link_defaults_and_writes_one_audit(
    m2_test_database: Engine,
) -> None:
    seeded = _seed(m2_test_database)
    factory = _factory(m2_test_database)
    updated_at = seeded["updated_at"]
    assert isinstance(updated_at, datetime)

    with factory.begin() as session:
        result = update_shop_default_credit_policy(
            session,
            authority=seeded["authority"],
            command=_command(expected_updated_at=updated_at),
            now=NOW + timedelta(minutes=1),
            audit_writer=SqlAlchemyAuditWriter(session),
        )

    assert result is not None
    assert result.outcome is ShopDefaultPolicyUpdateOutcome.CHANGED
    with factory() as verification:
        shop = verification.get(Shop, seeded["shop"])
        row = verification.get(ShopCustomer, seeded["row"])
        audit = verification.scalar(select(AuditLog))
        assert shop is not None and row is not None and audit is not None
        assert (
            shop.default_credit_limit_uzs,
            shop.default_max_open_debts,
            shop.updated_at,
        ) == (Decimal("4000000"), 4, NOW + timedelta(minutes=1))
        assert (
            row.credit_limit_uzs,
            row.max_open_debts,
            row.list_status,
            row.revision,
            row.updated_at,
        ) == (Decimal("1000000"), 2, "normal", 1, NOW)
        assert audit.event_type == AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED.value
        assert audit.object_id == shop.id
        assert audit.payload == {
            "old_default_credit_limit_uzs": 1_000_000,
            "new_default_credit_limit_uzs": 4_000_000,
            "old_default_max_open_debts": 2,
            "new_default_max_open_debts": 4,
        }


@pytest.mark.integration
def test_owner_noop_and_stale_submissions_leave_defaults_timestamp_and_audit_unchanged(
    m2_test_database: Engine,
) -> None:
    seeded = _seed(m2_test_database)
    factory = _factory(m2_test_database)
    original_updated_at = seeded["updated_at"]
    assert isinstance(original_updated_at, datetime)
    authority = seeded["authority"]

    with factory.begin() as session:
        no_change = update_shop_default_credit_policy(
            session,
            authority=authority,
            command=_command(
                expected_updated_at=original_updated_at,
                credit="1000000",
                debts=2,
            ),
            now=NOW + timedelta(minutes=1),
            audit_writer=SqlAlchemyAuditWriter(session),
        )
    assert no_change is not None
    assert no_change.outcome is ShopDefaultPolicyUpdateOutcome.NO_CHANGE

    with factory.begin() as session:
        changed = update_shop_default_credit_policy(
            session,
            authority=authority,
            command=_command(expected_updated_at=original_updated_at),
            now=NOW + timedelta(minutes=2),
            audit_writer=SqlAlchemyAuditWriter(session),
        )
    assert changed is not None
    assert changed.outcome is ShopDefaultPolicyUpdateOutcome.CHANGED

    with factory.begin() as session:
        stale = update_shop_default_credit_policy(
            session,
            authority=authority,
            command=_command(expected_updated_at=original_updated_at),
            now=NOW + timedelta(minutes=3),
            audit_writer=SqlAlchemyAuditWriter(session),
        )
    assert stale is not None
    assert stale.outcome is ShopDefaultPolicyUpdateOutcome.STALE
    assert stale.defaults is None
    with factory() as verification:
        shop = verification.get(Shop, seeded["shop"])
        assert shop is not None
        assert shop.updated_at == NOW + timedelta(minutes=2)
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "shop_status", "include_membership", "platform_admin", "error_code"),
    (
        (ShopRole.MANAGER, ShopStatus.ACTIVE, True, False, ErrorCode.FORBIDDEN),
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
def test_non_owner_or_non_active_live_authority_cannot_change_defaults(
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
    original_updated_at = seeded["updated_at"]
    assert isinstance(original_updated_at, datetime)

    with pytest.raises(ShopCustomerMutationDenied) as caught:
        with factory.begin() as session:
            update_shop_default_credit_policy(
                session,
                authority=seeded["authority"],
                command=_command(expected_updated_at=original_updated_at),
                now=NOW + timedelta(minutes=1),
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    assert caught.value.error_code is error_code
    assert str(seeded["shop"]) not in repr(caught.value)
    with factory() as verification:
        shop = verification.get(Shop, seeded["shop"])
        assert shop is not None
        assert (
            shop.default_credit_limit_uzs,
            shop.default_max_open_debts,
            shop.updated_at,
        ) == (Decimal("1000000"), 2, original_updated_at)
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_audit_failure_rolls_back_default_change_in_caller_owned_transaction(
    m2_test_database: Engine,
) -> None:
    seeded = _seed(m2_test_database)
    factory = _factory(m2_test_database)
    original_updated_at = seeded["updated_at"]
    assert isinstance(original_updated_at, datetime)

    with pytest.raises(InjectedAuditFailure):
        with factory.begin() as session:
            update_shop_default_credit_policy(
                session,
                authority=seeded["authority"],
                command=_command(expected_updated_at=original_updated_at),
                now=NOW + timedelta(minutes=1),
                audit_writer=FailingAuditWriter(),
            )

    with factory() as verification:
        shop = verification.get(Shop, seeded["shop"])
        assert shop is not None
        assert (
            shop.default_credit_limit_uzs,
            shop.default_max_open_debts,
            shop.updated_at,
        ) == (Decimal("1000000"), 2, original_updated_at)
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_default_service_has_no_shop_customer_write_or_transaction_ownership() -> None:
    source = Path("app/shop_customer/service.py").read_text(encoding="utf-8")
    start = source.index("def update_shop_default_credit_policy")
    end = source.index("def update_shop_customer_policy")
    default_service = source[start:end]
    assert ".commit(" not in default_service
    assert ".rollback(" not in default_service
    assert ".close(" not in default_service
    assert "lock_shop_customer" not in default_service
    assert "update_locked_shop_customer" not in default_service
    assert "lock_shop_for_update" in default_service
    assert "lock_actor_shop_staff_for_update" in default_service
