from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.models import utc_now
from app.db import Base
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus


class OfferVersion(Base):
    __tablename__ = "offer_versions"
    __table_args__ = (
        UniqueConstraint(
            "purpose",
            "version_number",
            name="uq_offer_versions_purpose_version_number",
        ),
        CheckConstraint(
            (
                "purpose IN "
                f"('{OfferPurpose.REGISTRATION.value}', "
                f"'{OfferPurpose.DEBT_ACCEPTANCE.value}')"
            ),
            name="ck_offer_versions_purpose_allowed",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_offer_versions_version_number_positive",
        ),
        CheckConstraint(
            (
                "status IN "
                f"('{OfferStatus.DRAFT.value}', "
                f"'{OfferStatus.APPROVED.value}', "
                f"'{OfferStatus.CURRENT.value}')"
            ),
            name="ck_offer_versions_status_allowed",
        ),
        CheckConstraint(
            (
                f"(status = '{OfferStatus.DRAFT.value}' "
                "AND legal_review_authority IS NULL "
                "AND legal_reviewed_at IS NULL "
                "AND legal_review_reference IS NULL "
                "AND approved_by_user_id IS NULL "
                "AND approved_at IS NULL) "
                f"OR (status IN ('{OfferStatus.APPROVED.value}', "
                f"'{OfferStatus.CURRENT.value}') "
                "AND legal_review_authority IS NOT NULL "
                "AND legal_reviewed_at IS NOT NULL "
                "AND legal_review_reference IS NOT NULL "
                "AND approved_by_user_id IS NOT NULL "
                "AND approved_at IS NOT NULL)"
            ),
            name="ck_offer_versions_approval_evidence_matches_status",
        ),
        CheckConstraint(
            (
                f"(status = '{OfferStatus.DRAFT.value}' "
                "AND current_by_user_id IS NULL "
                "AND current_at IS NULL) "
                f"OR (status = '{OfferStatus.APPROVED.value}' "
                "AND ((current_by_user_id IS NULL AND current_at IS NULL) "
                "OR (current_by_user_id IS NOT NULL "
                "AND current_at IS NOT NULL))) "
                f"OR (status = '{OfferStatus.CURRENT.value}' "
                "AND current_by_user_id IS NOT NULL "
                "AND current_at IS NOT NULL)"
            ),
            name="ck_offer_versions_current_metadata_matches_status",
        ),
        CheckConstraint(
            (
                "legal_review_authority IS NULL "
                "OR (char_length(legal_review_authority) BETWEEN 1 AND 200 "
                "AND legal_review_authority = btrim(legal_review_authority) "
                "AND legal_review_authority !~ '[[:cntrl:]]')"
            ),
            name="ck_offer_versions_legal_review_authority_valid",
        ),
        CheckConstraint(
            (
                "legal_review_reference IS NULL "
                "OR legal_review_reference "
                "~ '^[A-Za-z0-9][A-Za-z0-9._ -]{0,199}$'"
            ),
            name="ck_offer_versions_legal_review_reference_valid",
        ),
        CheckConstraint(
            (
                "(approved_at IS NULL OR approved_at >= created_at) "
                "AND (legal_reviewed_at IS NULL "
                "OR legal_reviewed_at <= approved_at) "
                "AND (current_at IS NULL OR current_at >= approved_at)"
            ),
            name="ck_offer_versions_timestamp_order",
        ),
        Index(
            "uq_offer_versions_current_purpose",
            "purpose",
            unique=True,
            postgresql_where=sqlalchemy_text(f"status = '{OfferStatus.CURRENT.value}'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=OfferStatus.DRAFT.value,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_offer_versions_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    legal_review_authority: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    legal_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    legal_review_reference: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_offer_versions_approved_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    current_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_offer_versions_current_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    current_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            "OfferVersion("
            f"id={'<set>' if self.id is not None else '<unset>'}, "
            f"purpose={self.purpose!r}, "
            f"version_number={self.version_number!r}, "
            f"status={self.status!r}, "
            "legal_review=<redacted>)"
        )


class OfferText(Base):
    __tablename__ = "offer_texts"
    __table_args__ = (
        UniqueConstraint(
            "offer_version_id",
            "language",
            name="uq_offer_texts_offer_version_id_language",
        ),
        CheckConstraint(
            (
                "language IN "
                f"('{OfferLanguage.UZ_LATN.value}', "
                f"'{OfferLanguage.UZ_CYRL.value}', "
                f"'{OfferLanguage.RU.value}')"
            ),
            name="ck_offer_texts_language_allowed",
        ),
        CheckConstraint(
            (
                "length(btrim(title)) > 0 "
                "AND length(btrim(body)) > 0 "
                "AND position(chr(13) in title) = 0 "
                "AND position(chr(13) in body) = 0"
            ),
            name="ck_offer_texts_content_canonical",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_offer_texts_content_hash_sha256_hex",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_offer_texts_timestamp_order",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    offer_version_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "offer_versions.id",
            name="fk_offer_texts_offer_version_id_offer_versions_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        return (
            "OfferText("
            f"id={'<set>' if self.id is not None else '<unset>'}, "
            f"offer_version_id={self.offer_version_id!r}, "
            f"language={self.language!r}, "
            f"content_hash={self.content_hash!r}, "
            "title=<redacted>, body=<redacted>)"
        )


class OfferAcceptance(Base):
    __tablename__ = "offer_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "offer_text_id",
            "purpose",
            name="uq_offer_acceptances_user_id_offer_text_id_purpose",
        ),
        CheckConstraint(
            (
                "purpose IN "
                f"('{OfferPurpose.REGISTRATION.value}', "
                f"'{OfferPurpose.DEBT_ACCEPTANCE.value}')"
            ),
            name="ck_offer_acceptances_purpose_allowed",
        ),
        CheckConstraint(
            (
                "language IN "
                f"('{OfferLanguage.UZ_LATN.value}', "
                f"'{OfferLanguage.UZ_CYRL.value}', "
                f"'{OfferLanguage.RU.value}')"
            ),
            name="ck_offer_acceptances_language_allowed",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_offer_acceptances_version_number_positive",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_offer_acceptances_content_hash_sha256_hex",
        ),
        CheckConstraint(
            (
                "user_agent IS NULL "
                "OR (char_length(user_agent) BETWEEN 1 AND 512 "
                "AND user_agent = btrim(user_agent) "
                "AND user_agent !~ '[[:cntrl:]]' "
                "AND user_agent !~ '  +')"
            ),
            name="ck_offer_acceptances_user_agent_normalized",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_offer_acceptances_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    offer_version_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "offer_versions.id",
            name="fk_offer_acceptances_offer_version_id_offer_versions_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    offer_text_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "offer_texts.id",
            name="fk_offer_acceptances_offer_text_id_offer_texts_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            "OfferAcceptance("
            f"id={'<set>' if self.id is not None else '<unset>'}, "
            f"offer_version_id={self.offer_version_id!r}, "
            f"offer_text_id={self.offer_text_id!r}, "
            f"purpose={self.purpose!r}, "
            f"language={self.language!r}, "
            f"version_number={self.version_number!r}, "
            f"content_hash={self.content_hash!r}, "
            f"accepted_at={self.accepted_at!r}, "
            "user_id=<redacted>, user_agent=<redacted>)"
        )
