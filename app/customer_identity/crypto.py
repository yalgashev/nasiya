from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes

if TYPE_CHECKING:
    from app.customer_identity.contracts import CanonicalCustomerIdentity, Jshshir

CUSTOMER_IDENTITY_SCHEMA_VERSION: Final = 1
CUSTOMER_IDENTITY_AES_KEY_BYTES: Final = 32
CUSTOMER_IDENTITY_NONCE_BYTES: Final = 12
CUSTOMER_IDENTITY_BLIND_INDEX_BYTES: Final = 32
_CUSTOMER_IDENTITY_AAD_PREFIX: Final = b"NASIYA-CUSTOMER-IDENTITY-V1\x00"
_JSHSHIR_BLIND_INDEX_PREFIX: Final = b"NASIYA-JSHSHIR-V1\x00"
_KEY_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{1,64}", flags=re.ASCII)
_PAYLOAD_KEYS: Final = (
    "first_name",
    "last_name",
    "middle_name",
    "jshshir",
    "document_type",
    "document_number",
)


class CustomerIdentityCryptoConfigurationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Customer identity cryptography is unavailable")

    def __repr__(self) -> str:
        return "CustomerIdentityCryptoConfigurationError()"


class CustomerIdentityCryptoError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Customer identity cryptography is unavailable")

    def __repr__(self) -> str:
        return "CustomerIdentityCryptoError()"


class CustomerIdentityPayloadError(ValueError):
    def __init__(self) -> None:
        super().__init__("Customer identity payload is invalid")

    def __repr__(self) -> str:
        return "CustomerIdentityPayloadError()"


class _DuplicateJsonKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class CustomerIdentityKeyId:
    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self._value, str)
            or _KEY_ID_PATTERN.fullmatch(self._value) is None
        ):
            raise CustomerIdentityCryptoConfigurationError() from None

    def __repr__(self) -> str:
        return "CustomerIdentityKeyId(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_persistence_value(self) -> str:
        return self._value


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class CustomerIdentityAesKey:
    _value: SecretBytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self._value, SecretBytes):
            raise CustomerIdentityCryptoConfigurationError() from None
        if len(self._value.get_secret_value()) != CUSTOMER_IDENTITY_AES_KEY_BYTES:
            raise CustomerIdentityCryptoConfigurationError() from None

    @classmethod
    def from_bytes(cls, value: bytes) -> CustomerIdentityAesKey:
        if not isinstance(value, bytes):
            raise CustomerIdentityCryptoConfigurationError() from None
        return cls(SecretBytes(value))

    def __repr__(self) -> str:
        return "CustomerIdentityAesKey(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_aesgcm_key(self) -> bytes:
        return self._value.get_secret_value()


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class CustomerIdentityBlindIndexKey:
    _value: SecretBytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self._value, SecretBytes):
            raise CustomerIdentityCryptoConfigurationError() from None
        if len(self._value.get_secret_value()) != CUSTOMER_IDENTITY_BLIND_INDEX_BYTES:
            raise CustomerIdentityCryptoConfigurationError() from None

    @classmethod
    def from_bytes(cls, value: bytes) -> CustomerIdentityBlindIndexKey:
        if not isinstance(value, bytes):
            raise CustomerIdentityCryptoConfigurationError() from None
        return cls(SecretBytes(value))

    def __repr__(self) -> str:
        return "CustomerIdentityBlindIndexKey(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_hmac_key(self) -> bytes:
        return self._value.get_secret_value()


