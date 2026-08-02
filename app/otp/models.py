from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('LOGIN', 'REGISTRATION')",
            name="ck_otp_challenges_purpose_allowed",
        ),
        CheckConstraint(
            "("
            "purpose = 'LOGIN' "
            "AND customer_id IS NULL "
            "AND registration_offer_acceptance_id IS NULL "
            "AND customer_identity_revision IS NULL "
            "AND customer_document_id IS NULL"
            ") OR ("
            "purpose = 'REGISTRATION' "
            "AND user_id IS NOT NULL "
            "AND telegram_link_id IS NOT NULL "
            "AND telegram_linked_at IS NOT NULL "
            "AND customer_id IS NOT NULL "
            "AND registration_offer_acceptance_id IS NOT NULL "
            "AND customer_identity_revision > 0 "
            "AND customer_document_id IS NOT NULL"
            ")",
            name="ck_otp_challenges_registration_context_matches_purpose",
        ),
        CheckConstraint(
            "browser_binding_digest ~ '^[0-9a-f]{64}$'",
            name="ck_otp_challenges_browser_binding_digest_hmac_sha256_hex",
        ),
        CheckConstraint(
            "code_mac IS NULL OR code_mac ~ '^[0-9a-f]{64}$'",
            name="ck_otp_challenges_code_mac_hmac_sha256_hex",
        ),
        CheckConstraint(
            "status IN ("
            "'PENDING_DISPATCH', 'ACTIVE', 'CONSUMED', 'SUPERSEDED', "
            "'EXPIRED', 'BURNED', 'INVALIDATED'"
            ")",
            name="ck_otp_challenges_status_allowed",
        ),
        CheckConstraint(
            "failed_attempts BETWEEN 0 AND 10",
            name="ck_otp_challenges_failed_attempts_cap",
        ),
        CheckConstraint(
            "("
            "user_id IS NULL "
            "AND telegram_link_id IS NULL "
            "AND telegram_linked_at IS NULL"
            ") OR ("
            "user_id IS NOT NULL "
            "AND telegram_link_id IS NOT NULL "
            "AND telegram_linked_at IS NOT NULL"
            ")",
            name="ck_otp_challenges_real_identity_consistent",
        ),
        CheckConstraint(
            "status != 'PENDING_DISPATCH' OR ("
            "code_mac IS NULL "
            "AND activated_at IS NULL "
            "AND expires_at IS NULL "
            "AND consumed_at IS NULL "
            "AND terminal_at IS NULL"
            ")",
            name="ck_otp_challenges_pending_dispatch_state",
        ),
        CheckConstraint(
            "status != 'ACTIVE' OR ("
            "user_id IS NOT NULL "
            "AND telegram_link_id IS NOT NULL "
            "AND telegram_linked_at IS NOT NULL "
            "AND code_mac IS NOT NULL "
            "AND activated_at IS NOT NULL "
            "AND expires_at IS NOT NULL "
            "AND expires_at > activated_at "
            "AND consumed_at IS NULL "
            "AND terminal_at IS NULL"
            ")",
            name="ck_otp_challenges_active_state",
        ),
        CheckConstraint(
            "("
            "status IN ('PENDING_DISPATCH', 'ACTIVE') "
            "AND terminal_at IS NULL "
            "AND consumed_at IS NULL"
            ") OR ("
            "status = 'CONSUMED' "
            "AND terminal_at IS NOT NULL "
            "AND consumed_at IS NOT NULL"
            ") OR ("
            "status IN ('SUPERSEDED', 'EXPIRED', 'BURNED', 'INVALIDATED') "
            "AND terminal_at IS NOT NULL "
            "AND consumed_at IS NULL"
            ")",
            name="ck_otp_challenges_terminal_state",
        ),
        CheckConstraint(
            "updated_at >= created_at "
            "AND (activated_at IS NULL OR activated_at >= created_at) "
            "AND (expires_at IS NULL OR activated_at IS NOT NULL) "
            "AND (consumed_at IS NULL OR activated_at IS NOT NULL) "
            "AND (terminal_at IS NULL OR terminal_at >= created_at)",
            name="ck_otp_challenges_timestamp_order",
        ),
        Index(
            "uq_otp_challenges_one_outstanding_per_user_purpose",
            "user_id",
            "purpose",
            unique=True,
            postgresql_where=sqlalchemy_text(
                "status IN ('PENDING_DISPATCH', 'ACTIVE') AND user_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_otp_challenges_one_outstanding_per_browser_purpose",
            "browser_binding_digest",
            "purpose",
            unique=True,
            postgresql_where=sqlalchemy_text(
                "status IN ('PENDING_DISPATCH', 'ACTIVE')"
            ),
        ),
        Index("ix_otp_challenges_terminal_at", "terminal_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_otp_challenges_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_link_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "telegram_links.id",
            name="fk_otp_challenges_telegram_link_id_telegram_links_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    telegram_linked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "customers.id",
            name="fk_otp_challenges_customer_id_customers_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    registration_offer_acceptance_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "offer_acceptances.id",
            name="fk_otp_challenges_registration_acceptance_offer_acceptances",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    customer_identity_revision: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    customer_document_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "customer_documents.id",
            name="fk_otp_challenges_customer_document_id_customer_documents",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    browser_binding_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_mac: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sqlalchemy_text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return (
            "OtpChallenge("
            "id=<redacted>, user_id=<redacted>, telegram_link_id=<redacted>, "
            "registration_context=<redacted>, browser_binding=<redacted>, "
            "code_mac=<redacted>, "
            f"purpose={self.purpose!r}, status={self.status!r}, "
            f"failed_attempts={self.failed_attempts!r})"
        )


class OtpDispatch(Base):
    __tablename__ = "otp_dispatches"
    __table_args__ = (
        UniqueConstraint(
            "challenge_id",
            name="uq_otp_dispatches_challenge_id",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PREPARED', 'SENT', 'FAILED', 'UNKNOWN', "
            "'CANCELLED')",
            name="ck_otp_dispatches_status_allowed",
        ),
        CheckConstraint(
            "locale IN ('uz-Latn', 'ru')",
            name="ck_otp_dispatches_locale_allowed",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_otp_dispatches_failure_code_format",
        ),
        CheckConstraint(
            "("
            "status = 'PENDING' "
            "AND prepared_at IS NULL "
            "AND sent_at IS NULL "
            "AND terminal_at IS NULL "
            "AND failure_code IS NULL"
            ") OR ("
            "status = 'PREPARED' "
            "AND claimed_at IS NOT NULL "
            "AND prepared_at IS NOT NULL "
            "AND sent_at IS NULL "
            "AND terminal_at IS NULL "
            "AND failure_code IS NULL"
            ") OR ("
            "status = 'SENT' "
            "AND prepared_at IS NOT NULL "
            "AND sent_at IS NOT NULL "
            "AND terminal_at IS NOT NULL "
            "AND failure_code IS NULL"
            ") OR ("
            "status IN ('FAILED', 'UNKNOWN') "
            "AND prepared_at IS NOT NULL "
            "AND sent_at IS NULL "
            "AND terminal_at IS NOT NULL "
            "AND failure_code IS NOT NULL"
            ") OR ("
            "status = 'CANCELLED' "
            "AND terminal_at IS NOT NULL "
            "AND sent_at IS NULL "
            "AND failure_code IS NULL"
            ")",
            name="ck_otp_dispatches_state_consistent",
        ),
        CheckConstraint(
            "updated_at >= created_at "
            "AND (claimed_at IS NULL OR claimed_at >= created_at) "
            "AND (prepared_at IS NULL OR claimed_at IS NOT NULL) "
            "AND (sent_at IS NULL OR prepared_at IS NOT NULL) "
            "AND (terminal_at IS NULL OR terminal_at >= created_at)",
            name="ck_otp_dispatches_timestamp_order",
        ),
        Index("ix_otp_dispatches_status_created_at", "status", "created_at"),
        Index("ix_otp_dispatches_terminal_at", "terminal_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    challenge_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "otp_challenges.id",
            name="fk_otp_dispatches_challenge_id_otp_challenges_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )


class OtpChallengeEvent(Base):
    __tablename__ = "otp_challenge_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ("
            "'ISSUED', 'DISPATCH_PREPARED', 'DISPATCH_RESULT', "
            "'VERIFY_FAILED', 'CONSUMED', 'SUPERSEDED', 'EXPIRED', 'BURNED', "
            "'INVALIDATED_BY_LINK_CHANGE', "
            "'INVALIDATED_BY_REGISTRATION_STATE_CHANGE'"
            ")",
            name="ck_otp_challenge_events_action_allowed",
        ),
        CheckConstraint(
            "safe_code IS NULL OR safe_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_otp_challenge_events_safe_code_format",
        ),
        Index(
            "ix_otp_challenge_events_challenge_id_occurred_at",
            "challenge_id",
            "occurred_at",
        ),
        Index("ix_otp_challenge_events_occurred_at", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    challenge_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_otp_challenge_events_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )
    safe_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OtpDispatcherState(Base):
    __tablename__ = "otp_dispatcher_state"
    __table_args__ = (
        CheckConstraint(
            "id = 1",
            name="ck_otp_dispatcher_state_singleton",
        ),
        CheckConstraint(
            "ready_at IS NULL OR heartbeat_at IS NOT NULL",
            name="ck_otp_dispatcher_state_ready_requires_heartbeat",
        ),
        CheckConstraint(
            "heartbeat_at IS NULL OR ready_at IS NULL OR heartbeat_at >= ready_at",
            name="ck_otp_dispatcher_state_heartbeat_not_before_ready",
        ),
    )

    id: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        default=1,
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )
