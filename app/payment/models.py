"""PostgreSQL metadata for M14's immutable, append-only Payment ledger."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.payment.enums import PaymentMethod, PaymentVoidReason


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
        UniqueConstraint(
            "id",
            "debt_id",
            "debt_revision_after",
            name="uq_payments_id_debt_id_debt_revision_after",
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


class PaymentVoid(Base):
    """One immutable correction fact chained to Payment and Debt revisions."""

    __tablename__ = "payment_voids"
    __table_args__ = (
        ForeignKeyConstraint(
            ("payment_id", "debt_id", "source_payment_revision"),
            ("payments.id", "payments.debt_id", "payments.debt_revision_after"),
            name="fk_payment_voids_payment_debt_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("debt_id", "shop_customer_id"),
            ("debts.id", "debts.shop_customer_id"),
            name="fk_payment_voids_debt_shop_customer",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("payment_id", name="uq_payment_voids_payment_id"),
        UniqueConstraint(
            "debt_id",
            "debt_revision_after",
            name="uq_payment_voids_debt_id_debt_revision_after",
        ),
        CheckConstraint(
            "reason IN "
            f"('{PaymentVoidReason.DUPLICATE_PAYMENT.value}', "
            f"'{PaymentVoidReason.INCORRECT_AMOUNT.value}', "
            f"'{PaymentVoidReason.INCORRECT_METHOD.value}', "
            f"'{PaymentVoidReason.PAYMENT_NOT_RECEIVED.value}', "
            f"'{PaymentVoidReason.WRONG_DEBT.value}')",
            name="ck_payment_voids_reason_allowed",
        ),
        CheckConstraint(
            "source_payment_revision > 0",
            name="ck_payment_voids_source_payment_revision_positive",
        ),
        CheckConstraint(
            "debt_revision_after > 0",
            name="ck_payment_voids_debt_revision_after_positive",
        ),
        CheckConstraint(
            "source_payment_revision < debt_revision_after",
            name="ck_payment_voids_revision_order",
        ),
        PrimaryKeyConstraint("id", name="pk_payment_voids"),
        Index(
            "ix_payment_voids_shop_customer_voided_at_id",
            "shop_customer_id",
            "voided_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    debt_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    shop_customer_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), nullable=False
    )
    source_payment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    debt_revision_after: Mapped[int] = mapped_column(Integer, nullable=False)
    voided_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_payment_voids_voided_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    voided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return "PaymentVoid(<redacted>)"
