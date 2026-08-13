from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4, uuid5

import pytest
from alembic.config import Config
from sqlalchemy import event, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.audit.models import AuditLog
from app.auth.models import User
from app.customer.models import Customer
from app.debt.models import Debt
from app.payment.models import Payment
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer
from tests.postgresql import cleanup_m2_tables

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M15_REVISION = "b6c7d8e9f0a1"
M16_REVISION = "c7d8e9f0a1b2"
NAMESPACE = UUID("c7d8e9f0-a1b2-5c16-8000-000000000001")
CREATED = datetime(2026, 8, 8, 8, tzinfo=UTC)
ACCEPTED = datetime(2026, 8, 9, 8, tzinfo=UTC)
PAID = datetime(2026, 8, 10, 8, tzinfo=UTC)
OVERDUE = datetime(2026, 8, 11, 8, tzinfo=UTC)


def _config(*, connection=None) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


@pytest.fixture
def m15_database(m2_test_database: Engine) -> Generator[Engine, None, None]:
    command.downgrade(_config(), M15_REVISION)
    try:
        yield m2_test_database
    finally:
        if _revision(m2_test_database) == M16_REVISION:
            with m2_test_database.begin() as connection:
                connection.execute(text("DELETE FROM disclosure_view_logs"))
                connection.execute(text("DELETE FROM rating_events"))
            command.downgrade(_config(), M15_REVISION)
        cleanup_m2_tables(m2_test_database)
        command.upgrade(_config(), "head")


def _parents(session: Session) -> tuple[User, ShopCustomer]:
    actor = User(phone=f"+998{uuid4().int % 1_000_000_000:09d}", is_active=True)
    customer_user = User(phone=f"+998{uuid4().int % 1_000_000_000:09d}", is_active=True)
    session.add_all((actor, customer_user))
    session.flush()
    customer = Customer(
        user_id=customer_user.id,
        onboarding_status="active",
        activated_at=CREATED,
        created_at=CREATED,
        updated_at=CREATED,
    )
    shop = Shop(
        name=f"M16 migration {uuid4().hex[:8]}",
        phone=f"+998{uuid4().int % 1_000_000_000:09d}",
        created_at=CREATED,
        updated_at=CREATED,
    )
    session.add_all((customer, shop))
    session.flush()
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=10,
        list_status="normal",
        revision=1,
        created_by_user_id=actor.id,
        created_at=CREATED,
        updated_at=CREATED,
    )
    session.add(relation)
    session.flush()
    return actor, relation


def _positive_source(
    session: Session,
    *,
    actor: User,
    relation: ShopCustomer,
    paid_at: datetime = PAID,
) -> UUID:
    debt_id = uuid4()
    session.execute(
        text(
            "INSERT INTO debts (id,shop_customer_id,created_by_user_id,"
            "original_amount_uzs,discount_basis_points,discounted_amount_uzs,"
            "due_date,pending_expires_at,status,revision,accepted_at,paid_at,"
            "created_at,updated_at) VALUES (:id,:shop_customer_id,:actor_id,"
            ":original_amount,:discount_basis_points,:discounted_amount,"
            ":due_date,:pending_expires_at,'paid',3,:accepted_at,:paid_at,"
            ":created_at,:updated_at)"
        ),
        {
            "id": debt_id,
            "shop_customer_id": relation.id,
            "actor_id": actor.id,
            "original_amount": Decimal("100000"),
            "discount_basis_points": 1000,
            "discounted_amount": Decimal("90000"),
            "due_date": date(2026, 8, 12),
            "pending_expires_at": CREATED + timedelta(hours=72),
            "accepted_at": ACCEPTED,
            "paid_at": paid_at,
            "created_at": CREATED,
            "updated_at": paid_at,
        },
    )
    payment = Payment(
        id=uuid4(),
        debt_id=debt_id,
        recorded_by_user_id=actor.id,
        amount_uzs=Decimal("90000"),
        method="cash",
        debt_revision_after=3,
        created_at=paid_at,
    )
    session.add(payment)
    session.flush()
    session.add_all(
        (
            AuditLog(
                event_type="payment.recorded",
                actor_kind="USER",
                actor_user_id=actor.id,
                object_type="payment",
                object_id=payment.id,
                payload={
                    "amount_uzs": 90000,
                    "method": "cash",
                    "from_status": "active",
                    "to_status": "paid",
                    "debt_revision_after": 3,
                },
                occurred_at=paid_at,
            ),
            AuditLog(
                event_type="debt.paid",
                actor_kind="USER",
                actor_user_id=actor.id,
                object_type="debt",
                object_id=debt_id,
                payload={"source": "payment", "debt_revision_after": 3},
                occurred_at=paid_at,
            ),
        )
    )
    session.flush()
    return debt_id


