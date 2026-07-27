from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.auth.models import Session as AuthSession
from app.db import Base
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent


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


def unique_constraints(model) -> dict[str, set[str]]:
    return {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_shop_tables_are_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["shops"] is Shop.__table__
    assert Base.metadata.tables["shop_staff"] is ShopStaff.__table__
    assert Base.metadata.tables["shop_status_events"] is ShopStatusEvent.__table__
    assert Base.metadata.tables["shop_staff_events"] is ShopStaffEvent.__table__


def test_shop_tables_have_exact_m5_columns() -> None:
    assert set(Shop.__table__.columns.keys()) == {
        "id",
        "name",
        "phone",
        "address_text",
        "status",
        "created_at",
        "updated_at",
    }
    assert set(ShopStaff.__table__.columns.keys()) == {
        "id",
        "shop_id",
        "user_id",
        "role",
        "is_active",
        "created_at",
        "updated_at",
        "revoked_at",
    }
    assert set(ShopStatusEvent.__table__.columns.keys()) == {
        "id",
        "shop_id",
        "action",
        "actor_user_id",
        "reason",
        "created_at",
    }
    assert set(ShopStaffEvent.__table__.columns.keys()) == {
        "id",
        "shop_id",
        "subject_user_id",
        "action",
        "old_role",
        "new_role",
        "actor_user_id",
        "created_at",
    }


def test_session_active_shop_id_exists_in_orm_metadata() -> None:
    columns = AuthSession.__table__.columns
    foreign_key = next(iter(columns["active_shop_id"].foreign_keys))

    assert "active_shop_id" in columns
    assert isinstance(columns["active_shop_id"].type, PostgresUUID)
    assert columns["active_shop_id"].nullable is True
    assert foreign_key.target_fullname == "shops.id"
    assert foreign_key.ondelete == "RESTRICT"
    assert foreign_key.constraint.name == "fk_sessions_active_shop_id_shops_id"


def test_shop_models_use_uuid_primary_keys() -> None:
    for model in (Shop, ShopStaff, ShopStatusEvent, ShopStaffEvent):
        id_column = model.__table__.columns["id"]

        assert isinstance(id_column.type, PostgresUUID)
        assert id_column.primary_key is True
        assert id_column.nullable is False


def test_shop_timestamps_are_timezone_aware() -> None:
    timestamp_columns = {
        Shop: ("created_at", "updated_at"),
        ShopStaff: ("created_at", "updated_at", "revoked_at"),
        ShopStatusEvent: ("created_at",),
        ShopStaffEvent: ("created_at",),
    }

    for model, column_names in timestamp_columns.items():
        for column_name in column_names:
            column = model.__table__.columns[column_name]

            assert isinstance(column.type, DateTime)
            assert column.type.timezone is True


def test_shop_nullable_contracts() -> None:
    expected_nullable = {
        Shop: {
            "id": False,
            "name": False,
            "phone": False,
            "address_text": True,
            "status": False,
            "created_at": False,
            "updated_at": False,
        },
        ShopStaff: {
            "id": False,
            "shop_id": False,
            "user_id": False,
            "role": False,
            "is_active": False,
            "created_at": False,
            "updated_at": False,
            "revoked_at": True,
        },
        ShopStatusEvent: {
            "id": False,
            "shop_id": False,
            "action": False,
            "actor_user_id": True,
            "reason": True,
            "created_at": False,
        },
        ShopStaffEvent: {
            "id": False,
            "shop_id": False,
            "subject_user_id": False,
            "action": False,
            "old_role": True,
            "new_role": True,
            "actor_user_id": True,
            "created_at": False,
        },
    }

    for model, nullable_by_column in expected_nullable.items():
        for column_name, expected in nullable_by_column.items():
            assert model.__table__.columns[column_name].nullable is expected


def test_shop_column_types_are_m5_contract_types() -> None:
    assert isinstance(Shop.__table__.columns["name"].type, Text)
    assert isinstance(Shop.__table__.columns["phone"].type, Text)
    assert isinstance(Shop.__table__.columns["address_text"].type, Text)
    assert isinstance(Shop.__table__.columns["status"].type, Text)
    assert isinstance(ShopStaff.__table__.columns["role"].type, Text)
    assert isinstance(ShopStaff.__table__.columns["is_active"].type, Boolean)
    assert isinstance(ShopStatusEvent.__table__.columns["action"].type, Text)
    assert isinstance(ShopStatusEvent.__table__.columns["reason"].type, Text)
    assert isinstance(ShopStaffEvent.__table__.columns["action"].type, Text)
    assert isinstance(ShopStaffEvent.__table__.columns["old_role"].type, Text)
    assert isinstance(ShopStaffEvent.__table__.columns["new_role"].type, Text)


def test_shop_staff_unique_constraint_is_shop_id_user_id_only() -> None:
    constraints = unique_constraints(ShopStaff)

    assert constraints["uq_shop_staff_shop_id_user_id"] == {"shop_id", "user_id"}
    assert {"user_id"} not in constraints.values()


def test_m5_foreign_keys_restrict_parent_delete() -> None:
    foreign_key_columns = (
        (AuthSession, "active_shop_id", "shops.id", True),
        (ShopStaff, "shop_id", "shops.id", False),
        (ShopStaff, "user_id", "users.id", False),
        (ShopStatusEvent, "shop_id", "shops.id", False),
        (ShopStatusEvent, "actor_user_id", "users.id", True),
        (ShopStaffEvent, "shop_id", "shops.id", False),
        (ShopStaffEvent, "subject_user_id", "users.id", False),
        (ShopStaffEvent, "actor_user_id", "users.id", True),
    )

    for model, column_name, target_fullname, expected_nullable in foreign_key_columns:
        column = model.__table__.columns[column_name]
        foreign_key = next(iter(column.foreign_keys))

        assert isinstance(column.type, PostgresUUID)
        assert column.nullable is expected_nullable
        assert foreign_key.target_fullname == target_fullname
        assert foreign_key.ondelete == "RESTRICT"


def test_shop_indexes_are_present() -> None:
    assert Shop.__table__.columns["phone"].index is True
    assert ShopStaff.__table__.columns["shop_id"].index is True
    assert ShopStaff.__table__.columns["user_id"].index is True

    status_event_index = indexes(ShopStatusEvent)[
        "ix_shop_status_events_shop_id_created_at"
    ]
    staff_event_index = indexes(ShopStaffEvent)[
        "ix_shop_staff_events_shop_id_created_at"
    ]

    assert {column.name for column in status_event_index.columns} == {
        "shop_id",
        "created_at",
    }
    assert {column.name for column in staff_event_index.columns} == {
        "shop_id",
        "created_at",
    }


def test_shop_check_constraints_are_present() -> None:
    constraints = check_constraints(Shop)

    assert constraints["ck_shops_status_allowed"] == (
        "status IN ('active', 'suspended')"
    )
    assert constraints["ck_shops_name_trimmed_length"] == (
        "char_length(btrim(name)) BETWEEN 2 AND 120"
    )
    assert constraints["ck_shops_phone_not_blank"] == "length(btrim(phone)) > 0"


def test_shop_staff_check_constraints_are_present() -> None:
    constraints = check_constraints(ShopStaff)

    assert constraints["ck_shop_staff_role_allowed"] == (
        "role IN ('owner', 'manager', 'cashier')"
    )
    assert constraints["ck_shop_staff_active_revoked_consistent"] == (
        "(is_active = true AND revoked_at IS NULL) "
        "OR (is_active = false AND revoked_at IS NOT NULL)"
    )


def test_shop_status_event_semantic_checks_are_present() -> None:
    constraints = check_constraints(ShopStatusEvent)

    assert constraints["ck_shop_status_events_action_allowed"] == (
        "action IN ('activated', 'suspended', 'reactivated')"
    )
    assert constraints["ck_shop_status_events_reason_matches_action"] == (
        "(action = 'activated' AND reason IS NULL) "
        "OR (action IN ('suspended', 'reactivated') "
        "AND reason IS NOT NULL AND length(btrim(reason)) > 0)"
    )


def test_shop_staff_event_semantic_checks_are_present() -> None:
    constraints = check_constraints(ShopStaffEvent)

    assert constraints["ck_shop_staff_events_action_allowed"] == (
        "action IN ('added', 'role_changed', 'revoked')"
    )
    assert constraints["ck_shop_staff_events_old_role_allowed"] == (
        "old_role IS NULL OR old_role IN ('owner', 'manager', 'cashier')"
    )
    assert constraints["ck_shop_staff_events_new_role_allowed"] == (
        "new_role IS NULL OR new_role IN ('owner', 'manager', 'cashier')"
    )
    assert constraints["ck_shop_staff_events_role_transition_matches_action"] == (
        "(action = 'added' AND old_role IS NULL AND new_role IS NOT NULL) "
        "OR (action = 'role_changed' AND old_role IS NOT NULL "
        "AND new_role IS NOT NULL AND old_role <> new_role) "
        "OR (action = 'revoked' AND old_role IS NOT NULL AND new_role IS NULL)"
    )


def test_shops_table_has_no_owner_pending_or_settings_columns() -> None:
    forbidden_columns = {
        "owner_id",
        "created_by_user_id",
        "pending",
        "settings",
        "credit_limit",
        "discount_percent",
        "announcement_limit",
    }

    assert forbidden_columns.isdisjoint(Shop.__table__.columns.keys())


def test_event_tables_have_no_json_or_payload_columns() -> None:
    forbidden_columns = {"json", "payload", "metadata", "data"}

    for model in (ShopStatusEvent, ShopStaffEvent):
        column_names = {column.name.casefold() for column in model.__table__.columns}

        assert forbidden_columns.isdisjoint(column_names)
        for column in model.__table__.columns:
            assert not isinstance(column.type, (JSON, JSONB))
