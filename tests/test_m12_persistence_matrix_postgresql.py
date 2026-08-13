from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer
from tests.test_shop_customer_repository_postgresql import (
    _add_active_customer,
    _add_shop,
    _add_user,
)

M12_REVISION = "e3f4a5b6c7d8"
M14_REVISION = "a5b6c7d8e9f0"
M15_REVISION = "b6c7d8e9f0a1"
M16_REVISION = "c7d8e9f0a1b2"
M17_REVISION = "e9f0a1b2c3d4"
M12_SCHEMA_TABLES = {
    "shop_customers",
    "otp_challenge_events",
    "otp_dispatches",
    "otp_challenges",
    "customer_documents",
    "customer_identities",
    "audit_log",
    "offer_acceptances",
    "offer_texts",
    "offer_versions",
    "object_files",
    "otp_dispatcher_state",
    "telegram_update_failures",
    "telegram_polling_state",
    "telegram_link_events",
    "telegram_link_tokens",
    "telegram_links",
    "customers",
    "auth_rate_limits",
    "sessions",
    "shop_staff_events",
    "shop_status_events",
    "shop_staff",
    "shops",
    "users",
}
NOW = datetime(2026, 8, 7, 15, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    factory = create_database_session_factory(m2_test_database)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _dependencies(session: Session) -> tuple[User, User, Customer, Shop]:
    actor = _add_user(session)
    target = _add_user(session)
    customer = _add_active_customer(session, user=target)
    shop = _add_shop(session, name="Constraint tenant")
    return actor, target, customer, shop


def _row(
    *,
    actor: User,
    customer: Customer,
    shop: Shop,
    **overrides,
) -> ShopCustomer:
    values = {
        "id": uuid4(),
        "shop_id": shop.id,
        "customer_id": customer.id,
        "credit_limit_uzs": Decimal("1000000"),
        "max_open_debts": 2,
        "list_status": "normal",
        "revision": 1,
        "created_by_user_id": actor.id,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ShopCustomer(**values)


@pytest.mark.integration
def test_current_database_extends_exact_m12_metadata_at_m14_head(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        *M12_SCHEMA_TABLES,
        "debts",
        "idempotency_keys",
        "payments",
        "rating_events",
        "disclosure_view_logs",
        "payment_voids",
    }
    with m2_test_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            M17_REVISION
        )

    shop_columns = {column["name"]: column for column in inspector.get_columns("shops")}
    assert str(shop_columns["default_credit_limit_uzs"]["type"]) == "NUMERIC(18, 0)"
    assert str(shop_columns["default_max_open_debts"]["type"]) == "SMALLINT"
    relationship_columns = {
        column["name"]: column for column in inspector.get_columns("shop_customers")
    }
    assert str(relationship_columns["credit_limit_uzs"]["type"]) == ("NUMERIC(18, 0)")
    assert str(relationship_columns["max_open_debts"]["type"]) == "SMALLINT"
    assert set(relationship_columns) == {
        "id",
        "shop_id",
        "customer_id",
        "credit_limit_uzs",
        "max_open_debts",
        "list_status",
        "revision",
        "created_by_user_id",
        "created_at",
        "updated_at",
    }


@pytest.mark.integration
def test_database_defaults_are_whole_uzs_and_complete(db_session: Session) -> None:
    actor, _target, customer, shop = _dependencies(db_session)
    row = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=shop.default_credit_limit_uzs,
        max_open_debts=shop.default_max_open_debts,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(row)
    db_session.flush()

    assert shop.default_credit_limit_uzs == Decimal("1000000")
    assert shop.default_max_open_debts == 2
    assert row.credit_limit_uzs == Decimal("1000000")
    assert row.max_open_debts == 2
    assert row.list_status == "normal"
    assert row.revision == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    (
        (
            {"credit_limit_uzs": Decimal("-1")},
            "ck_shop_customers_credit_limit_uzs_bounds",
        ),
        (
            {"credit_limit_uzs": Decimal("1000000000001")},
            "ck_shop_customers_credit_limit_uzs_bounds",
        ),
        ({"max_open_debts": 0}, "ck_shop_customers_max_open_debts_bounds"),
        ({"max_open_debts": 101}, "ck_shop_customers_max_open_debts_bounds"),
        ({"list_status": "hidden"}, "ck_shop_customers_list_status_allowed"),
        ({"revision": 0}, "ck_shop_customers_revision_positive"),
        (
            {"updated_at": NOW - timedelta(seconds=1)},
            "ck_shop_customers_timestamp_order",
        ),
    ),
)
def test_shop_customer_checks_fail_with_exact_constraint_and_keep_session_usable(
    db_session: Session,
    overrides: dict[str, object],
    constraint_name: str,
) -> None:
    actor, _target, customer, shop = _dependencies(db_session)

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(
                _row(
                    actor=actor,
                    customer=customer,
                    shop=shop,
                    **overrides,
                )
            )
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == constraint_name
    assert db_session.scalar(select(func.count()).select_from(ShopCustomer)) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    (
        (
            {"default_credit_limit_uzs": Decimal("-1")},
            "ck_shops_default_credit_limit_uzs_bounds",
        ),
        (
            {"default_max_open_debts": 0},
            "ck_shops_default_max_open_debts_bounds",
        ),
    ),
)
def test_shop_default_checks_are_enforced_by_postgresql(
    db_session: Session,
    overrides: dict[str, object],
    constraint_name: str,
) -> None:
    shop = Shop(name="Invalid defaults tenant", phone=f"+998{uuid4().int % 10**9:09d}")
    for key, value in overrides.items():
        setattr(shop, key, value)

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(shop)
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == constraint_name
    assert db_session.scalar(select(func.count()).select_from(Shop)) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("parent", "constraint_name"),
    (
        ("shop", "fk_shop_customers_shop_id_shops_id"),
        ("customer", "fk_shop_customers_customer_id_customers_id"),
        ("actor", "fk_shop_customers_created_by_user_id_users_id"),
    ),
)
def test_shop_customer_foreign_keys_are_restrictive(
    db_session: Session,
    parent: str,
    constraint_name: str,
) -> None:
    actor, _target, customer, shop = _dependencies(db_session)
    row = _row(actor=actor, customer=customer, shop=shop)
    db_session.add(row)
    db_session.flush()
    parents = {"shop": shop, "customer": customer, "actor": actor}

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.delete(parents[parent])
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == constraint_name
    assert db_session.get(ShopCustomer, row.id) is row


@pytest.mark.integration
def test_pair_uniqueness_and_exact_indexes_are_live_in_postgresql(
    db_session: Session,
) -> None:
    actor, _target, customer, shop = _dependencies(db_session)
    db_session.add(_row(actor=actor, customer=customer, shop=shop))
    db_session.flush()

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(_row(actor=actor, customer=customer, shop=shop))
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == (
        "uq_shop_customers_shop_id_customer_id"
    )
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspect(db_session.bind).get_indexes("shop_customers")
    }
    assert indexes == {
        "ix_shop_customers_customer_id_created_at_id": (
            "customer_id",
            "created_at",
            "id",
        ),
        "ix_shop_customers_shop_id_created_at_id": (
            "shop_id",
            "created_at",
            "id",
        ),
        "uq_shop_customers_shop_id_customer_id": ("shop_id", "customer_id"),
        "uq_shop_customers_id_shop_id": ("id", "shop_id"),
    }
