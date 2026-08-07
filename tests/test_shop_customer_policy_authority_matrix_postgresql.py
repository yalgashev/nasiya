from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.contracts import AuditEvent
from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.error_codes import ErrorCode
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop
from app.shop_customer.contracts import (
    ShopCustomerPolicyUpdateOutcome,
    ShopDefaultPolicyUpdateOutcome,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.service import (
    ShopCustomerMutationDenied,
    update_shop_customer_policy,
    update_shop_default_credit_policy,
)
from tests.test_shop_customer_default_service_postgresql import (
    NOW as DEFAULT_NOW,
)
from tests.test_shop_customer_default_service_postgresql import (
    _command as default_command,
)
from tests.test_shop_customer_default_service_postgresql import (
    _factory,
)
from tests.test_shop_customer_default_service_postgresql import (
    _seed as seed_defaults,
)
from tests.test_shop_customer_policy_service_postgresql import (
    NOW as POLICY_NOW,
)
from tests.test_shop_customer_policy_service_postgresql import (
    _command as policy_command,
)
from tests.test_shop_customer_policy_service_postgresql import (
    _seed as seed_policy,
)


class InjectedAuditFailure(RuntimeError):
    pass


class FailingAuditWriter:
    def append(self, *, event: AuditEvent) -> None:
        _ = event
        raise InjectedAuditFailure("synthetic internal provider detail")


AUTHORITY_CASES = (
    ("owner_active", ShopRole.OWNER, True, True, False, ShopStatus.ACTIVE),
    ("manager_active", ShopRole.MANAGER, True, True, False, ShopStatus.ACTIVE),
    ("cashier_active", ShopRole.CASHIER, True, True, False, ShopStatus.ACTIVE),
    ("owner_revoked", ShopRole.OWNER, False, True, False, ShopStatus.ACTIVE),
    ("no_membership", ShopRole.OWNER, True, False, False, ShopStatus.ACTIVE),
    ("platform_admin", ShopRole.OWNER, True, False, True, ShopStatus.ACTIVE),
    ("owner_suspended", ShopRole.OWNER, True, True, False, ShopStatus.SUSPENDED),
    (
        "manager_suspended",
        ShopRole.MANAGER,
        True,
        True,
        False,
        ShopStatus.SUSPENDED,
    ),
    (
        "cashier_suspended",
        ShopRole.CASHIER,
        True,
        True,
        False,
        ShopStatus.SUSPENDED,
    ),
)


def _expected_error(operation: str, label: str) -> ErrorCode | None:
    if label.endswith("_suspended"):
        return ErrorCode.SHOP_SUSPENDED
    if label == "owner_active":
        return None
    if operation == "policy" and label == "manager_active":
        return None
    return ErrorCode.FORBIDDEN


@pytest.mark.integration
@pytest.mark.parametrize("operation", ("defaults", "policy"))
@pytest.mark.parametrize(
    (
        "label",
        "role",
        "membership_active",
        "include_membership",
        "platform_admin",
        "shop_status",
    ),
    AUTHORITY_CASES,
)
def test_role_membership_platform_admin_and_suspension_matrix_is_exact(
    m2_test_database: Engine,
    operation: str,
    label: str,
    role: ShopRole,
    membership_active: bool,
    include_membership: bool,
    platform_admin: bool,
    shop_status: ShopStatus,
) -> None:
    expected_error = _expected_error(operation, label)
    factory = _factory(m2_test_database)
    if operation == "defaults":
        original_row_time = DEFAULT_NOW
        seeded = seed_defaults(
            m2_test_database,
            role=role,
            shop_status=shop_status,
            membership_active=membership_active,
            include_membership=include_membership,
            actor_platform_admin=platform_admin,
        )
        updated_at = seeded["updated_at"]
        assert isinstance(updated_at, datetime)

        def mutate(session):
            return update_shop_default_credit_policy(
                session,
                authority=seeded["authority"],
                command=default_command(
                    expected_updated_at=updated_at,
                    credit="8000000",
                    debts=8,
                ),
                now=DEFAULT_NOW + timedelta(minutes=1),
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    else:
        original_row_time = POLICY_NOW
        seeded = seed_policy(
            m2_test_database,
            role=role,
            shop_status=shop_status,
            membership_active=membership_active,
            include_membership=include_membership,
            actor_platform_admin=platform_admin,
        )

        def mutate(session):
            return update_shop_customer_policy(
                session,
                authority=seeded["authority"],
                command=policy_command(seeded["row"]),
                now=POLICY_NOW + timedelta(minutes=1),
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    if expected_error is None:
        with factory.begin() as session:
            result = mutate(session)
        expected_outcome = (
            ShopDefaultPolicyUpdateOutcome.CHANGED
            if operation == "defaults"
            else ShopCustomerPolicyUpdateOutcome.CHANGED
        )
        assert result.outcome is expected_outcome
    else:
        with pytest.raises(ShopCustomerMutationDenied) as caught:
            with factory.begin() as session:
                mutate(session)
        assert caught.value.error_code is expected_error
        rendered = repr(caught.value) + str(caught.value)
        assert str(seeded["shop"]) not in rendered
        assert str(seeded["row"]) not in rendered
        assert "provider" not in rendered.casefold()
        assert "sql" not in rendered.casefold()

    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        shop = verification.get(Shop, seeded["shop"])
        audit_count = verification.scalar(select(func.count()).select_from(AuditLog))
        assert row is not None and shop is not None
        if expected_error is not None:
            assert (
                row.credit_limit_uzs,
                row.max_open_debts,
                row.list_status,
                row.revision,
                row.updated_at,
            ) == (Decimal("1000000"), 2, "normal", 1, original_row_time)
            assert audit_count == 0
        elif operation == "defaults":
            assert (
                shop.default_credit_limit_uzs,
                shop.default_max_open_debts,
            ) == (Decimal("8000000"), 8)
            assert (row.revision, row.updated_at) == (1, original_row_time)
            assert audit_count == 1
        else:
            assert (
                row.credit_limit_uzs,
                row.max_open_debts,
                row.list_status,
                row.revision,
            ) == (Decimal("2000000"), 3, "normal", 2)
            assert audit_count == 1


@pytest.mark.integration
@pytest.mark.parametrize("operation", ("defaults", "policy"))
def test_each_policy_audit_failure_is_all_or_nothing(
    m2_test_database: Engine,
    operation: str,
) -> None:
    factory = _factory(m2_test_database)
    if operation == "defaults":
        original_row_time = DEFAULT_NOW
        seeded = seed_defaults(m2_test_database)
        updated_at = seeded["updated_at"]
        assert isinstance(updated_at, datetime)

        def mutate(session):
            return update_shop_default_credit_policy(
                session,
                authority=seeded["authority"],
                command=default_command(expected_updated_at=updated_at),
                now=DEFAULT_NOW + timedelta(minutes=1),
                audit_writer=FailingAuditWriter(),
            )

    else:
        original_row_time = POLICY_NOW
        seeded = seed_policy(m2_test_database)

        def mutate(session):
            return update_shop_customer_policy(
                session,
                authority=seeded["authority"],
                command=policy_command(seeded["row"]),
                now=POLICY_NOW + timedelta(minutes=1),
                audit_writer=FailingAuditWriter(),
            )

    with pytest.raises(InjectedAuditFailure):
        with factory.begin() as session:
            mutate(session)

    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        shop = verification.get(Shop, seeded["shop"])
        assert row is not None and shop is not None
        assert (
            shop.default_credit_limit_uzs,
            shop.default_max_open_debts,
        ) == (Decimal("1000000"), 2)
        assert (
            row.credit_limit_uzs,
            row.max_open_debts,
            row.list_status,
            row.revision,
            row.updated_at,
        ) == (Decimal("1000000"), 2, "normal", 1, original_row_time)
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0
