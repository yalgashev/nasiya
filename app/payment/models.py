"""PostgreSQL metadata for M14's immutable, append-only Payment ledger."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.payment.enums import PaymentMethod


class Payment(Base):
    """One immutable recorded payment; values are redacted in diagnostics."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "amount_uzs BETWEEN 1 AND 1000000000000",
            name="ck_payments_amount_uzs_bounds",
        ),
        CheckConstraint(
            "method IN "
            f"('{PaymentMethod.CASH.value}', '{PaymentMethod.CARD.value}', "
            f"'{PaymentMethod.TRANSFER.value}', '{PaymentMethod.OTHER.value}')",
            name="ck_payments_method_allowed",
        ),
        CheckConstraint(
            "debt_revision_after > 0",
            name="ck_payments_debt_revision_after_positive",
        ),
        UniqueConstraint(
            "debt_id",
            "debt_revision_after",
            name="uq_payments_debt_id_debt_revision_after",
        ),
        PrimaryKeyConstraint("id", name="pk_payments"),
    )

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    debt_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "debts.id",
            name="fk_payments_debt_id_debts_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    recorded_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_payments_recorded_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    amount_uzs: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    debt_revision_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return "Payment(<redacted>)"
