from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from app.customer_identity.crypto import (
    CustomerIdentityEnvelope,
    JshshirBlindIndex,
)

_FORBIDDEN_NAME_CATEGORIES: Final = frozenset({"Cc", "Cf", "Co", "Cs"})
_DOCUMENT_NUMBER_PATTERN: Final = re.compile(
    r"[A-Z0-9 -]{5,32}",
    flags=re.ASCII,
)


class CustomerDocumentType(StrEnum):
    PASSPORT = "PASSPORT"
    ID_CARD = "ID_CARD"


def parse_customer_document_type(value: str) -> CustomerDocumentType:
    try:
        return CustomerDocumentType(value)
    except (TypeError, ValueError):
        raise ValueError("Unknown customer document type") from None


@dataclass(frozen=True, slots=True, repr=False)
class CustomerName:
    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self._value, str)
            or not 1 <= len(self._value) <= 100
            or self._value != " ".join(self._value.split())
            or any(
                unicodedata.category(character) in _FORBIDDEN_NAME_CATEGORIES
                for character in self._value
            )
        ):
            raise ValueError("Customer name is invalid")

    def __repr__(self) -> str:
        return "CustomerName(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_crypto_plaintext(self) -> str:
        return self._value


@dataclass(frozen=True, slots=True, repr=False)
class Jshshir:
    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self._value, str)
            or len(self._value) != 14
            or not self._value.isascii()
            or not self._value.isdigit()
        ):
            raise ValueError("JSHSHIR is invalid")

    def __repr__(self) -> str:
        return "Jshshir(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_crypto_plaintext(self) -> str:
        return self._value

    def masked_last_four(self) -> str:
        return f"**********{self._value[-4:]}"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDocumentNumber:
    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self._value, str)
            or _DOCUMENT_NUMBER_PATTERN.fullmatch(self._value) is None
        ):
            raise ValueError("Customer document number is invalid")

    def __repr__(self) -> str:
        return "CustomerDocumentNumber(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_crypto_plaintext(self) -> str:
        return self._value

    def masked_last_four(self) -> str:
        hidden_length = max(len(self._value) - 4, 0)
        return f"{'*' * hidden_length}{self._value[-4:]}"


