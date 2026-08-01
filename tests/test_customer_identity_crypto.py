import hashlib
import hmac
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.customer_identity.canonicalization import (
    canonicalize_customer_identity,
    canonicalize_jshshir,
)
from app.customer_identity.contracts import CustomerDocumentType
from app.customer_identity.crypto import (
    CUSTOMER_IDENTITY_BLIND_INDEX_BYTES,
    CUSTOMER_IDENTITY_NONCE_BYTES,
    CustomerIdentityAesKey,
    CustomerIdentityBlindIndexKey,
    CustomerIdentityCryptoConfig,
    CustomerIdentityCryptoConfigurationError,
    CustomerIdentityCryptoError,
    CustomerIdentityEnvelope,
    CustomerIdentityKeyId,
    JshshirBlindIndex,
    compute_jshshir_blind_index,
    decrypt_customer_identity,
    encrypt_customer_identity,
    serialize_customer_identity_payload,
)

CUSTOMER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_CUSTOMER_ID = UUID("22222222-2222-2222-2222-222222222222")
ACTIVE_KEY_ID_TEXT = "identity-active-v1"
HISTORICAL_KEY_ID_TEXT = "identity-history-v0"
ACTIVE_KEY_BYTES = bytes(range(32))
HISTORICAL_KEY_BYTES = bytes(range(32, 64))
BLIND_KEY_BYTES = bytes(reversed(range(32)))


def _identity():
    return canonicalize_customer_identity(
        first_name="Synthetic",
        last_name="Customer",
        middle_name=None,
        jshshir="12345678901234",
        document_type=CustomerDocumentType.PASSPORT,
        document_number="AB 12345",
    )


def _config(
    *,
    active_key_bytes: bytes = ACTIVE_KEY_BYTES,
    blind_key_bytes: bytes = BLIND_KEY_BYTES,
) -> CustomerIdentityCryptoConfig:
    return CustomerIdentityCryptoConfig(
        active_key_id=CustomerIdentityKeyId(ACTIVE_KEY_ID_TEXT),
        encryption_keys={
            CustomerIdentityKeyId(ACTIVE_KEY_ID_TEXT): (
                CustomerIdentityAesKey.from_bytes(active_key_bytes)
            ),
            CustomerIdentityKeyId(HISTORICAL_KEY_ID_TEXT): (
                CustomerIdentityAesKey.from_bytes(HISTORICAL_KEY_BYTES)
            ),
        },
        blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(blind_key_bytes),
    )


def test_key_id_and_key_material_contracts_are_exact_and_redacted() -> None:
    key_id = CustomerIdentityKeyId(ACTIVE_KEY_ID_TEXT)
    aes_key = CustomerIdentityAesKey.from_bytes(ACTIVE_KEY_BYTES)
    blind_key = CustomerIdentityBlindIndexKey.from_bytes(BLIND_KEY_BYTES)

    rendered = f"{key_id!r} {key_id!s} {aes_key!r} {aes_key!s} {blind_key!r}"
    assert ACTIVE_KEY_ID_TEXT not in rendered
    assert ACTIVE_KEY_BYTES.hex() not in rendered
    assert BLIND_KEY_BYTES.hex() not in rendered
    assert key_id.as_persistence_value() == ACTIVE_KEY_ID_TEXT
    assert aes_key.as_aesgcm_key() == ACTIVE_KEY_BYTES
    assert blind_key.as_hmac_key() == BLIND_KEY_BYTES


