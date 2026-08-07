from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.audit.contracts import (
    AuditActorKind,
    AuditEventType,
    AuditObjectType,
)
from app.audit.models import AuditLog

_PERSISTED_AUDIT_EVENT_TYPES = frozenset(
    {
        AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
        AuditEventType.OFFER_VERSION_CREATED,
        AuditEventType.OFFER_TEXT_UPDATED,
        AuditEventType.OFFER_VERSION_APPROVED,
        AuditEventType.OFFER_VERSION_MADE_CURRENT,
        AuditEventType.OFFER_VERSION_DEMOTED,
        AuditEventType.OFFER_REGISTRATION_ACCEPTED,
        AuditEventType.CUSTOMER_IDENTITY_SAVED,
        AuditEventType.CUSTOMER_DOCUMENT_ATTACHED,
        AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED,
        AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED,
        AuditEventType.CUSTOMER_ACTIVATED,
        AuditEventType.SHOP_CUSTOMER_LINKED,
        AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED,
        AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED,
    }
)
_PERSISTED_AUDIT_OBJECT_TYPES = frozenset(
    {
        AuditObjectType.USER,
        AuditObjectType.OFFER_VERSION,
        AuditObjectType.OFFER_TEXT,
        AuditObjectType.OFFER_ACCEPTANCE,
        AuditObjectType.CUSTOMER_IDENTITY,
        AuditObjectType.CUSTOMER_DOCUMENT,
        AuditObjectType.CUSTOMER,
        AuditObjectType.SHOP_CUSTOMER,
        AuditObjectType.SHOP,
    }
)


def _checks() -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in AuditLog.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_audit_log_columns_match_cr_m9_01_exact_support_shape() -> None:
    table = AuditLog.__table__

    assert table.name == "audit_log"
    assert tuple(table.columns.keys()) == (
        "id",
        "occurred_at",
        "event_type",
        "actor_kind",
        "actor_user_id",
        "object_type",
        "object_id",
        "payload",
    )
    assert isinstance(table.c.id.type, PostgresUUID)
    assert table.c.occurred_at.type.timezone is True
    assert table.c.event_type.type.length == 64
    assert table.c.actor_kind.type.length == 8
    assert isinstance(table.c.actor_user_id.type, PostgresUUID)
    assert table.c.actor_user_id.nullable is True
    assert table.c.object_type.type.length == 32
    assert isinstance(table.c.object_id.type, PostgresUUID)
    assert isinstance(table.c.payload.type, JSONB)
    assert table.c.payload.nullable is False


def test_audit_actor_foreign_key_is_named_and_restrictive() -> None:
    foreign_key = next(iter(AuditLog.__table__.c.actor_user_id.foreign_keys))

    assert foreign_key.constraint.name == "fk_audit_log_actor_user_id_users_id"
    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "RESTRICT"


def test_audit_model_matches_current_m12_exact_shape_registry() -> None:
    checks = _checks()

    assert set(checks) == {
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_kind_allowed",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_actor_matches_event",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_payload_exact_shape",
    }
    event_sql = str(checks["ck_audit_log_event_type_allowed"].sqltext)
    assert all(event.value in event_sql for event in _PERSISTED_AUDIT_EVENT_TYPES)
    actor_sql = str(checks["ck_audit_log_actor_kind_allowed"].sqltext)
    assert all(kind.value in actor_sql for kind in AuditActorKind)
    object_sql = str(checks["ck_audit_log_object_type_allowed"].sqltext)
    assert all(
        object_type.value in object_sql for object_type in _PERSISTED_AUDIT_OBJECT_TYPES
    )
    object_mapping_sql = str(checks["ck_audit_log_object_matches_event"].sqltext)
    assert (
        "event_type = 'customer.activated' AND object_type = 'customer'"
        in object_mapping_sql
    )
    assert "object_type = 'shop_customer'" in object_mapping_sql
    assert "object_type = 'shop'" in object_mapping_sql
    payload_sql = str(checks["ck_audit_log_payload_exact_shape"].sqltext)
    assert "jsonb_typeof(payload) = 'object'" in payload_sql
    assert "payload ?& ARRAY" in payload_sql
    assert "payload - ARRAY" in payload_sql
    assert "= '{}'::jsonb" in payload_sql
    assert "bootstrap_method" in payload_sql
    assert "content_hash" in payload_sql
    assert (
        "event_type = 'customer.activated' "
        "AND payload ?& ARRAY['from_status', 'to_status', 'activation_method']"
        in payload_sql
    )
    assert "payload ->> 'from_status' = 'draft'" in payload_sql
    assert "payload ->> 'to_status' = 'active'" in payload_sql
    assert (
        "payload ->> 'activation_method' = 'TELEGRAM_REGISTRATION_OTP'" in payload_sql
    )
    assert "event_type = 'shop_customer.linked'" in payload_sql
    assert "event_type = 'shop_customer.policy_updated'" in payload_sql
    assert "event_type = 'shop.customer_defaults_updated'" in payload_sql


def test_audit_model_has_no_query_update_or_delete_application_api() -> None:
    assert not hasattr(AuditLog, "query")
    assert not hasattr(AuditLog, "update")
    assert not hasattr(AuditLog, "delete")


def test_audit_log_repr_redacts_actor_and_payload() -> None:
    actor_id = UUID("11111111-1111-4111-8111-111111111111")
    model = AuditLog(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        occurred_at=datetime(2026, 7, 31, 13, 0, tzinfo=UTC),
        event_type=AuditEventType.OFFER_VERSION_CREATED.value,
        actor_kind=AuditActorKind.USER.value,
        actor_user_id=actor_id,
        object_type=AuditObjectType.OFFER_VERSION.value,
        object_id=UUID("33333333-3333-4333-8333-333333333333"),
        payload={
            "purpose": "REGISTRATION",
            "title": "SECRET LEGAL TITLE",
        },
    )

    rendered = repr(model)

    assert str(actor_id) not in rendered
    assert "SECRET" not in rendered
    assert "actor_user_id=<redacted>" in rendered
    assert "payload=<redacted>" in rendered