def _negative_source(
    session: Session,
    *,
    actor: User,
    relation: ShopCustomer,
    late_paid: bool = False,
) -> UUID:
    debt_id = uuid4()
    paid_at = OVERDUE + timedelta(hours=2) if late_paid else None
    session.execute(
        text(
            "INSERT INTO debts (id,shop_customer_id,created_by_user_id,"
            "original_amount_uzs,discount_basis_points,discounted_amount_uzs,"
            "due_date,pending_expires_at,status,revision,accepted_at,overdue_at,"
            "overdue_revision,paid_at,created_at,updated_at) VALUES "
            "(:id,:shop_customer_id,:actor_id,:original_amount,"
            ":discount_basis_points,:discounted_amount,:due_date,"
            ":pending_expires_at,:status,:revision,:accepted_at,:overdue_at,"
            "3,:paid_at,:created_at,:updated_at)"
        ),
        {
            "id": debt_id,
            "shop_customer_id": relation.id,
            "actor_id": actor.id,
            "original_amount": Decimal("100000"),
            "discount_basis_points": 1000,
            "discounted_amount": Decimal("90000"),
            "due_date": date(2026, 8, 10),
            "pending_expires_at": CREATED + timedelta(hours=72),
            "status": "paid" if late_paid else "overdue",
            "revision": 4 if late_paid else 3,
            "accepted_at": ACCEPTED,
            "overdue_at": OVERDUE,
            "paid_at": paid_at,
            "created_at": CREATED,
            "updated_at": paid_at or OVERDUE,
        },
    )
    session.add_all(
        (
            AuditLog(
                event_type="debt.overdue",
                actor_kind="SYSTEM",
                actor_user_id=None,
                object_type="debt",
                object_id=debt_id,
                payload={
                    "source": "batch",
                    "from_status": "active",
                    "to_status": "overdue",
                    "overdue_revision": 3,
                    "business_date": "2026-08-11",
                },
                occurred_at=OVERDUE,
            ),
            AuditLog(
                event_type="debt.clawback_applied",
                actor_kind="SYSTEM",
                actor_user_id=None,
                object_type="debt",
                object_id=debt_id,
                payload={
                    "source": "batch",
                    "from_basis": "discounted",
                    "to_basis": "original",
                    "balance_increase_uzs": 10000,
                    "overdue_revision": 3,
                },
                occurred_at=OVERDUE,
            ),
        )
    )
    session.flush()
    return debt_id


@pytest.mark.integration
def test_fresh_upgrade_empty_downgrade_and_reupgrade(m15_database: Engine) -> None:
    command.upgrade(_config(), M16_REVISION)
    assert _revision(m15_database) == M16_REVISION
    assert {"rating_events", "disclosure_view_logs"} <= set(
        inspect(m15_database).get_table_names()
    )
    command.downgrade(_config(), M15_REVISION)
    assert _revision(m15_database) == M15_REVISION
    assert {"rating_events", "disclosure_view_logs"}.isdisjoint(
        inspect(m15_database).get_table_names()
    )
    command.upgrade(_config(), M16_REVISION)
    assert _revision(m15_database) == M16_REVISION


