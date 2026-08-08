from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, Integer, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.db import Base
from app.debt.models import Debt

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_debt_and_idempotency_models_are_registered_for_runtime_and_alembic() -> None:
    db_source = (PROJECT_ROOT / "app/db.py").read_text(encoding="utf-8")
    env_source = (PROJECT_ROOT / "alembic/env.py").read_text(encoding="utf-8")

    for source in (db_source, env_source):
        assert "from app.debt import models as _debt_models  # noqa: F401" in source
        assert (
            "from app.idempotency import models as _idempotency_models  # noqa: F401"
            in source
        )


def test_debt_is_one_registered_pii_free_m13_table() -> None:
    table = Debt.__table__

    assert Base.metadata.tables["debts"] is table
    assert tuple(table.columns.keys()) == (
        "id",
        "shop_customer_id",
        "created_by_user_id",
        "original_amount_uzs",
        "discount_basis_points",
        "discounted_amount_uzs",
        "due_date",
        "pending_expires_at",
        "status",
        "revision",
        "rejection_reason",
        "cancellation_reason",
        "accepted_at",
        "rejected_at",
        "cancelled_at",
        "expired_at",
        "paid_at",
        "created_at",
        "updated_at",
    )
    assert not {
        "balance",
        "paid_amount",
        "remaining_amount",
        "payment",
        "phone",
        "name",
        "offer_body",
        "idempotency_key",
        "rating",
        "notification",
        "deleted_at",
    } & set(table.columns)


def test_debt_columns_defaults_and_timestamps_are_exact() -> None:
    table = Debt.__table__

    for column_name in ("id", "shop_customer_id", "created_by_user_id"):
        assert isinstance(table.c[column_name].type, PostgresUUID)
        assert table.c[column_name].nullable is False
    for column_name in ("original_amount_uzs", "discounted_amount_uzs"):
        assert isinstance(table.c[column_name].type, Numeric)
        assert table.c[column_name].type.precision == 18
        assert table.c[column_name].type.scale == 0
    assert isinstance(table.c.discount_basis_points.type, SmallInteger)
    assert isinstance(table.c.due_date.type, Date)
    assert isinstance(table.c.status.type, Text)
    assert isinstance(table.c.revision.type, Integer)
    assert isinstance(table.c.rejection_reason.type, Text)
    assert isinstance(table.c.cancellation_reason.type, Text)
    assert table.c.rejection_reason.nullable is True
    assert table.c.cancellation_reason.nullable is True
    assert table.c.status.default is not None
    assert table.c.status.default.arg == "pending"
    assert table.c.status.server_default is not None
    assert str(table.c.status.server_default.arg) == "'pending'"
    assert table.c.revision.default is not None
    assert table.c.revision.default.arg == 1
    assert table.c.revision.server_default is not None
    assert str(table.c.revision.server_default.arg) == "1"
    for column_name in (
        "pending_expires_at",
        "accepted_at",
        "rejected_at",
        "cancelled_at",
        "expired_at",
        "paid_at",
        "created_at",
        "updated_at",
    ):
        assert table.c[column_name].type.timezone is True
    for column_name in ("created_at", "updated_at"):
        assert table.c[column_name].server_default is not None
        assert str(table.c[column_name].server_default.arg) == "CURRENT_TIMESTAMP"