@pytest.mark.parametrize(
    "raw_key_id",
    ["", "a" * 65, "contains/slash", "outer space", "ключ", "line\nbreak"],
)
def test_key_id_rejects_invalid_values_without_echoing_them(raw_key_id: str) -> None:
    with pytest.raises(CustomerIdentityCryptoConfigurationError) as caught:
        CustomerIdentityKeyId(raw_key_id)

    if raw_key_id:
        assert raw_key_id not in str(caught.value)
        assert raw_key_id not in repr(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("length", [0, 1, 16, 31, 33, 64])
def test_key_material_requires_exactly_thirty_two_bytes(length: int) -> None:
    raw = b"K" * length
    for factory in (
        CustomerIdentityAesKey.from_bytes,
        CustomerIdentityBlindIndexKey.from_bytes,
    ):
        with pytest.raises(CustomerIdentityCryptoConfigurationError) as caught:
            factory(raw)
        if raw:
            assert raw.hex() not in str(caught.value)
            assert raw.hex() not in repr(caught.value)


def test_crypto_config_is_immutable_redacted_and_supports_exact_key_lookup() -> None:
    config = _config()
    active_id, active_key = config.get_active_write_key()
    historical_id = CustomerIdentityKeyId(HISTORICAL_KEY_ID_TEXT)

    assert active_id.as_persistence_value() == ACTIVE_KEY_ID_TEXT
    assert active_key.as_aesgcm_key() == ACTIVE_KEY_BYTES
    assert config.get_decryption_key(historical_id).as_aesgcm_key() == (
        HISTORICAL_KEY_BYTES
    )
    assert config.get_blind_index_key().as_hmac_key() == BLIND_KEY_BYTES
    assert repr(config) == "CustomerIdentityCryptoConfig(<redacted>)"
    assert ACTIVE_KEY_ID_TEXT not in repr(config)
    assert not hasattr(config, "encryption_keys")
    with pytest.raises(FrozenInstanceError):
        config._active_key_id = historical_id  # type: ignore[misc]
    assert isinstance(config._encryption_keys, MappingProxyType)
    with pytest.raises(TypeError):
        config._encryption_keys[historical_id] = active_key  # type: ignore[index]


def test_crypto_config_rejects_empty_unknown_duplicate_and_reused_keys() -> None:
    active_id = CustomerIdentityKeyId(ACTIVE_KEY_ID_TEXT)
    other_id = CustomerIdentityKeyId(HISTORICAL_KEY_ID_TEXT)
    aes_key = CustomerIdentityAesKey.from_bytes(ACTIVE_KEY_BYTES)
    blind_key = CustomerIdentityBlindIndexKey.from_bytes(BLIND_KEY_BYTES)

    invalid_mappings = (
        (active_id, {}),
        (active_id, {other_id: aes_key}),
        (
            active_id,
            {
                active_id: aes_key,
                other_id: CustomerIdentityAesKey.from_bytes(ACTIVE_KEY_BYTES),
            },
        ),
    )
    for selected_id, mapping in invalid_mappings:
        with pytest.raises(CustomerIdentityCryptoConfigurationError):
            CustomerIdentityCryptoConfig(
                active_key_id=selected_id,
                encryption_keys=mapping,
                blind_index_key=blind_key,
            )

    with pytest.raises(CustomerIdentityCryptoConfigurationError):
        CustomerIdentityCryptoConfig(
            active_key_id=active_id,
            encryption_keys={active_id: aes_key},
            blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(ACTIVE_KEY_BYTES),
        )


def test_unknown_decryption_key_fails_without_fallback_or_key_id_leakage() -> None:
    config = _config()
    unknown_id = CustomerIdentityKeyId("unknown-retired-key")

    with pytest.raises(CustomerIdentityCryptoError) as caught:
        config.get_decryption_key(unknown_id)

    assert "unknown-retired-key" not in str(caught.value)
    assert "unknown-retired-key" not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_aesgcm_round_trip_uses_exact_aad_and_ciphertext_includes_tag(
    monkeypatch,
) -> None:
    fixed_nonce = b"N" * CUSTOMER_IDENTITY_NONCE_BYTES
    monkeypatch.setattr(
        "app.customer_identity.crypto.secrets.token_bytes",
        lambda length: fixed_nonce if length == CUSTOMER_IDENTITY_NONCE_BYTES else b"",
    )
    identity = _identity()
    config = _config()

    envelope = encrypt_customer_identity(
        identity,
        customer_id=CUSTOMER_ID,
        crypto_config=config,
    )

    expected_aad = (
        b"NASIYA-CUSTOMER-IDENTITY-V1\x00"
        + CUSTOMER_ID.bytes
        + (1).to_bytes(4, byteorder="big", signed=False)
    )
    plaintext = AESGCM(ACTIVE_KEY_BYTES).decrypt(
        fixed_nonce,
        envelope.ciphertext,
        expected_aad,
    )
    assert plaintext == serialize_customer_identity_payload(identity)
    assert len(envelope.ciphertext) == len(plaintext) + 16
    assert (
        decrypt_customer_identity(
            envelope,
            customer_id=CUSTOMER_ID,
            crypto_config=config,
        )
        == identity
    )


def test_random_nonce_produces_distinct_nonce_and_ciphertext() -> None:
    identity = _identity()
    config = _config()

    first = encrypt_customer_identity(
        identity,
        customer_id=CUSTOMER_ID,
        crypto_config=config,
    )
    second = encrypt_customer_identity(
        identity,
        customer_id=CUSTOMER_ID,
        crypto_config=config,
    )

    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


@pytest.mark.parametrize("failure", ["customer", "key", "tamper", "truncate"])
def test_wrong_customer_key_and_ciphertext_tampering_fail_closed(failure: str) -> None:
    identity = _identity()
    config = _config()
    envelope = encrypt_customer_identity(
        identity,
        customer_id=CUSTOMER_ID,
        crypto_config=config,
    )
    customer_id = CUSTOMER_ID
    decrypt_config = config
    tested_envelope = envelope
    if failure == "customer":
        customer_id = OTHER_CUSTOMER_ID
    elif failure == "key":
        decrypt_config = _config(active_key_bytes=b"Z" * 32)
    elif failure == "tamper":
        tested_envelope = CustomerIdentityEnvelope(
            ciphertext=envelope.ciphertext[:-1] + bytes((envelope.ciphertext[-1] ^ 1,)),
            nonce=envelope.nonce,
            key_id=envelope.key_id,
            schema_version=envelope.schema_version,
        )
    else:
        tested_envelope = CustomerIdentityEnvelope(
            ciphertext=envelope.ciphertext[:-1],
            nonce=envelope.nonce,
            key_id=envelope.key_id,
            schema_version=envelope.schema_version,
        )

    with pytest.raises(CustomerIdentityCryptoError) as caught:
        decrypt_customer_identity(
            tested_envelope,
            customer_id=customer_id,
            crypto_config=decrypt_config,
        )

    assert caught.value.__cause__ is None
    assert "Synthetic" not in repr(caught.value)
    assert "12345678901234" not in str(caught.value)


def test_unknown_key_and_unsupported_schema_fail_closed() -> None:
    config = _config()
    envelope = encrypt_customer_identity(
        _identity(),
        customer_id=CUSTOMER_ID,
        crypto_config=config,
    )
    unknown_key_envelope = CustomerIdentityEnvelope(
        ciphertext=envelope.ciphertext,
        nonce=envelope.nonce,
        key_id=CustomerIdentityKeyId("retired-unknown"),
        schema_version=1,
    )

    with pytest.raises(CustomerIdentityCryptoError):
        decrypt_customer_identity(
            unknown_key_envelope,
            customer_id=CUSTOMER_ID,
            crypto_config=config,
        )
    with pytest.raises(CustomerIdentityCryptoError):
        CustomerIdentityEnvelope(
            ciphertext=envelope.ciphertext,
            nonce=envelope.nonce,
            key_id=envelope.key_id,
            schema_version=2,
        )


def test_envelope_repr_redacts_ciphertext_nonce_and_key_id() -> None:
    envelope = encrypt_customer_identity(
        _identity(),
        customer_id=CUSTOMER_ID,
        crypto_config=_config(),
    )
    rendered = repr(envelope)

    assert envelope.ciphertext.hex() not in rendered
    assert envelope.nonce.hex() not in rendered
    assert ACTIVE_KEY_ID_TEXT not in rendered
    assert rendered == (
        "CustomerIdentityEnvelope("
        "ciphertext=<redacted>, nonce=<redacted>, key_id=<redacted>, "
        "schema_version=1)"
    )


def test_blind_index_uses_exact_domain_prefixed_hmac_sha256() -> None:
    jshshir = canonicalize_jshshir("12345678901234")
    key = CustomerIdentityBlindIndexKey.from_bytes(BLIND_KEY_BYTES)

    blind_index = compute_jshshir_blind_index(jshshir, blind_index_key=key)

    expected = hmac.new(
        BLIND_KEY_BYTES,
        b"NASIYA-JSHSHIR-V1\x00" + b"12345678901234",
        hashlib.sha256,
    ).digest()
    assert len(blind_index.as_persistence_bytes()) == (
        CUSTOMER_IDENTITY_BLIND_INDEX_BYTES
    )
    assert blind_index.as_persistence_bytes() == expected
    assert repr(blind_index) == "JshshirBlindIndex(<redacted>)"
    assert str(blind_index) == "<redacted>"
    assert expected.hex() not in repr(blind_index)


def test_blind_index_is_deterministic_and_separated_by_input_and_key() -> None:
    first_jshshir = canonicalize_jshshir("12345678901234")
    second_jshshir = canonicalize_jshshir("12345678901235")
    first_key = CustomerIdentityBlindIndexKey.from_bytes(BLIND_KEY_BYTES)
    second_key = CustomerIdentityBlindIndexKey.from_bytes(b"B" * 32)

    first = compute_jshshir_blind_index(
        first_jshshir,
        blind_index_key=first_key,
    )
    replay = compute_jshshir_blind_index(
        first_jshshir,
        blind_index_key=first_key,
    )
    other_input = compute_jshshir_blind_index(
        second_jshshir,
        blind_index_key=first_key,
    )
    other_key = compute_jshshir_blind_index(
        first_jshshir,
        blind_index_key=second_key,
    )

    assert replay == first
    assert other_input != first
    assert other_key != first


def test_blind_index_value_rejects_wrong_length() -> None:
    for raw in (b"", b"I" * 31, b"I" * 33):
        with pytest.raises(ValueError, match="blind index is invalid"):
            JshshirBlindIndex(raw)