@pytest.mark.integration
def test_mixed_history_reconciles_deterministically_and_preserves_sources(
    m15_database: Engine,
) -> None:
    with Session(m15_database) as session, session.begin():
        actor, relation = _parents(session)
        winner = _positive_source(session, actor=actor, relation=relation)
        _positive_source(
            session,
            actor=actor,
            relation=relation,
            paid_at=PAID + timedelta(hours=1),
        )
        negative = _negative_source(session, actor=actor, relation=relation)
        late_paid = _negative_source(
            session,
            actor=actor,
            relation=relation,
            late_paid=True,
        )
        session.execute(
            text(
                "INSERT INTO debts (id,shop_customer_id,created_by_user_id,"
                "original_amount_uzs,discount_basis_points,discounted_amount_uzs,"
                "due_date,pending_expires_at,status,revision,accepted_at,"
                "created_at,updated_at) VALUES (:id,:shop_customer_id,:actor_id,"
                "100000,0,100000,:due_date,:pending_expires_at,'active',2,"
                ":accepted_at,:created_at,:updated_at)"
            ),
            {
                "id": uuid4(),
                "shop_customer_id": relation.id,
                "actor_id": actor.id,
                "due_date": date(2026, 8, 1),
                "pending_expires_at": CREATED + timedelta(hours=72),
                "accepted_at": ACCEPTED,
                "created_at": CREATED,
                "updated_at": ACCEPTED,
            },
        )
        winner_id = winner
        negative_id = negative
        late_paid_id = late_paid
        before = {
            table: session.execute(
                text(
                    f"SELECT md5(row_to_json(source_row)::text) "
                    f"FROM {table} source_row ORDER BY id"
                )
            )
            .scalars()
            .all()
            for table in ("debts", "payments", "audit_log")
        }

    command.upgrade(_config(), M16_REVISION)

    with Session(m15_database) as session:
        rows = (
            session.execute(
                text(
                    "SELECT id,debt_id,event_type,delta,occurred_at,business_date,"
                    "recording_source FROM rating_events "
                    "ORDER BY occurred_at,debt_id,event_type"
                )
            )
            .mappings()
            .all()
        )
        assert [(row["debt_id"], row["event_type"], row["delta"]) for row in rows] == [
            (winner_id, "on_time_paid", 5),
            *sorted(
                (
                    (negative_id, "overdue", -15),
                    (late_paid_id, "overdue", -15),
                ),
                key=lambda item: item[0],
            ),
        ]
        assert rows[0]["id"] == uuid5(NAMESPACE, f"on_time_paid:{winner_id}")
        assert {row["id"] for row in rows[1:]} == {
            uuid5(NAMESPACE, f"overdue:{negative_id}"),
            uuid5(NAMESPACE, f"overdue:{late_paid_id}"),
        }
        assert all(
            row["recording_source"] == "historical_reconciliation" for row in rows
        )
        after = {
            table: session.execute(
                text(
                    f"SELECT md5(row_to_json(source_row)::text) "
                    f"FROM {table} source_row ORDER BY id"
                )
            )
            .scalars()
            .all()
            for table in ("debts", "payments", "audit_log")
        }
        assert after == before

    with pytest.raises(RuntimeError, match="M16 downgrade blocked:"):
        command.downgrade(_config(), M15_REVISION)
    assert _revision(m15_database) == M16_REVISION


@pytest.mark.integration
def test_incoherent_positive_aborts_whole_upgrade(m15_database: Engine) -> None:
    with Session(m15_database) as session, session.begin():
        actor, relation = _parents(session)
        debt = _positive_source(session, actor=actor, relation=relation)
        payment = session.scalar(select(Payment).where(Payment.debt_id == debt))
        assert payment is not None
        session.execute(
            text("DELETE FROM audit_log WHERE object_id = :payment_id"),
            {"payment_id": payment.id},
        )

    with pytest.raises(
        RuntimeError,
        match="M16 reconciliation blocked: incoherent positive source history",
    ):
        command.upgrade(_config(), M16_REVISION)
    assert _revision(m15_database) == M15_REVISION
    assert "rating_events" not in inspect(m15_database).get_table_names()


@pytest.mark.integration
@pytest.mark.parametrize(
    "corruption",
    ("no_payment", "wrong_total", "wrong_terminal", "wrong_payment_audit"),
)
def test_each_positive_source_contradiction_aborts_upgrade(
    m15_database: Engine,
    corruption: str,
) -> None:
    with Session(m15_database) as session, session.begin():
        actor, relation = _parents(session)
        debt = _positive_source(session, actor=actor, relation=relation)
        payment = session.scalar(select(Payment).where(Payment.debt_id == debt))
        assert payment is not None
        if corruption == "no_payment":
            session.delete(payment)
        elif corruption == "wrong_total":
            payment.amount_uzs = Decimal("89999")
        elif corruption == "wrong_terminal":
            payment.created_at = PAID - timedelta(seconds=1)
        else:
            session.execute(
                text("DELETE FROM audit_log WHERE object_id = :payment_id"),
                {"payment_id": payment.id},
            )

    with pytest.raises(RuntimeError, match="incoherent positive source history"):
        command.upgrade(_config(), M16_REVISION)
    assert _revision(m15_database) == M15_REVISION


@pytest.mark.integration
def test_mismatched_overdue_audit_pair_aborts_upgrade(
    m15_database: Engine,
) -> None:
    with Session(m15_database) as session, session.begin():
        actor, relation = _parents(session)
        debt = _negative_source(session, actor=actor, relation=relation)
        session.execute(
            text(
                "DELETE FROM audit_log WHERE object_id = :debt_id "
                "AND event_type = 'debt.clawback_applied'"
            ),
            {"debt_id": debt},
        )

    with pytest.raises(RuntimeError, match="incoherent negative source history"):
        command.upgrade(_config(), M16_REVISION)
    assert _revision(m15_database) == M15_REVISION


