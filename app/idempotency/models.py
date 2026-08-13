"""PostgreSQL metadata for durable, raw-key-free M13 replay state."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.models import utc_now
from app.db import Base
from app.idempotency.contracts import IdempotencyEndpoint, IdempotencyResultType


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id",
            "endpoint",
            "key_digest",
            name="uq_idempotency_keys_actor_user_id_endpoint_key_digest",
        ),
        CheckConstraint(
            (
                f"(endpoint = '{IdempotencyEndpoint.SHOP_DEBTS_CREATE.value}' "
                f"AND result_object_type = '{IdempotencyResultType.DEBT.value}') "
                "OR "
                f"(endpoint = "
                f"'{IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE.value}' "
                f"AND result_object_type = '{IdempotencyResultType.PAYMENT.value}') "
                "OR "
                f"(endpoint = "
                f"'{IdempotencyEndpoint.SHOP_RISK_BAND_DISCLOSURES_CREATE.value}' "
                f"AND result_object_type = "
                f"'{IdempotencyResultType.DISCLOSURE_VIEW.value}') OR "
                f"(endpoint = '{IdempotencyEndpoint.ADMIN_DEBTS_WRITE_OFF.value}' "
                f"AND result_object_type = '{IdempotencyResultType.DEBT.value}') OR "
                f"(endpoint = '{IdempotencyEndpoint.SHOP_PAYMENTS_VOID.value}' "
                f"AND result_object_type = '{IdempotencyResultType.PAYMENT.value}')"
            ),
            name="ck_idempotency_keys_endpoint_result_pair_allowed",
        ),
        CheckConstraint(
            "key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_idempotency_keys_key_digest_sha256_hex",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_idempotency_keys_request_hash_sha256_hex",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_idempotency_keys_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    key_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    result_object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    result_object_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return (
            "IdempotencyKey(id=<redacted>, actor_user_id=<redacted>, "
            "endpoint=<redacted>, key_digest=<redacted>, request_hash=<redacted>, "
            "result_object_type=<redacted>, result_object_id=<redacted>, "
            "created_at=<redacted>)"
        )
