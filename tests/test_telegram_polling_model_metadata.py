from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    String,
)

from app.db import Base
from app.telegram.models import TelegramPollingState, TelegramUpdateFailure

FORBIDDEN_OPERATIONAL_COLUMNS = {
    "raw_update",
    "update_json",
    "message",
    "message_text",
    "payload",
    "token",
    "token_hash",
    "telegram_chat_id",
    "telegram_user_id",
    "user_id",
    "shop_id",
    "customer_id",
    "phone",
    "client_ip",
    "bot_token",
    "username",
    "exception",
    "traceback",
    "http_body",
    "sql",
    "sql_params",
    "metadata",
}


def check_constraints(model) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_operational_models_are_registered_with_exact_columns() -> None:
    assert (
        Base.metadata.tables["telegram_polling_state"] is TelegramPollingState.__table__
    )
    assert (
        Base.metadata.tables["telegram_update_failures"]
        is TelegramUpdateFailure.__table__
    )
    assert set(TelegramPollingState.__table__.columns.keys()) == {
        "id",
        "next_offset",
        "heartbeat_at",
        "ready_at",
        "updated_at",
    }
    assert set(TelegramUpdateFailure.__table__.columns.keys()) == {
        "update_id",
        "attempt_count",
        "failure_code",
        "first_failed_at",
        "last_failed_at",
        "quarantined_at",
    }


def test_polling_state_types_and_constraints_match_contract() -> None:
    columns = TelegramPollingState.__table__.columns
    constraints = check_constraints(TelegramPollingState)

    assert isinstance(columns["id"].type, SmallInteger)
    assert columns["id"].primary_key is True
    assert isinstance(columns["next_offset"].type, BigInteger)
    assert columns["next_offset"].nullable is False
    assert constraints == {
        "ck_telegram_polling_state_singleton": "id = 1",
        "ck_telegram_polling_state_next_offset_nonnegative": "next_offset >= 0",
        "ck_telegram_polling_state_ready_requires_heartbeat": (
            "ready_at IS NULL OR heartbeat_at IS NOT NULL"
        ),
        "ck_telegram_polling_state_heartbeat_not_before_ready": (
            "heartbeat_at IS NULL OR ready_at IS NULL OR heartbeat_at >= ready_at"
        ),
    }


def test_failure_ledger_types_constraints_and_index_match_contract() -> None:
    columns = TelegramUpdateFailure.__table__.columns
    constraints = check_constraints(TelegramUpdateFailure)

    assert isinstance(columns["update_id"].type, BigInteger)
    assert columns["update_id"].primary_key is True
    assert isinstance(columns["attempt_count"].type, SmallInteger)
    assert isinstance(columns["failure_code"].type, String)
    assert columns["failure_code"].type.length == 64
    assert constraints == {
        "ck_telegram_update_failures_update_id_nonnegative": "update_id >= 0",
        "ck_telegram_update_failures_attempt_count": ("attempt_count BETWEEN 1 AND 5"),
        "ck_telegram_update_failures_code_format": (
            "failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'"
        ),
        "ck_telegram_update_failures_time_order": ("last_failed_at >= first_failed_at"),
        "ck_telegram_update_failures_quarantine_state": (
            "(attempt_count < 5 AND quarantined_at IS NULL) "
            "OR (attempt_count = 5 AND quarantined_at IS NOT NULL)"
        ),
        "ck_telegram_update_failures_quarantine_time": (
            "quarantined_at IS NULL OR quarantined_at >= last_failed_at"
        ),
    }
    indexes = {
        index.name: index
        for index in TelegramUpdateFailure.__table__.indexes
        if isinstance(index, Index)
    }
    quarantine_index = indexes["ix_telegram_update_failures_quarantined_at"]
    assert quarantine_index.unique is False
    assert [column.name for column in quarantine_index.columns] == ["quarantined_at"]


def test_operational_timestamps_are_timezone_aware() -> None:
    timestamp_columns = {
        TelegramPollingState: ("heartbeat_at", "ready_at", "updated_at"),
        TelegramUpdateFailure: (
            "first_failed_at",
            "last_failed_at",
            "quarantined_at",
        ),
    }

    for model, names in timestamp_columns.items():
        for name in names:
            column = model.__table__.columns[name]
            assert isinstance(column.type, DateTime)
            assert column.type.timezone is True


def test_operational_tables_have_no_forbidden_identity_or_raw_data_columns() -> None:
    for model in (TelegramPollingState, TelegramUpdateFailure):
        assert FORBIDDEN_OPERATIONAL_COLUMNS.isdisjoint(model.__table__.columns)