@pytest.mark.integration
def test_every_new_required_column_rejects_null(m2_test_database: Engine) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, relation = _parents(session)
        debt = Debt(
            shop_customer_id=relation.id,
            created_by_user_id=actor.id,
            original_amount_uzs=Decimal("100000"),
            discount_basis_points=0,
            discounted_amount_uzs=Decimal("100000"),
            due_date=date(2026, 8, 20),
            pending_expires_at=CREATED + timedelta(hours=72),
            status="active",
            revision=2,
            accepted_at=ACCEPTED,
            created_at=CREATED,
            updated_at=ACCEPTED,
        )
        session.add(debt)
        session.flush()
        rating_values = {
            "id": uuid4(),
            "shop_customer_id": relation.id,
            "debt_id": debt.id,
            "event_type": "overdue",
            "delta": -15,
            "occurred_at": OVERDUE,
            "business_date": date(2026, 8, 11),
            "recording_source": "live",
        }
        disclosure_values = {
            "id": uuid4(),
            "actor_user_id": actor.id,
            "shop_id": relation.shop_id,
            "shop_customer_id": relation.id,
            "purpose": "credit_limit_review",
            "band": "yellow",
            "created_at": PAID,
        }
        for table_name, values in (
            ("rating_events", rating_values),
            ("disclosure_view_logs", disclosure_values),
        ):
            columns = ",".join(values)
            parameters = ",".join(f":{column}" for column in values)
            for column in values:
                invalid = dict(values)
                invalid[column] = None
                with pytest.raises(IntegrityError) as caught:
                    with session.begin_nested():
                        session.execute(
                            text(
                                f"INSERT INTO {table_name} ({columns}) "
                                f"VALUES ({parameters})"
                            ),
                            invalid,
                        )
                assert caught.value.orig.diag.column_name == column


@pytest.mark.integration
@pytest.mark.parametrize(
    ("actor_kind", "actor", "object_type", "payload"),
    (
        (
            "USER",
            "valid",
            "disclosure_view",
            {"purpose": 1, "band": "green"},
        ),
        (
            "USER",
            "valid",
            "disclosure_view",
            {"purpose": "credit_limit_review", "band": True},
        ),
        (
            "USER",
            "valid",
            "disclosure_view",
            {"purpose": "future", "band": "green"},
        ),
        (
            "SYSTEM",
            None,
            "disclosure_view",
            {"purpose": "credit_limit_review", "band": "green"},
        ),
        (
            "USER",
            "valid",
            "debt",
            {"purpose": "credit_limit_review", "band": "green"},
        ),
    ),
)
def test_disclosure_audit_rejects_invalid_json_value_actor_and_object(
    m2_test_database: Engine,
    actor_kind: str,
    actor: str | None,
    object_type: str,
    payload: dict[str, object],
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, _relation = _parents(session)
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    AuditLog(
                        event_type="disclosure.risk_band_viewed",
                        actor_kind=actor_kind,
                        actor_user_id=user.id if actor == "valid" else None,
                        object_type=object_type,
                        object_id=uuid4(),
                        payload=payload,
                        occurred_at=PAID,
                    )
                )
                session.flush()


@pytest.mark.integration
def test_debt_table_lock_closes_in_flight_scan_race_without_restart_claim(
    m15_database: Engine,
) -> None:
    writer = m15_database.connect()
    writer_tx = writer.begin()
    writer.execute(text("LOCK TABLE debts IN ROW EXCLUSIVE MODE"))

    lock_statement_reached = Event()
    failure: list[BaseException] = []

    def before_cursor_execute(
        _connection, _cursor, statement, _parameters, _context, _many
    ) -> None:
        if statement == "LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE":
            lock_statement_reached.set()

    def migrate() -> None:
        try:
            with m15_database.connect() as connection:
                event.listen(connection, "before_cursor_execute", before_cursor_execute)
                command.upgrade(_config(connection=connection), M16_REVISION)
        except BaseException as exc:  # pragma: no cover - asserted in caller
            failure.append(exc)

    worker = Thread(target=migrate)
    worker.start()
    lock_statement_reached.wait()
    assert worker.is_alive()
    writer_tx.commit()
    writer.close()
    worker.join()

    assert failure == []
    assert _revision(m15_database) == M16_REVISION
