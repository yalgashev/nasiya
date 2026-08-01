from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.customer.repository import load_existing_own_customer_draft_for_update
from app.customer_identity.contracts import (
    CustomerIdentityRepository,
    EncryptedCustomerIdentityRecord,
    IdentityRevision,
    OwnCustomerDraft,
)
from app.customer_identity.crypto import (
    CustomerIdentityEnvelope,
    CustomerIdentityKeyId,
    JshshirBlindIndex,
)
from app.customer_identity.models import (
    CUSTOMER_IDENTITY_BLIND_INDEX_CONSTRAINT,
    CustomerIdentity,
)


class CustomerIdentityBlindIndexConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Customer identity conflicts with an existing identity")


class CustomerIdentityRevisionConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Customer identity revision changed")


class SqlAlchemyCustomerIdentityRepository(CustomerIdentityRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_own_customer_draft(
        self,
        *,
        actor_user_id: UUID,
    ) -> OwnCustomerDraft | None:
        customer = load_existing_own_customer_draft_for_update(
            self._session,
            actor_user_id=actor_user_id,
        )
        if customer is None:
            return None
        return OwnCustomerDraft(customer_id=customer.id)

    def get_identity(
        self,
        *,
        customer_id: UUID,
    ) -> EncryptedCustomerIdentityRecord | None:
        model = self._session.get(CustomerIdentity, customer_id)
        return None if model is None else _to_record(model)

    def lock_identity(
        self,
        *,
        customer_id: UUID,
    ) -> EncryptedCustomerIdentityRecord | None:
        statement = (
            select(CustomerIdentity)
            .where(CustomerIdentity.customer_id == customer_id)
            .with_for_update()
        )
        model = self._session.scalar(statement)
        return None if model is None else _to_record(model)

    def save_identity(
        self,
        *,
        record: EncryptedCustomerIdentityRecord,
        expected_revision: int,
    ) -> EncryptedCustomerIdentityRecord:
        _require_expected_revision(expected_revision)
        if not isinstance(record, EncryptedCustomerIdentityRecord):
            raise TypeError("Encrypted customer identity record is invalid")
        if record.revision.value != expected_revision + 1:
            raise ValueError("Saved identity revision must increment exactly once")

        statement = (
            select(CustomerIdentity)
            .where(CustomerIdentity.customer_id == record.customer_id)
            .with_for_update()
        )
        model = self._session.scalar(statement)
        if expected_revision == 0:
            if model is not None:
                raise CustomerIdentityRevisionConflict() from None
            model = CustomerIdentity(customer_id=record.customer_id)
        elif model is None or model.revision != expected_revision:
            raise CustomerIdentityRevisionConflict() from None

        try:
            with self._session.begin_nested():
                _apply_record(model, record)
                self._session.add(model)
                self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == CUSTOMER_IDENTITY_BLIND_INDEX_CONSTRAINT:
                raise CustomerIdentityBlindIndexConflict() from None
            raise
        return _to_record(model)


def _apply_record(
    model: CustomerIdentity,
    record: EncryptedCustomerIdentityRecord,
) -> None:
    model.ciphertext = record.envelope.ciphertext
    model.nonce = record.envelope.nonce
    model.key_id = record.envelope.key_id.as_persistence_value()
    model.schema_version = record.envelope.schema_version
    model.jshshir_blind_index = record.jshshir_blind_index.as_persistence_bytes()
    model.revision = record.revision.value


def _to_record(model: CustomerIdentity) -> EncryptedCustomerIdentityRecord:
    return EncryptedCustomerIdentityRecord(
        customer_id=model.customer_id,
        envelope=CustomerIdentityEnvelope(
            ciphertext=bytes(model.ciphertext),
            nonce=bytes(model.nonce),
            key_id=CustomerIdentityKeyId(model.key_id),
            schema_version=model.schema_version,
        ),
        jshshir_blind_index=JshshirBlindIndex(bytes(model.jshshir_blind_index)),
        revision=IdentityRevision(model.revision),
    )


def _require_expected_revision(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Expected identity revision must be non-negative")


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
