from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.shop.enums import ShopRole
from app.shop.models import Shop
from app.shop_customer.contracts import (
    ExpectedShopUpdatedAt,
    ShopCustomerLinkOutcome,
    ShopCustomerPolicyUpdateOutcome,
    ShopDefaultCreditPolicyUpdate,
    ShopDefaultPolicyUpdateOutcome,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.service import (
    coordinate_link_active_customer,
    update_shop_customer_policy,
    update_shop_default_credit_policy,
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
from tests.test_shop_customer_link_service_postgresql import _seed_eligible
from tests.test_shop_customer_policy_service_postgresql import (
    _command as policy_command,
)
from tests.test_shop_customer_policy_service_postgresql import (
    _policy,
)
from tests.test_shop_customer_policy_service_postgresql import (
    _seed as seed_policy,
)

PRECISE = datetime(2026, 8, 7, 22, 3, 4, 567890, tzinfo=UTC)


def _run_pair(left, right):
    barrier = Barrier(2)

    def run(operation):
        barrier.wait()
        return operation()

    with ThreadPoolExecutor(max_workers=2) as executor:
        return tuple(executor.map(run, (left, right)))


@pytest.mark.integration
def test_parallel_policy_writers_have_exactly_one_revision_winner(
    m2_test_database: Engine,
) -> None:
    seeded = seed_policy(m2_test_database, role=ShopRole.MANAGER)
    factory = _factory(m2_test_database)
    commands = (
        policy_command(
            seeded["row"],
            policy=_policy(credit="3000000", debts=3),
        ),
        policy_command(
            seeded["row"],
            policy=_policy(credit="4000000", debts=4),
        ),
    )

    def mutate(index: int):
        with factory.begin() as session:
            return update_shop_customer_policy(
                session,
                authority=seeded["authority"],
                command=commands[index],
                now=PRECISE + timedelta(minutes=index),
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    results = _run_pair(lambda: mutate(0), lambda: mutate(1))

    assert {result.outcome for result in results} == {
        ShopCustomerPolicyUpdateOutcome.CHANGED,
        ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_CHANGED,
    }
    winner_index = next(
        index
        for index, result in enumerate(results)
        if result.outcome is ShopCustomerPolicyUpdateOutcome.CHANGED
    )
    with factory() as verification:
        row = verification.get(ShopCustomer, seeded["row"])
        audits = tuple(verification.scalars(select(AuditLog)))
        assert row is not None
        assert (
            row.credit_limit_uzs,
            row.max_open_debts,
            row.list_status,
            row.revision,
        ) == (
            commands[winner_index].new_policy.credit_limit.value,
            commands[winner_index].new_policy.max_open_debts.value,
            commands[winner_index].new_policy.list_status.value,
            2,
        )
        assert len(audits) == 1
        assert audits[0].event_type == AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED
        assert audits[0].payload["revision"] == 2


@pytest.mark.integration
def test_parallel_default_writers_have_one_updated_at_winner_and_one_stale_result(
    m2_test_database: Engine,
) -> None:
    seeded = seed_defaults(m2_test_database)
    factory = _factory(m2_test_database)
    expected = seeded["updated_at"]
    assert isinstance(expected, datetime)
    commands = (
        default_command(
            expected_updated_at=expected,
            credit="5000000",
            debts=5,
        ),
        default_command(
            expected_updated_at=expected,
            credit="6000000",
            debts=6,
        ),
    )

    def mutate(index: int):
        with factory.begin() as session:
            return update_shop_default_credit_policy(
                session,
                authority=seeded["authority"],
                command=commands[index],
                now=PRECISE + timedelta(minutes=index),
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    results = _run_pair(lambda: mutate(0), lambda: mutate(1))

    assert {result.outcome for result in results} == {
        ShopDefaultPolicyUpdateOutcome.CHANGED,
        ShopDefaultPolicyUpdateOutcome.STALE,
    }
    winner_index = next(
        index
        for index, result in enumerate(results)
        if result.outcome is ShopDefaultPolicyUpdateOutcome.CHANGED
    )
    with factory() as verification:
        shop = verification.get(Shop, seeded["shop"])
        assert shop is not None
        assert (
            shop.default_credit_limit_uzs,
            shop.default_max_open_debts,
            shop.updated_at,
        ) == (
            commands[winner_index].new_defaults.credit_limit.value,
            commands[winner_index].new_defaults.max_open_debts.value,
            PRECISE + timedelta(minutes=winner_index),
        )
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.integration
def test_default_noop_preserves_precise_timezone_aware_stale_token(
    m2_test_database: Engine,
) -> None:
    seeded = seed_defaults(m2_test_database)
    factory = _factory(m2_test_database)
    tashkent = timezone(timedelta(hours=5))
    equivalent_tashkent_token = PRECISE.astimezone(tashkent)
    with factory.begin() as setup:
        shop = setup.get(Shop, seeded["shop"])
        assert shop is not None
        shop.updated_at = PRECISE

    command = ShopDefaultCreditPolicyUpdate(
        expected_updated_at=ExpectedShopUpdatedAt(equivalent_tashkent_token),
        new_defaults=default_command(
            expected_updated_at=PRECISE,
            credit="1000000",
            debts=2,
        ).new_defaults,
    )
    with factory.begin() as session:
        result = update_shop_default_credit_policy(
            session,
            authority=seeded["authority"],
            command=command,
            now=PRECISE + timedelta(minutes=1),
            audit_writer=SqlAlchemyAuditWriter(session),
        )

    assert result.outcome is ShopDefaultPolicyUpdateOutcome.NO_CHANGE
    with factory() as verification:
        shop = verification.get(Shop, seeded["shop"])
        assert shop is not None
        assert shop.updated_at == PRECISE
        assert shop.updated_at.tzinfo is not None
        assert shop.updated_at.microsecond == 567890
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_parallel_default_update_and_link_copy_only_complete_old_or_new_pair(
    m2_test_database: Engine,
) -> None:
    link_command, ids = _seed_eligible(
        m2_test_database,
        role=ShopRole.OWNER,
    )
    factory = _factory(m2_test_database)
    with factory() as read:
        shop = read.get(Shop, ids["shop"])
        assert shop is not None
        expected = shop.updated_at
    defaults_command = default_command(
        expected_updated_at=expected,
        credit="7000000",
        debts=7,
    )

    def change_defaults():
        with factory.begin() as session:
            return update_shop_default_credit_policy(
                session,
                authority=link_command.authority,
                command=defaults_command,
                now=PRECISE,
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    default_result, link_result = _run_pair(
        change_defaults,
        lambda: coordinate_link_active_customer(
            factory,
            command=link_command,
            now=PRECISE + timedelta(minutes=1),
        ),
    )

    assert default_result.outcome is ShopDefaultPolicyUpdateOutcome.CHANGED
    assert link_result.outcome is ShopCustomerLinkOutcome.CREATED
    with factory() as verification:
        row = verification.scalar(select(ShopCustomer))
        audits = tuple(verification.scalars(select(AuditLog)))
        assert row is not None
        assert (row.credit_limit_uzs, row.max_open_debts) in {
            (1_000_000, 2),
            (7_000_000, 7),
        }
        assert {audit.event_type for audit in audits} == {
            AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED,
            AuditEventType.SHOP_CUSTOMER_LINKED,
        }


def test_policy_concurrency_proofs_use_only_barriers() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    assert "Barrier(" in source
    for forbidden in (
        "sl" + "eep(",
        "re" + "try",
        "no" + "wait",
        "lock" + "_timeout",
        "ad" + "visory",
    ):
        assert forbidden not in source.casefold()