@dataclass(frozen=True, slots=True, repr=False, init=False, eq=False)
class CustomerIdentityCryptoConfig:
    _active_key_id: CustomerIdentityKeyId = field(repr=False)
    _encryption_keys: Mapping[
        CustomerIdentityKeyId,
        CustomerIdentityAesKey,
    ] = field(repr=False, compare=False)
    _blind_index_key: CustomerIdentityBlindIndexKey = field(
        repr=False,
        compare=False,
    )

    def __init__(
        self,
        *,
        active_key_id: CustomerIdentityKeyId,
        encryption_keys: Mapping[CustomerIdentityKeyId, CustomerIdentityAesKey],
        blind_index_key: CustomerIdentityBlindIndexKey,
    ) -> None:
        try:
            if not isinstance(active_key_id, CustomerIdentityKeyId):
                raise CustomerIdentityCryptoConfigurationError()
            if not isinstance(encryption_keys, Mapping) or not encryption_keys:
                raise CustomerIdentityCryptoConfigurationError()
            copied_keys = dict(encryption_keys)
            if any(
                not isinstance(key_id, CustomerIdentityKeyId)
                or not isinstance(key, CustomerIdentityAesKey)
                for key_id, key in copied_keys.items()
            ):
                raise CustomerIdentityCryptoConfigurationError()
            if active_key_id not in copied_keys:
                raise CustomerIdentityCryptoConfigurationError()
            if not isinstance(blind_index_key, CustomerIdentityBlindIndexKey):
                raise CustomerIdentityCryptoConfigurationError()
            aes_materials = [key.as_aesgcm_key() for key in copied_keys.values()]
            if len(set(aes_materials)) != len(aes_materials):
                raise CustomerIdentityCryptoConfigurationError()
            blind_material = blind_index_key.as_hmac_key()
            if any(
                hmac.compare_digest(blind_material, material)
                for material in aes_materials
            ):
                raise CustomerIdentityCryptoConfigurationError()
        except CustomerIdentityCryptoConfigurationError:
            raise
        except (TypeError, ValueError):
            raise CustomerIdentityCryptoConfigurationError() from None

        object.__setattr__(self, "_active_key_id", active_key_id)
        object.__setattr__(
            self,
            "_encryption_keys",
            MappingProxyType(copied_keys),
        )
        object.__setattr__(self, "_blind_index_key", blind_index_key)

    def __repr__(self) -> str:
        return "CustomerIdentityCryptoConfig(<redacted>)"

    def get_active_write_key(
        self,
    ) -> tuple[CustomerIdentityKeyId, CustomerIdentityAesKey]:
        return self._active_key_id, self._encryption_keys[self._active_key_id]

    def get_decryption_key(
        self,
        key_id: CustomerIdentityKeyId,
    ) -> CustomerIdentityAesKey:
        try:
            return self._encryption_keys[key_id]
        except (KeyError, TypeError):
            raise CustomerIdentityCryptoError() from None

    def get_blind_index_key(self) -> CustomerIdentityBlindIndexKey:
        return self._blind_index_key


@dataclass(frozen=True, slots=True, repr=False)
class CustomerIdentityEnvelope:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    key_id: CustomerIdentityKeyId = field(repr=False)
    schema_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.ciphertext, bytes) or len(self.ciphertext) < 16:
            raise CustomerIdentityCryptoError() from None
        if (
            not isinstance(self.nonce, bytes)
            or len(self.nonce) != CUSTOMER_IDENTITY_NONCE_BYTES
        ):
            raise CustomerIdentityCryptoError() from None
        if not isinstance(self.key_id, CustomerIdentityKeyId):
            raise CustomerIdentityCryptoError() from None
        try:
            _require_supported_schema_version(self.schema_version)
        except CustomerIdentityPayloadError:
            raise CustomerIdentityCryptoError() from None

    def __repr__(self) -> str:
        return (
            "CustomerIdentityEnvelope("
            "ciphertext=<redacted>, nonce=<redacted>, key_id=<redacted>, "
            f"schema_version={self.schema_version!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class JshshirBlindIndex:
    _value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self._value, bytes)
            or len(self._value) != CUSTOMER_IDENTITY_BLIND_INDEX_BYTES
        ):
            raise ValueError("JSHSHIR blind index is invalid")

    def __repr__(self) -> str:
        return "JshshirBlindIndex(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_persistence_bytes(self) -> bytes:
        return self._value


