import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest

import app.customer_identity.service as identity_service_module
from app.audit.contracts import AuditEvent
from app.auth.error_codes import ErrorCode
from app.customer_identity.canonicalization import canonicalize_customer_identity
from app.customer_identity.contracts import (
    CustomerIdentityActor,
    CustomerIdentityRepository,
    EncryptedCustomerIdentityRecord,
    IdentityRevision,
    OwnCustomerDraft,
    SaveCustomerIdentity,
)
from app.customer_identity.crypto import (
    CustomerIdentityAesKey,
    CustomerIdentityBlindIndexKey,
    CustomerIdentityCryptoConfig,
    CustomerIdentityEnvelope,
    CustomerIdentityKeyId,
    JshshirBlindIndex,
    compute_jshshir_blind_index,
    encrypt_customer_identity,
)
from app.customer_identity.repository import (
    CustomerIdentityBlindIndexConflict,
    CustomerIdentityRevisionConflict,
)
from app.customer_identity.service import (
    CustomerIdentityCompletenessService,
    CustomerIdentityServiceError,
    get_own_customer_identity_view,
    resolve_customer_identity_actor,
    save_own_customer_identity,
)

CUSTOMER_ID = UUID("11111111-1111-1111-1111-111111111111")
ACTOR_ID = UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)


class FakeIdentityRepository:
    def __init__(self, record: EncryptedCustomerIdentityRecord | None = None) -> None:
        self.record = record
        self.own_customer = OwnCustomerDraft(CUSTOMER_ID)
        self.save_failure: RuntimeError | None = None

    def lock_own_customer_draft(
        self,
        *,
        actor_user_id: UUID,
    ) -> OwnCustomerDraft | None:
        return self.own_customer if actor_user_id == ACTOR_ID else None

    def get_identity(
        self,
        *,
        customer_id: UUID,
    ) -> EncryptedCustomerIdentityRecord | None:
        return self.record if customer_id == CUSTOMER_ID else None

    def lock_identity(
        self,
        *,
        customer_id: UUID,
    ) -> EncryptedCustomerIdentityRecord | None:
        return self.get_identity(customer_id=customer_id)

    def save_identity(
        self,
        *,
        record: EncryptedCustomerIdentityRecord,
        expected_revision: int,
    ) -> EncryptedCustomerIdentityRecord:
        if self.save_failure is not None:
            raise self.save_failure
        current_revision = 0 if self.record is None else self.record.revision.value
        if current_revision != expected_revision:
            raise CustomerIdentityRevisionConflict()
        self.record = record
        return record


class CapturingAuditWriter:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, *, event: AuditEvent) -> None:
        self.events.append(event)


def _config() -> CustomerIdentityCryptoConfig:
    key_id = CustomerIdentityKeyId("identity-v1")
    return CustomerIdentityCryptoConfig(
        active_key_id=key_id,
        encryption_keys={key_id: CustomerIdentityAesKey.from_bytes(bytes(range(32)))},
        blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(
            bytes(reversed(range(32)))
        ),
    )


def _command(*, expected_revision: int = 0) -> SaveCustomerIdentity:
    return SaveCustomerIdentity(
        actor=CustomerIdentityActor(ACTOR_ID),
        expected_revision=expected_revision,
        first_name="Synthetic",
        last_name="Specimen",
        middle_name=None,
        jshshir="12345678901234",
        document_type="PASSPORT",
        document_number="AB 12345",
    )


def _record() -> EncryptedCustomerIdentityRecord:
    config = _config()
    identity = canonicalize_customer_identity(
        first_name="Synthetic",
        last_name="Specimen",
        middle_name=None,
        jshshir="12345678901234",
        document_type="PASSPORT",
        document_number="AB 12345",
    )
    return EncryptedCustomerIdentityRecord(
        customer_id=CUSTOMER_ID,
        envelope=encrypt_customer_identity(
            identity,
            customer_id=CUSTOMER_ID,
            crypto_config=config,
        ),
        jshshir_blind_index=compute_jshshir_blind_index(
            identity.jshshir,
            blind_index_key=config.get_blind_index_key(),
        ),
        revision=IdentityRevision(1),
    )


def test_actor_and_save_command_are_narrow_active_and_redacted() -> None:
    class User:
        id = ACTOR_ID
        is_active = True
        is_platform_admin = True

    actor = resolve_customer_identity_actor(User())
    command = _command()
    rendered = f"{actor!r} {command!r}"

    assert actor.user_id == ACTOR_ID
    assert str(ACTOR_ID) not in rendered
    for plaintext in (
        command.first_name,
        command.last_name,
        command.jshshir,
        command.document_number,
    ):
        assert plaintext not in rendered
    assert not hasattr(command, "customer_id")
    assert not hasattr(command, "key_id")
    assert not hasattr(command, "ciphertext")
    assert not hasattr(command, "blind_index")

    User.is_active = False
    with pytest.raises(CustomerIdentityServiceError) as caught:
        resolve_customer_identity_actor(User())
    assert caught.value.code is ErrorCode.UNAUTHORIZED


