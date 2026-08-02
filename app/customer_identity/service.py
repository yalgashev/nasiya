from __future__ import annotations

import hmac
from datetime import UTC, datetime
from uuid import UUID

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    AuditWriter,
)
from app.auth.error_codes import ErrorCode
from app.customer_identity.canonicalization import canonicalize_customer_identity
from app.customer_identity.contracts import (
    CustomerIdentityActor,
    CustomerIdentityMaskedSummary,
    CustomerIdentityRepository,
    DecryptedCustomerIdentity,
    EncryptedCustomerIdentityRecord,
    IdentityRevision,
    SaveCustomerIdentity,
)
from app.customer_identity.crypto import (
    CustomerIdentityCryptoConfig,
    CustomerIdentityCryptoError,
    compute_jshshir_blind_index,
    decrypt_customer_identity,
    encrypt_customer_identity,
)
from app.customer_identity.repository import (
    CustomerIdentityBlindIndexConflict,
    CustomerIdentityRevisionConflict,
)


class CustomerIdentityServiceError(RuntimeError):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"CustomerIdentityServiceError(code={self.code.value!r})"


def resolve_customer_identity_actor(
    authenticated_user: object,
) -> CustomerIdentityActor:
    user_id = getattr(authenticated_user, "id", None)
    is_active = getattr(authenticated_user, "is_active", None)
    if not isinstance(user_id, UUID) or is_active is not True:
        raise CustomerIdentityServiceError(ErrorCode.UNAUTHORIZED) from None
    return CustomerIdentityActor(user_id=user_id)


def get_own_customer_identity_view(
    *,
    repository: CustomerIdentityRepository,
    crypto_config: CustomerIdentityCryptoConfig,
    actor: CustomerIdentityActor,
) -> CustomerIdentityMaskedSummary | None:
    _require_service_dependencies(repository, crypto_config, actor)
    own_customer = repository.lock_own_customer_draft(actor_user_id=actor.user_id)
    if own_customer is None:
        raise CustomerIdentityServiceError(ErrorCode.CUSTOMER_DRAFT_REQUIRED) from None
    record = repository.get_identity(customer_id=own_customer.customer_id)
    if record is None:
        return None
    return _decrypt_verified_summary(record=record, crypto_config=crypto_config)


def save_own_customer_identity(
    *,
    repository: CustomerIdentityRepository,
    audit_writer: AuditWriter,
    crypto_config: CustomerIdentityCryptoConfig,
    command: SaveCustomerIdentity,
    now: datetime,
) -> CustomerIdentityMaskedSummary:
    if not isinstance(command, SaveCustomerIdentity):
        raise TypeError("Save customer identity command is invalid")
    _require_service_dependencies(repository, crypto_config, command.actor)
    if not isinstance(audit_writer, AuditWriter):
        raise TypeError("Audit writer is invalid")
    occurred_at = _as_utc(now)

    own_customer = repository.lock_own_customer_draft(
        actor_user_id=command.actor.user_id
    )
    if own_customer is None:
        raise CustomerIdentityServiceError(ErrorCode.CUSTOMER_DRAFT_REQUIRED) from None
    current = repository.lock_identity(customer_id=own_customer.customer_id)
    current_revision = 0 if current is None else current.revision.value
    if current_revision != command.expected_revision:
        raise CustomerIdentityServiceError(
            ErrorCode.CUSTOMER_IDENTITY_CHANGED
        ) from None

    try:
        identity = canonicalize_customer_identity(
            first_name=command.first_name,
            last_name=command.last_name,
            middle_name=command.middle_name,
            jshshir=command.jshshir,
            document_type=command.document_type,
            document_number=command.document_number,
        )
    except (TypeError, ValueError):
        raise CustomerIdentityServiceError(ErrorCode.VALIDATION_ERROR) from None

    try:
        blind_index = compute_jshshir_blind_index(
            identity.jshshir,
            blind_index_key=crypto_config.get_blind_index_key(),
        )
        envelope = encrypt_customer_identity(
            identity,
            customer_id=own_customer.customer_id,
            crypto_config=crypto_config,
        )
    except (CustomerIdentityCryptoError, TypeError, ValueError):
        raise CustomerIdentityServiceError(
            ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE
        ) from None
    next_revision = IdentityRevision(current_revision + 1)
    record = EncryptedCustomerIdentityRecord(
        customer_id=own_customer.customer_id,
        envelope=envelope,
        jshshir_blind_index=blind_index,
        revision=next_revision,
    )
    try:
        stored = repository.save_identity(
            record=record,
            expected_revision=current_revision,
        )
    except CustomerIdentityBlindIndexConflict:
        raise CustomerIdentityServiceError(ErrorCode.DUPLICATE_JSHSHIR) from None
    except CustomerIdentityRevisionConflict:
        raise CustomerIdentityServiceError(
            ErrorCode.CUSTOMER_IDENTITY_CHANGED
        ) from None

    audit_writer.append(
        event=AuditEvent(
            event_type=AuditEventType.CUSTOMER_IDENTITY_SAVED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=command.actor.user_id,
            object_type=AuditObjectType.CUSTOMER_IDENTITY,
            object_id=own_customer.customer_id,
            occurred_at=occurred_at,
            candidate_metadata={
                "revision": stored.revision.value,
                "created_or_updated": (
                    "created" if current_revision == 0 else "updated"
                ),
                "document_type": identity.document_type,
            },
        )
    )
    return DecryptedCustomerIdentity(
        customer_id=own_customer.customer_id,
        identity=identity,
        revision=stored.revision,
    ).to_safe_masked_summary()