def serialize_customer_identity_payload(
    identity: CanonicalCustomerIdentity,
    *,
    schema_version: int = CUSTOMER_IDENTITY_SCHEMA_VERSION,
) -> bytes:
    from app.customer_identity.contracts import CanonicalCustomerIdentity

    _require_supported_schema_version(schema_version)
    if not isinstance(identity, CanonicalCustomerIdentity):
        raise CustomerIdentityPayloadError()
    payload = {
        "first_name": identity.first_name.as_crypto_plaintext(),
        "last_name": identity.last_name.as_crypto_plaintext(),
        "middle_name": (
            identity.middle_name.as_crypto_plaintext()
            if identity.middle_name is not None
            else None
        ),
        "jshshir": identity.jshshir.as_crypto_plaintext(),
        "document_type": identity.document_type.value,
        "document_number": identity.document_number.as_crypto_plaintext(),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def deserialize_customer_identity_payload(
    payload: bytes,
    *,
    schema_version: int = CUSTOMER_IDENTITY_SCHEMA_VERSION,
) -> CanonicalCustomerIdentity:
    from app.customer_identity.canonicalization import canonicalize_customer_identity

    try:
        _require_supported_schema_version(schema_version)
        if not isinstance(payload, bytes):
            raise CustomerIdentityPayloadError()
        decoded = payload.decode("utf-8", errors="strict")
        raw = json.loads(decoded, object_pairs_hook=_strict_json_object)
        if not isinstance(raw, dict) or tuple(raw) != _PAYLOAD_KEYS:
            raise CustomerIdentityPayloadError()
        identity = canonicalize_customer_identity(
            first_name=_require_string(raw["first_name"]),
            last_name=_require_string(raw["last_name"]),
            middle_name=_require_optional_string(raw["middle_name"]),
            jshshir=_require_string(raw["jshshir"]),
            document_type=_require_string(raw["document_type"]),
            document_number=_require_string(raw["document_number"]),
        )
        if (
            serialize_customer_identity_payload(
                identity,
                schema_version=schema_version,
            )
            != payload
        ):
            raise CustomerIdentityPayloadError()
        return identity
    except CustomerIdentityPayloadError:
        raise
    except (KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise CustomerIdentityPayloadError() from None


def encrypt_customer_identity(
    identity: CanonicalCustomerIdentity,
    *,
    customer_id: UUID,
    crypto_config: CustomerIdentityCryptoConfig,
) -> CustomerIdentityEnvelope:
    try:
        _require_uuid(customer_id)
        if not isinstance(crypto_config, CustomerIdentityCryptoConfig):
            raise CustomerIdentityCryptoError()
        key_id, key = crypto_config.get_active_write_key()
        nonce = secrets.token_bytes(CUSTOMER_IDENTITY_NONCE_BYTES)
        if len(nonce) != CUSTOMER_IDENTITY_NONCE_BYTES:
            raise CustomerIdentityCryptoError()
        plaintext = serialize_customer_identity_payload(identity)
        ciphertext = AESGCM(key.as_aesgcm_key()).encrypt(
            nonce,
            plaintext,
            _build_customer_identity_aad(
                customer_id=customer_id,
                schema_version=CUSTOMER_IDENTITY_SCHEMA_VERSION,
            ),
        )
        return CustomerIdentityEnvelope(
            ciphertext=ciphertext,
            nonce=nonce,
            key_id=key_id,
            schema_version=CUSTOMER_IDENTITY_SCHEMA_VERSION,
        )
    except CustomerIdentityCryptoError:
        raise
    except (CustomerIdentityPayloadError, TypeError, ValueError):
        raise CustomerIdentityCryptoError() from None


def decrypt_customer_identity(
    envelope: CustomerIdentityEnvelope,
    *,
    customer_id: UUID,
    crypto_config: CustomerIdentityCryptoConfig,
) -> CanonicalCustomerIdentity:
    try:
        _require_uuid(customer_id)
        if not isinstance(envelope, CustomerIdentityEnvelope) or not isinstance(
            crypto_config,
            CustomerIdentityCryptoConfig,
        ):
            raise CustomerIdentityCryptoError()
        key = crypto_config.get_decryption_key(envelope.key_id)
        plaintext = AESGCM(key.as_aesgcm_key()).decrypt(
            envelope.nonce,
            envelope.ciphertext,
            _build_customer_identity_aad(
                customer_id=customer_id,
                schema_version=envelope.schema_version,
            ),
        )
        return deserialize_customer_identity_payload(
            plaintext,
            schema_version=envelope.schema_version,
        )
    except CustomerIdentityCryptoError:
        raise
    except (
        CustomerIdentityPayloadError,
        InvalidTag,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise CustomerIdentityCryptoError() from None


def compute_jshshir_blind_index(
    jshshir: Jshshir,
    *,
    blind_index_key: CustomerIdentityBlindIndexKey,
) -> JshshirBlindIndex:
    from app.customer_identity.contracts import Jshshir

    if not isinstance(jshshir, Jshshir) or not isinstance(
        blind_index_key,
        CustomerIdentityBlindIndexKey,
    ):
        raise ValueError("JSHSHIR blind-index input is invalid")
    digest = hmac.new(
        blind_index_key.as_hmac_key(),
        _JSHSHIR_BLIND_INDEX_PREFIX + jshshir.as_crypto_plaintext().encode("ascii"),
        hashlib.sha256,
    ).digest()
    return JshshirBlindIndex(digest)


def _strict_json_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError()
        result[key] = value
    return result


def _require_supported_schema_version(schema_version: int) -> None:
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != CUSTOMER_IDENTITY_SCHEMA_VERSION
    ):
        raise CustomerIdentityPayloadError()


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise CustomerIdentityPayloadError()
    return value


def _require_optional_string(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise CustomerIdentityPayloadError()


def _build_customer_identity_aad(*, customer_id: UUID, schema_version: int) -> bytes:
    _require_uuid(customer_id)
    _require_supported_schema_version(schema_version)
    return (
        _CUSTOMER_IDENTITY_AAD_PREFIX
        + customer_id.bytes
        + schema_version.to_bytes(4, byteorder="big", signed=False)
    )


def _require_uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        raise CustomerIdentityCryptoError()