def test_save_create_and_update_return_masked_summary_and_exact_audit() -> None:
    repository = FakeIdentityRepository()
    audit = CapturingAuditWriter()

    created = save_own_customer_identity(
        repository=repository,
        audit_writer=audit,
        crypto_config=_config(),
        command=_command(),
        now=NOW,
    )
    updated = save_own_customer_identity(
        repository=repository,
        audit_writer=audit,
        crypto_config=_config(),
        command=_command(expected_revision=1),
        now=NOW,
    )

    assert created.masked_jshshir == "**********1234"
    assert created.masked_document_number == "****2345"
    assert created.revision == IdentityRevision(1)
    assert updated.revision == IdentityRevision(2)
    assert [event.candidate_metadata for event in audit.events] == [
        {
            "revision": 1,
            "created_or_updated": "created",
            "document_type": created.document_type,
        },
        {
            "revision": 2,
            "created_or_updated": "updated",
            "document_type": updated.document_type,
        },
    ]
    assert all(
        set(event.candidate_metadata)
        == {"revision", "created_or_updated", "document_type"}
        for event in audit.events
    )


def test_missing_draft_stale_duplicate_and_validation_map_to_safe_codes() -> None:
    cases = []
    missing = FakeIdentityRepository()
    missing.own_customer = None
    cases.append((missing, _command(), ErrorCode.CUSTOMER_DRAFT_REQUIRED))
    stale = FakeIdentityRepository(_record())
    cases.append((stale, _command(), ErrorCode.CUSTOMER_IDENTITY_CHANGED))
    duplicate = FakeIdentityRepository()
    duplicate.save_failure = CustomerIdentityBlindIndexConflict()
    cases.append((duplicate, _command(), ErrorCode.DUPLICATE_JSHSHIR))
    invalid = FakeIdentityRepository()
    invalid_command = SaveCustomerIdentity(
        actor=CustomerIdentityActor(ACTOR_ID),
        expected_revision=0,
        first_name="",
        last_name="Customer",
        middle_name=None,
        jshshir="bad",
        document_type="PASSPORT",
        document_number="bad",
    )
    cases.append((invalid, invalid_command, ErrorCode.VALIDATION_ERROR))

    for repository, command, expected_code in cases:
        with pytest.raises(CustomerIdentityServiceError) as caught:
            save_own_customer_identity(
                repository=repository,
                audit_writer=CapturingAuditWriter(),
                crypto_config=_config(),
                command=command,
                now=NOW,
            )
        assert caught.value.code is expected_code
        assert caught.value.__cause__ is None


def test_read_and_completeness_verify_aead_and_blind_index() -> None:
    repository = FakeIdentityRepository(_record())
    config = _config()

    summary = get_own_customer_identity_view(
        repository=repository,
        crypto_config=config,
        actor=CustomerIdentityActor(ACTOR_ID),
    )

    assert summary is not None
    assert summary.masked_jshshir == "**********1234"
    assert CustomerIdentityCompletenessService(
        repository=repository,
        crypto_config=config,
    )(customer_id=CUSTOMER_ID)

    original = repository.record
    assert original is not None
    repository.record = EncryptedCustomerIdentityRecord(
        customer_id=original.customer_id,
        envelope=original.envelope,
        jshshir_blind_index=JshshirBlindIndex(b"Z" * 32),
        revision=original.revision,
    )
    with pytest.raises(CustomerIdentityServiceError) as caught:
        get_own_customer_identity_view(
            repository=repository,
            crypto_config=config,
            actor=CustomerIdentityActor(ACTOR_ID),
        )
    assert caught.value.code is ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE
    assert not CustomerIdentityCompletenessService(
        repository=repository,
        crypto_config=config,
    )(customer_id=CUSTOMER_ID)


def test_tamper_unknown_key_and_cross_user_read_fail_closed() -> None:
    original = _record()
    tampered = EncryptedCustomerIdentityRecord(
        customer_id=original.customer_id,
        envelope=CustomerIdentityEnvelope(
            ciphertext=original.envelope.ciphertext[:-1]
            + bytes((original.envelope.ciphertext[-1] ^ 1,)),
            nonce=original.envelope.nonce,
            key_id=original.envelope.key_id,
            schema_version=1,
        ),
        jshshir_blind_index=original.jshshir_blind_index,
        revision=original.revision,
    )
    unknown_key = EncryptedCustomerIdentityRecord(
        customer_id=original.customer_id,
        envelope=CustomerIdentityEnvelope(
            ciphertext=original.envelope.ciphertext,
            nonce=original.envelope.nonce,
            key_id=CustomerIdentityKeyId("unknown-key"),
            schema_version=1,
        ),
        jshshir_blind_index=original.jshshir_blind_index,
        revision=original.revision,
    )
    for record in (tampered, unknown_key):
        with pytest.raises(CustomerIdentityServiceError) as caught:
            get_own_customer_identity_view(
                repository=FakeIdentityRepository(record),
                crypto_config=_config(),
                actor=CustomerIdentityActor(ACTOR_ID),
            )
        assert caught.value.code is ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE
        assert "unknown-key" not in repr(caught.value)
        assert "12345678901234" not in str(caught.value)

    with pytest.raises(CustomerIdentityServiceError) as caught:
        get_own_customer_identity_view(
            repository=FakeIdentityRepository(original),
            crypto_config=_config(),
            actor=CustomerIdentityActor(UUID(int=99)),
        )
    assert caught.value.code is ErrorCode.CUSTOMER_DRAFT_REQUIRED


def test_service_dependencies_match_inner_protocols() -> None:
    assert isinstance(FakeIdentityRepository(), CustomerIdentityRepository)


def test_identity_service_has_no_transaction_owner_or_external_io() -> None:
    source = inspect.getsource(identity_service_module)

    for forbidden in (
        ".commit(",
        ".rollback(",
        ".close(",
        "sqlalchemy",
        "app.storage",
        "boto",
        "httpx",
        "presigned",
        "logger",
        "print(",
    ):
        assert forbidden not in source
