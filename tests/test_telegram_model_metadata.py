from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.auth.models import User
from app.db import Base
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken


def unique_constraints(model) -> dict[str, set[str]]:
    return {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def check_constraints(model) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def indexes(model) -> dict[str, Index]:
    return {
        index.name: index
        for index in model.__table__.indexes
        if isinstance(index, Index)
    }


def test_telegram_tables_are_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["users"] is User.__table__
    assert Base.metadata.tables["telegram_links"] is TelegramLink.__table__
    assert Base.metadata.tables["telegram_link_tokens"] is TelegramLinkToken.__table__
    assert Base.metadata.tables["telegram_link_events"] is TelegramLinkEvent.__table__


def test_telegram_tables_have_only_m4_allowed_columns() -> None:
    assert set(TelegramLink.__table__.columns.keys()) == {
        "id",
        "user_id",
        "telegram_chat_id",
        "linked_at",
        "unlinked_at",
        "updated_at",
    }
    assert set(TelegramLinkToken.__table__.columns.keys()) == {
        "id",
        "user_id",
        "token_hash",
        "created_at",
        "expires_at",
        "consumed_at",
        "invalidated_at",
    }
    assert set(TelegramLinkEvent.__table__.columns.keys()) == {
        "id",
        "user_id",
        "action",
        "occurred_at",
    }


def test_telegram_models_use_uuid_primary_keys() -> None:
    for model in (TelegramLink, TelegramLinkToken, TelegramLinkEvent):
        id_column = model.__table__.columns["id"]

        assert isinstance(id_column.type, PostgresUUID)
        assert id_column.primary_key is True
        assert id_column.nullable is False


def test_telegram_user_foreign_keys_restrict_parent_delete() -> None:
    for model in (TelegramLink, TelegramLinkToken, TelegramLinkEvent):
        user_id_column = model.__table__.columns["user_id"]
        foreign_key = next(iter(user_id_column.foreign_keys))

        assert isinstance(user_id_column.type, PostgresUUID)
        assert user_id_column.nullable is False
        assert foreign_key.target_fullname == "users.id"
        assert foreign_key.ondelete == "RESTRICT"


def test_telegram_links_columns_and_state_constraints() -> None:
    columns = TelegramLink.__table__.columns

    assert isinstance(columns["telegram_chat_id"].type, BigInteger)
    assert columns["telegram_chat_id"].nullable is True
    assert unique_constraints(TelegramLink)["uq_telegram_links_user_id"] == {"user_id"}
    assert check_constraints(TelegramLink)["ck_telegram_links_state_consistent"] == (
        "(telegram_chat_id IS NOT NULL AND unlinked_at IS NULL) "
        "OR (telegram_chat_id IS NULL AND unlinked_at IS NOT NULL)"
    )


def test_telegram_links_active_chat_partial_unique_index() -> None:
    active_chat_index = indexes(TelegramLink)["uq_telegram_links_active_chat_id"]

    assert active_chat_index.unique is True
    assert {column.name for column in active_chat_index.columns} == {"telegram_chat_id"}
    assert str(active_chat_index.dialect_options["postgresql"]["where"]) == (
        "telegram_chat_id IS NOT NULL AND unlinked_at IS NULL"
    )


def test_telegram_link_tokens_hash_and_lifecycle_constraints() -> None:
    columns = TelegramLinkToken.__table__.columns
    token_constraints = check_constraints(TelegramLinkToken)

    assert isinstance(columns["token_hash"].type, String)
    assert columns["token_hash"].type.length == 64
    assert columns["token_hash"].nullable is False
    assert unique_constraints(TelegramLinkToken)[
        "uq_telegram_link_tokens_token_hash"
    ] == {"token_hash"}
    assert token_constraints["ck_telegram_link_tokens_token_hash_sha256_hex"] == (
        "token_hash ~ '^[0-9a-f]{64}$'"
    )
    assert (
        token_constraints["ck_telegram_link_tokens_expires_after_created"]
        == "expires_at > created_at"
    )
    assert (
        token_constraints["ck_telegram_link_tokens_terminal_state_exclusive"]
        == "NOT (consumed_at IS NOT NULL AND invalidated_at IS NOT NULL)"
    )


def test_telegram_link_tokens_one_outstanding_partial_unique_index() -> None:
    outstanding_index = indexes(TelegramLinkToken)[
        "uq_telegram_link_tokens_one_outstanding_per_user"
    ]

    assert outstanding_index.unique is True
    assert {column.name for column in outstanding_index.columns} == {"user_id"}
    assert str(outstanding_index.dialect_options["postgresql"]["where"]) == (
        "consumed_at IS NULL AND invalidated_at IS NULL"
    )


def test_telegram_link_events_action_check() -> None:
    columns = TelegramLinkEvent.__table__.columns

    assert isinstance(columns["action"].type, String)
    assert columns["action"].nullable is False
    assert (
        check_constraints(TelegramLinkEvent)["ck_telegram_link_events_action_allowed"]
        == "action IN ('linked', 'unlinked', 'relinked')"
    )


def test_telegram_timestamps_are_timezone_aware() -> None:
    timestamp_columns = {
        TelegramLink: ("linked_at", "unlinked_at", "updated_at"),
        TelegramLinkToken: (
            "created_at",
            "expires_at",
            "consumed_at",
            "invalidated_at",
        ),
        TelegramLinkEvent: ("occurred_at",),
    }

    for model, column_names in timestamp_columns.items():
        for column_name in column_names:
            column = model.__table__.columns[column_name]

            assert isinstance(column.type, DateTime)
            assert column.type.timezone is True


def test_telegram_models_do_not_add_forbidden_columns() -> None:
    forbidden_columns_by_model = {
        TelegramLink: {
            "token",
            "token_hash",
            "raw_token",
            "deep_link",
            "phone",
            "ip",
            "username",
            "update_json",
            "metadata",
            "customer_status",
            "onboarding_status",
        },
        TelegramLinkToken: {
            "telegram_chat_id",
            "old_chat_id",
            "new_chat_id",
            "token",
            "raw_token",
            "deep_link",
            "phone",
            "ip",
            "username",
            "update_json",
            "metadata",
            "purpose",
        },
        TelegramLinkEvent: {
            "telegram_chat_id",
            "old_chat_id",
            "new_chat_id",
            "token",
            "token_hash",
            "phone",
            "ip",
            "username",
            "update_json",
            "message",
            "metadata",
            "updated_at",
            "deleted_at",
        },
    }

    for model, forbidden_columns in forbidden_columns_by_model.items():
        column_names = set(model.__table__.columns.keys())

        assert forbidden_columns.isdisjoint(column_names)