@dataclass(frozen=True, slots=True, repr=False)
class CanonicalCustomerIdentity:
    first_name: CustomerName = field(repr=False)
    last_name: CustomerName = field(repr=False)
    middle_name: CustomerName | None = field(repr=False)
    jshshir: Jshshir = field(repr=False)
    document_type: CustomerDocumentType
    document_number: CustomerDocumentNumber = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.first_name, CustomerName):
            raise ValueError("Customer first name is invalid")
        if not isinstance(self.last_name, CustomerName):
            raise ValueError("Customer last name is invalid")
        if self.middle_name is not None and not isinstance(
            self.middle_name,
            CustomerName,
        ):
            raise ValueError("Customer middle name is invalid")
        if not isinstance(self.jshshir, Jshshir):
            raise ValueError("Customer JSHSHIR is invalid")
        if not isinstance(self.document_type, CustomerDocumentType):
            raise ValueError("Customer document type is invalid")
        if not isinstance(self.document_number, CustomerDocumentNumber):
            raise ValueError("Customer document number is invalid")

    def __repr__(self) -> str:
        return (
            "CanonicalCustomerIdentity("
            "first_name=<redacted>, last_name=<redacted>, "
            "middle_name=<redacted>, jshshir=<redacted>, "
            f"document_type={self.document_type.value!r}, "
            "document_number=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class IdentityRevision:
    value: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, int)
            or isinstance(self.value, bool)
            or self.value < 1
        ):
            raise ValueError("Identity revision must be positive")


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedCustomerIdentityRecord:
    customer_id: UUID = field(repr=False)
    envelope: CustomerIdentityEnvelope = field(repr=False)
    jshshir_blind_index: JshshirBlindIndex = field(repr=False)
    revision: IdentityRevision

    def __post_init__(self) -> None:
        _require_uuid(self.customer_id, field_name="customer_id")
        if not isinstance(self.envelope, CustomerIdentityEnvelope):
            raise ValueError("Customer identity envelope is invalid")
        if not isinstance(self.jshshir_blind_index, JshshirBlindIndex):
            raise ValueError("Customer identity blind index is invalid")
        if not isinstance(self.revision, IdentityRevision):
            raise ValueError("Customer identity revision is invalid")

    def __repr__(self) -> str:
        return (
            "EncryptedCustomerIdentityRecord("
            "customer_id=<redacted>, envelope=<redacted>, "
            "jshshir_blind_index=<redacted>, "
            f"revision={self.revision.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class DecryptedCustomerIdentity:
    customer_id: UUID = field(repr=False)
    identity: CanonicalCustomerIdentity = field(repr=False)
    revision: IdentityRevision

    def __post_init__(self) -> None:
        _require_uuid(self.customer_id, field_name="customer_id")
        if not isinstance(self.identity, CanonicalCustomerIdentity):
            raise ValueError("Decrypted customer identity is invalid")
        if not isinstance(self.revision, IdentityRevision):
            raise ValueError("Customer identity revision is invalid")

    def __repr__(self) -> str:
        return (
            "DecryptedCustomerIdentity("
            "customer_id=<redacted>, identity=<redacted>, "
            f"revision={self.revision.value!r})"
        )

    def to_safe_masked_summary(self) -> CustomerIdentityMaskedSummary:
        return CustomerIdentityMaskedSummary(
            first_name=self.identity.first_name.as_crypto_plaintext(),
            last_name=self.identity.last_name.as_crypto_plaintext(),
            middle_name=(
                self.identity.middle_name.as_crypto_plaintext()
                if self.identity.middle_name is not None
                else None
            ),
            masked_jshshir=self.identity.jshshir.masked_last_four(),
            document_type=self.identity.document_type,
            masked_document_number=(self.identity.document_number.masked_last_four()),
            revision=self.revision,
        )


@dataclass(frozen=True, slots=True, repr=False)
class CustomerIdentityMaskedSummary:
    first_name: str = field(repr=False)
    last_name: str = field(repr=False)
    middle_name: str | None = field(repr=False)
    masked_jshshir: str = field(repr=False)
    document_type: CustomerDocumentType
    masked_document_number: str = field(repr=False)
    revision: IdentityRevision

    def __post_init__(self) -> None:
        CustomerName(self.first_name)
        CustomerName(self.last_name)
        if self.middle_name is not None:
            CustomerName(self.middle_name)
        if not re.fullmatch(r"\*{10}[0-9]{4}", self.masked_jshshir):
            raise ValueError("Masked JSHSHIR is invalid")
        if not isinstance(self.document_type, CustomerDocumentType):
            raise ValueError("Customer document type is invalid")
        if not re.fullmatch(
            r"\*{1,28}[A-Z0-9 -]{4}",
            self.masked_document_number,
        ):
            raise ValueError("Masked customer document number is invalid")
        if not isinstance(self.revision, IdentityRevision):
            raise ValueError("Customer identity revision is invalid")

    def __repr__(self) -> str:
        return (
            "CustomerIdentityMaskedSummary("
            "names=<redacted>, masked_jshshir=<redacted>, "
            f"document_type={self.document_type.value!r}, "
            "masked_document_number=<redacted>, "
            f"revision={self.revision.value!r})"
        )


@runtime_checkable
class HasCompleteCustomerIdentity(Protocol):
    def __call__(self, *, customer_id: UUID) -> bool: ...


@dataclass(frozen=True, slots=True, repr=False)
class OwnCustomerDraft:
    customer_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.customer_id, field_name="customer_id")

    def __repr__(self) -> str:
        return "OwnCustomerDraft(customer_id=<redacted>)"


@runtime_checkable
class CustomerIdentityRepository(Protocol):
    def lock_own_customer_draft(
        self,
        *,
        actor_user_id: UUID,
    ) -> OwnCustomerDraft | None: ...

    def get_identity(
        self,
        *,
        customer_id: UUID,
    ) -> EncryptedCustomerIdentityRecord | None: ...

    def lock_identity(
        self,
        *,
        customer_id: UUID,
    ) -> EncryptedCustomerIdentityRecord | None: ...

    def save_identity(
        self,
        *,
        record: EncryptedCustomerIdentityRecord,
        expected_revision: int,
    ) -> EncryptedCustomerIdentityRecord: ...


def _require_uuid(value: object, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{field_name} must be a UUID")