class CustomerIdentityCompletenessService:
    def __init__(
        self,
        *,
        repository: CustomerIdentityRepository,
        crypto_config: CustomerIdentityCryptoConfig,
    ) -> None:
        if not isinstance(repository, CustomerIdentityRepository):
            raise TypeError("Customer identity repository is invalid")
        if not isinstance(crypto_config, CustomerIdentityCryptoConfig):
            raise TypeError("Customer identity crypto config is invalid")
        self._repository = repository
        self._crypto_config = crypto_config

    def __call__(self, *, customer_id: UUID) -> bool:
        return self.resolve_revision(customer_id=customer_id) is not None

    def resolve_revision(
        self,
        *,
        customer_id: UUID,
        lock_for_update: bool = False,
    ) -> IdentityRevision | None:
        if not isinstance(customer_id, UUID):
            return None
        record = (
            self._repository.lock_identity(customer_id=customer_id)
            if lock_for_update
            else self._repository.get_identity(customer_id=customer_id)
        )
        if record is None:
            return None
        try:
            _decrypt_verified_summary(
                record=record,
                crypto_config=self._crypto_config,
            )
        except CustomerIdentityServiceError:
            return None
        return record.revision


def _decrypt_verified_summary(
    *,
    record: EncryptedCustomerIdentityRecord,
    crypto_config: CustomerIdentityCryptoConfig,
) -> CustomerIdentityMaskedSummary:
    try:
        identity = decrypt_customer_identity(
            record.envelope,
            customer_id=record.customer_id,
            crypto_config=crypto_config,
        )
        recomputed = compute_jshshir_blind_index(
            identity.jshshir,
            blind_index_key=crypto_config.get_blind_index_key(),
        )
        if not hmac.compare_digest(
            recomputed.as_persistence_bytes(),
            record.jshshir_blind_index.as_persistence_bytes(),
        ):
            raise CustomerIdentityCryptoError()
        return DecryptedCustomerIdentity(
            customer_id=record.customer_id,
            identity=identity,
            revision=record.revision,
        ).to_safe_masked_summary()
    except (CustomerIdentityCryptoError, TypeError, ValueError):
        raise CustomerIdentityServiceError(
            ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE
        ) from None


def _require_service_dependencies(
    repository: CustomerIdentityRepository,
    crypto_config: CustomerIdentityCryptoConfig,
    actor: CustomerIdentityActor,
) -> None:
    if not isinstance(repository, CustomerIdentityRepository):
        raise TypeError("Customer identity repository is invalid")
    if not isinstance(crypto_config, CustomerIdentityCryptoConfig):
        raise TypeError("Customer identity crypto config is invalid")
    if not isinstance(actor, CustomerIdentityActor):
        raise TypeError("Customer identity actor is invalid")


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Customer identity service time must be timezone-aware")
    return value.astimezone(UTC)