def test_debt_constraints_indexes_and_foreign_keys_are_exact() -> None:
    table = Debt.__table__
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    indexes = {
        index.name: tuple(str(expression) for expression in index.expressions)
        for index in table.indexes
    }
    foreign_keys = {
        foreign_key.constraint.name: (
            foreign_key.target_fullname,
            foreign_key.ondelete,
        )
        for column in table.columns
        for foreign_key in column.foreign_keys
    }

    assert checks == {
        "ck_debts_original_amount_uzs_bounds": (
            "original_amount_uzs BETWEEN 1 AND 1000000000000"
        ),
        "ck_debts_discount_basis_points_bounds": (
            "discount_basis_points BETWEEN 0 AND 10000"
        ),
        "ck_debts_discounted_amount_uzs_bounds": (
            "discounted_amount_uzs BETWEEN 1 AND original_amount_uzs"
        ),
        "ck_debts_status_allowed": (
            "status IN ('pending', 'active', 'rejected', 'cancelled', 'expired', "
            "'paid')"
        ),
        "ck_debts_revision_positive": "revision > 0",
        "ck_debts_rejection_reason_normalized": (
            "rejection_reason IS NULL OR (char_length(rejection_reason) BETWEEN 1 "
            "AND 500 AND rejection_reason = btrim(rejection_reason) AND "
            "rejection_reason !~ '[[:cntrl:]]')"
        ),
        "ck_debts_cancellation_reason_normalized": (
            "cancellation_reason IS NULL OR (char_length(cancellation_reason) "
            "BETWEEN 1 AND 500 AND cancellation_reason = "
            "btrim(cancellation_reason) AND cancellation_reason !~ '[[:cntrl:]]')"
        ),
        "ck_debts_status_metadata_matches_status": (
            "(status = 'pending' AND accepted_at IS NULL AND rejected_at IS NULL "
            "AND cancelled_at IS NULL AND expired_at IS NULL AND "
            "paid_at IS NULL AND rejection_reason IS NULL AND "
            "cancellation_reason IS NULL) OR "
            "(status = 'active' AND accepted_at IS NOT NULL AND rejected_at IS NULL "
            "AND cancelled_at IS NULL AND expired_at IS NULL AND "
            "paid_at IS NULL AND rejection_reason IS NULL AND "
            "cancellation_reason IS NULL) OR "
            "(status = 'rejected' AND accepted_at IS NULL AND rejected_at IS NOT NULL "
            "AND cancelled_at IS NULL AND expired_at IS NULL AND "
            "paid_at IS NULL AND cancellation_reason IS NULL) OR "
            "(status = 'cancelled' AND "
            "accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NOT NULL "
            "AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL "
            "AND cancellation_reason IS NOT NULL) OR "
            "(status = 'expired' AND "
            "accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL "
            "AND expired_at IS NOT NULL AND paid_at IS NULL AND "
            "rejection_reason IS NULL AND cancellation_reason IS NULL) OR "
            "(status = 'paid' AND accepted_at IS NOT NULL AND rejected_at IS NULL "
            "AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NOT NULL "
            "AND rejection_reason IS NULL AND cancellation_reason IS NULL)"
        ),
        "ck_debts_pending_expires_at_exact": (
            "pending_expires_at = created_at + INTERVAL '72 hours'"
        ),
        "ck_debts_timestamp_order": (
            "updated_at >= created_at AND (accepted_at IS NULL OR accepted_at >= "
            "created_at) AND (rejected_at IS NULL OR rejected_at >= created_at) AND "
            "(cancelled_at IS NULL OR cancelled_at >= created_at) AND "
            "(expired_at IS NULL OR expired_at >= created_at) AND "
            "(paid_at IS NULL OR (accepted_at IS NOT NULL AND paid_at >= accepted_at "
            "AND updated_at >= paid_at))"
        ),
    }
    assert indexes == {
        "ix_debts_shop_customer_id_created_at_id": (
            "debts.shop_customer_id",
            "created_at DESC",
            "debts.id",
        ),
        "ix_debts_shop_customer_id_status_due_date_id": (
            "debts.shop_customer_id",
            "debts.status",
            "debts.due_date",
            "debts.id",
        ),
        "ix_debts_status_pending_expires_at_id": (
            "debts.status",
            "debts.pending_expires_at",
            "debts.id",
        ),
    }
    assert foreign_keys == {
        "fk_debts_shop_customer_id_shop_customers_id": (
            "shop_customers.id",
            "RESTRICT",
        ),
        "fk_debts_created_by_user_id_users_id": ("users.id", "RESTRICT"),
    }
    assert "Asia/Tashkent" in Debt.__doc__
    assert "due_date" not in checks["ck_debts_timestamp_order"]
    assert "paid_at IS NULL" in checks["ck_debts_status_metadata_matches_status"]


def test_debt_repr_redacts_identifiers_money_reasons_and_timestamps() -> None:
    identifiers = [UUID(int=value) for value in range(1, 4)]
    created_at = datetime(2026, 8, 7, 9, 10, tzinfo=UTC)
    model = Debt(
        id=identifiers[0],
        shop_customer_id=identifiers[1],
        created_by_user_id=identifiers[2],
        original_amount_uzs=Decimal("987654321"),
        discount_basis_points=37,
        discounted_amount_uzs=Decimal("987617777"),
        due_date=date(2026, 8, 11),
        pending_expires_at=created_at + timedelta(hours=72),
        status="cancelled",
        revision=2,
        cancellation_reason="SECRET CANCELLATION REASON",
        cancelled_at=created_at + timedelta(minutes=1),
        paid_at=None,
        created_at=created_at,
        updated_at=created_at + timedelta(minutes=1),
    )

    rendered = repr(model)

    for value in (
        *(str(identifier) for identifier in identifiers),
        "987654321",
        "37",
        "987617777",
        "SECRET CANCELLATION REASON",
        repr(created_at),
    ):
        assert value not in rendered
    assert "<redacted>" in rendered
