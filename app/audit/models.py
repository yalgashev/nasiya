from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.audit.contracts import (
    AuditActorKind,
    AuditEventType,
    AuditObjectType,
)
from app.auth.models import utc_now
from app.db import Base


def _exact_payload_clause(
    event_type: AuditEventType,
    keys: tuple[str, ...],
    *,
    extra_predicate: str | None = None,
) -> str:
    key_array = ", ".join(f"'{key}'" for key in keys)
    clause = (
        f"(event_type = '{event_type.value}' "
        f"AND payload ?& ARRAY[{key_array}] "
        f"AND payload - ARRAY[{key_array}] = '{{}}'::jsonb"
    )
    if extra_predicate is not None:
        clause += f" AND {extra_predicate}"
    return clause + ")"


_AUDIT_PAYLOAD_EXACT_SHAPE_SQL = (
    "jsonb_typeof(payload) = 'object' AND ("
    + " OR ".join(
        (
            _exact_payload_clause(
                AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
                ("bootstrap_method",),
                extra_predicate=("payload ->> 'bootstrap_method' = 'operator_cli'"),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_VERSION_CREATED,
                ("purpose", "version_number", "status"),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_TEXT_UPDATED,
                ("purpose", "version_number", "language", "content_hash"),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_VERSION_APPROVED,
                (
                    "purpose",
                    "version_number",
                    "from_status",
                    "to_status",
                    "legal_review_authority",
                    "legal_review_reference",
                    "legal_reviewed_at",
                ),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_VERSION_MADE_CURRENT,
                (
                    "purpose",
                    "version_number",
                    "from_status",
                    "to_status",
                    "previous_current_version_id",
                ),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_VERSION_DEMOTED,
                (
                    "purpose",
                    "version_number",
                    "from_status",
                    "to_status",
                    "replacement_version_id",
                ),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_REGISTRATION_ACCEPTED,
                (
                    "purpose",
                    "offer_version_id",
                    "offer_text_id",
                    "version_number",
                    "language",
                    "content_hash",
                ),
            ),
        )
    )
    + ")"
)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            (
                "event_type IN ("
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}', "
                f"'{AuditEventType.OFFER_VERSION_CREATED.value}', "
                f"'{AuditEventType.OFFER_TEXT_UPDATED.value}', "
                f"'{AuditEventType.OFFER_VERSION_APPROVED.value}', "
                f"'{AuditEventType.OFFER_VERSION_MADE_CURRENT.value}', "
                f"'{AuditEventType.OFFER_VERSION_DEMOTED.value}', "
                f"'{AuditEventType.OFFER_REGISTRATION_ACCEPTED.value}'"
                ")"
            ),
            name="ck_audit_log_event_type_allowed",
        ),
        CheckConstraint(
            (
                "actor_kind IN "
                f"('{AuditActorKind.USER.value}', "
                f"'{AuditActorKind.SYSTEM.value}')"
            ),
            name="ck_audit_log_actor_kind_allowed",
        ),
        CheckConstraint(
            (
                "object_type IN ("
                f"'{AuditObjectType.USER.value}', "
                f"'{AuditObjectType.OFFER_VERSION.value}', "
                f"'{AuditObjectType.OFFER_TEXT.value}', "
                f"'{AuditObjectType.OFFER_ACCEPTANCE.value}'"
                ")"
            ),
            name="ck_audit_log_object_type_allowed",
        ),
        CheckConstraint(
            (
                f"(event_type = "
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}' "
                f"AND actor_kind = '{AuditActorKind.SYSTEM.value}' "
                "AND actor_user_id IS NULL) "
                f"OR (event_type <> "
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}' "
                f"AND actor_kind = '{AuditActorKind.USER.value}' "
                "AND actor_user_id IS NOT NULL)"
            ),
            name="ck_audit_log_actor_matches_event",
        ),
        CheckConstraint(
            (
                f"(event_type = "
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}' "
                f"AND object_type = '{AuditObjectType.USER.value}') "
                f"OR (event_type IN ("
                f"'{AuditEventType.OFFER_VERSION_CREATED.value}', "
                f"'{AuditEventType.OFFER_VERSION_APPROVED.value}', "
                f"'{AuditEventType.OFFER_VERSION_MADE_CURRENT.value}', "
                f"'{AuditEventType.OFFER_VERSION_DEMOTED.value}') "
                f"AND object_type = '{AuditObjectType.OFFER_VERSION.value}') "
                f"OR (event_type = '{AuditEventType.OFFER_TEXT_UPDATED.value}' "
                f"AND object_type = '{AuditObjectType.OFFER_TEXT.value}') "
                f"OR (event_type = "
                f"'{AuditEventType.OFFER_REGISTRATION_ACCEPTED.value}' "
                f"AND object_type = '{AuditObjectType.OFFER_ACCEPTANCE.value}')"
            ),
            name="ck_audit_log_object_matches_event",
        ),
        CheckConstraint(
            _AUDIT_PAYLOAD_EXACT_SHAPE_SQL,
            name="ck_audit_log_payload_exact_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_audit_log_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            "AuditLog("
            f"id={'<set>' if self.id is not None else '<unset>'}, "
            f"occurred_at={self.occurred_at!r}, "
            f"event_type={self.event_type!r}, "
            f"actor_kind={self.actor_kind!r}, "
            f"object_type={self.object_type!r}, "
            f"object_id={self.object_id!r}, "
            "actor_user_id=<redacted>, payload=<redacted>)"
        )
