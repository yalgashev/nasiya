from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    ShopCustomerDefaultsUpdatedAuditPayload,
    ShopCustomerLinkedAuditPayload,
    ShopCustomerPolicyUpdatedAuditPayload,
)
from app.audit.models import AuditLog
from app.audit.repository import append_audit_event
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop.models import Shop
from app.shop.values import UserId
from app.shop_customer.contracts import (
    ShopCustomerPolicy,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import insert_shop_customer
from app.shop_customer.values import (
    CreditLimitUzbekistanSom,
    MaxOpenDebts,
    ShopCustomerId,
)
from tests.test_shop_customer_repository_postgresql import (
    _add_active_customer,
    _add_shop,
    _add_user,
    _locked_predecessors,
)

NOW = datetime(2026, 8, 7, 14, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    factory = create_database_session_factory(m2_test_database)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _policy(
    credit: str,
    debts: int,
    status: ShopCustomerListStatus = ShopCustomerListStatus.NORMAL,
) -> ShopCustomerPolicy:
    return ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal(credit)),
        max_open_debts=MaxOpenDebts(debts),
        list_status=status,
    )


def _event(
    *,
    event_type: AuditEventType,
    object_type: AuditObjectType,
    object_id,
    actor: User,
    metadata,
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        actor_kind=AuditActorKind.USER,
        actor_user_id=actor.id,
        object_type=object_type,
        object_id=object_id,
        occurred_at=NOW,
        candidate_metadata=metadata,
    )


@pytest.mark.integration
def test_three_m12_audit_events_persist_with_exact_db_shapes(
    db_session: Session,
) -> None:
    actor = _add_user(db_session)
    shop = _add_shop(db_session, name="Audit tenant")
    relationship_id = uuid4()
    old_policy = _policy("1000000", 2)
    new_policy = _policy("2000000", 3, ShopCustomerListStatus.WHITELISTED)
    old_defaults = ShopDefaultCreditPolicy()
    new_defaults = ShopDefaultCreditPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal("2000000")),
        max_open_debts=MaxOpenDebts(3),
    )
    events = (
        _event(
            event_type=AuditEventType.SHOP_CUSTOMER_LINKED,
            object_type=AuditObjectType.SHOP_CUSTOMER,
            object_id=relationship_id,
            actor=actor,
            metadata=ShopCustomerLinkedAuditPayload(
                policy=old_policy,
                revision=ShopCustomerRevision(1),
            ).as_candidate_metadata(),
        ),
        _event(
            event_type=AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED,
            object_type=AuditObjectType.SHOP_CUSTOMER,
            object_id=relationship_id,
            actor=actor,
            metadata=ShopCustomerPolicyUpdatedAuditPayload(
                old_policy=old_policy,
                new_policy=new_policy,
                revision=ShopCustomerRevision(2),
            ).as_candidate_metadata(),
        ),
        _event(
            event_type=AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED,
            object_type=AuditObjectType.SHOP,
            object_id=shop.id,
            actor=actor,
            metadata=ShopCustomerDefaultsUpdatedAuditPayload(
                old_defaults=old_defaults,
                new_defaults=new_defaults,
            ).as_candidate_metadata(),
        ),
    )

    for event in events:
        append_audit_event(db_session, event)

    rows = tuple(
        db_session.scalars(select(AuditLog).order_by(AuditLog.event_type.asc()))
    )
    assert len(rows) == 3
    assert {row.event_type for row in rows} == {
        event.event_type.value for event in events
    }
    assert all("<redacted>" in repr(row) for row in rows)


@pytest.mark.integration
def test_m12_audit_db_shape_rejects_extra_or_invalid_payload(
    db_session: Session,
) -> None:
    actor = _add_user(db_session)
    malformed = AuditLog(
        occurred_at=NOW,
        event_type=AuditEventType.SHOP_CUSTOMER_LINKED.value,
        actor_kind=AuditActorKind.USER.value,
        actor_user_id=actor.id,
        object_type=AuditObjectType.SHOP_CUSTOMER.value,
        object_id=uuid4(),
        payload={
            "outcome": "created",
            "credit_limit_uzs": 1_000_000,
            "max_open_debts": 2,
            "list_status": "normal",
            "revision": 1,
            "phone": "forbidden",
        },
    )

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(malformed)
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == (
        "ck_audit_log_payload_exact_shape"
    )
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_pair_unique_conflict_is_the_only_recovered_insert_constraint(
    db_session: Session,
) -> None:
    actor = _add_user(db_session)
    target = _add_user(db_session)
    _add_active_customer(db_session, user=target)
    shop = _add_shop(db_session, name="Conflict tenant")
    predecessors = _locked_predecessors(
        db_session,
        shop=shop,
        actor=actor,
        target=target,
    )
    relationship_id = ShopCustomerId(uuid4())
    snapshot = ShopDefaultCreditPolicy().for_new_link()
    created = insert_shop_customer(
        db_session,
        locked_predecessors=predecessors,
        shop_customer_id=relationship_id,
        snapshot=snapshot,
        created_by_user_id=UserId(actor.id),
        now=NOW,
    )
    assert created is not None

    recovered = insert_shop_customer(
        db_session,
        locked_predecessors=predecessors,
        shop_customer_id=ShopCustomerId(uuid4()),
        snapshot=snapshot,
        created_by_user_id=UserId(actor.id),
        now=NOW,
    )

    assert recovered is None
    assert db_session.scalar(select(func.count()).select_from(ShopCustomer)) == 1
    db_session.commit()
    db_session.expunge(created)

    other_target = _add_user(db_session)
    _add_active_customer(db_session, user=other_target)
    other_predecessors = _locked_predecessors(
        db_session,
        shop=shop,
        actor=actor,
        target=other_target,
    )
    with pytest.raises(IntegrityError) as caught:
        insert_shop_customer(
            db_session,
            locked_predecessors=other_predecessors,
            shop_customer_id=relationship_id,
            snapshot=snapshot,
            created_by_user_id=UserId(actor.id),
            now=NOW,
        )

    assert caught.value.orig.diag.constraint_name == "pk_shop_customers"
    assert db_session.scalar(select(func.count()).select_from(ShopCustomer)) == 1


@pytest.mark.integration
def test_audit_writer_failure_rolls_back_relationship_with_outer_transaction(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as setup:
        actor_id = _add_user(setup).id
        target_id = _add_user(setup).id
        target = setup.get(User, target_id)
        assert target is not None
        _add_active_customer(setup, user=target)
        shop_id = _add_shop(setup, name="Atomic tenant").id

    with pytest.raises(RuntimeError, match="injected audit failure"):
        with factory.begin() as transaction:
            actor = transaction.get(User, actor_id)
            target = transaction.get(User, target_id)
            shop = transaction.get(Shop, shop_id)
            assert actor is not None and target is not None and shop is not None
            predecessors = _locked_predecessors(
                transaction,
                shop=shop,
                actor=actor,
                target=target,
            )
            assert (
                insert_shop_customer(
                    transaction,
                    locked_predecessors=predecessors,
                    shop_customer_id=ShopCustomerId(uuid4()),
                    snapshot=ShopDefaultCreditPolicy().for_new_link(),
                    created_by_user_id=UserId(actor.id),
                    now=NOW,
                )
                is not None
            )
            raise RuntimeError("injected audit failure")

    with factory() as verification:
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 0
