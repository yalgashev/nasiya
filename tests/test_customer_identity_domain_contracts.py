from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from app.customer_identity.canonicalization import canonicalize_customer_identity
from app.customer_identity.contracts import (
    CustomerDocumentType,
    CustomerIdentityMaskedSummary,
    DecryptedCustomerIdentity,
    EncryptedCustomerIdentityRecord,
    HasCompleteCustomerIdentity,
    IdentityRevision,
)
from app.customer_identity.crypto import (
    CustomerIdentityEnvelope,
    CustomerIdentityKeyId,
    JshshirBlindIndex,
)

CUSTOMER_ID = UUID("11111111-1111-1111-1111-111111111111")
PLAINTEXT_MARKERS = (
    "Synthetic",
    "12345678901234",
    "AB 12345",
)


def _canonical_identity():
    return canonicalize_customer_identity(
        first_name="Synthetic",
        last_name="Customer",
        middle_name=None,
        jshshir="12345678901234",
        document_type=CustomerDocumentType.PASSPORT,
        document_number="AB 12345",
    )


def test_identity_revision_requires_positive_non_boolean_integer() -> None:
    assert IdentityRevision(1).value == 1
    for invalid in (True, False, 0, -1, 1.0, "1"):
        with pytest.raises(ValueError, match="revision must be positive"):
            IdentityRevision(invalid)  # type: ignore[arg-type]


def test_encrypted_record_is_exact_immutable_and_redacted() -> None:
    record = EncryptedCustomerIdentityRecord(
        customer_id=CUSTOMER_ID,
        envelope=CustomerIdentityEnvelope(
            ciphertext=b"C" * 16,
            nonce=b"N" * 12,
            key_id=CustomerIdentityKeyId("identity-key-v1"),
            schema_version=1,
        ),
        jshshir_blind_index=JshshirBlindIndex(b"I" * 32),
        revision=IdentityRevision(1),
    )
    rendered = repr(record)

    assert str(CUSTOMER_ID) not in rendered
    assert "identity-key-v1" not in rendered
    assert (b"C" * 16).hex() not in rendered
    assert (b"N" * 12).hex() not in rendered
    assert (b"I" * 32).hex() not in rendered
    with pytest.raises(FrozenInstanceError):
        record.customer_id = UUID(int=2)  # type: ignore[misc]


def test_decrypted_record_builds_last_four_only_summary_and_redacts_repr() -> None:
    decrypted = DecryptedCustomerIdentity(
        customer_id=CUSTOMER_ID,
        identity=_canonical_identity(),
        revision=IdentityRevision(7),
    )

    summary = decrypted.to_safe_masked_summary()

    assert summary.first_name == "Synthetic"
    assert summary.last_name == "Customer"
    assert summary.middle_name is None
    assert summary.masked_jshshir == "**********1234"
    assert summary.masked_document_number == "****2345"
    assert summary.document_type is CustomerDocumentType.PASSPORT
    assert summary.revision == IdentityRevision(7)
    for rendered in (repr(decrypted), repr(summary)):
        assert str(CUSTOMER_ID) not in rendered
        assert "1234" not in rendered
        assert "2345" not in rendered
        for marker in PLAINTEXT_MARKERS:
            assert marker not in rendered


def test_masked_summary_rejects_noncanonical_or_revealing_masks() -> None:
    base = {
        "first_name": "Synthetic",
        "last_name": "Customer",
        "middle_name": None,
        "masked_jshshir": "**********1234",
        "document_type": CustomerDocumentType.ID_CARD,
        "masked_document_number": "****2345",
        "revision": IdentityRevision(1),
    }
    for field_name, invalid in (
        ("masked_jshshir", "12345678901234"),
        ("masked_jshshir", "*********1234"),
        ("masked_document_number", "AB 12345"),
        ("masked_document_number", "2345"),
    ):
        values = dict(base)
        values[field_name] = invalid
        with pytest.raises(ValueError):
            CustomerIdentityMaskedSummary(**values)  # type: ignore[arg-type]


def test_completeness_protocol_is_runtime_checkable_and_narrow() -> None:
    class Complete:
        def __call__(self, *, customer_id: UUID) -> bool:
            return customer_id == CUSTOMER_ID

    implementation = Complete()
    assert isinstance(implementation, HasCompleteCustomerIdentity)
    assert implementation(customer_id=CUSTOMER_ID) is True
