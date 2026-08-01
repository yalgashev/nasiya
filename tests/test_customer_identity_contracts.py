import json
from dataclasses import FrozenInstanceError

import pytest

from app.customer_document.contracts import (
    CustomerDocumentStatus,
    parse_customer_document_status,
)
from app.customer_identity.canonicalization import (
    canonicalize_customer_document_number,
    canonicalize_customer_identity,
    canonicalize_customer_name,
    canonicalize_jshshir,
    canonicalize_optional_customer_name,
)
from app.customer_identity.contracts import (
    CanonicalCustomerIdentity,
    CustomerDocumentType,
    parse_customer_document_type,
)
from app.customer_identity.crypto import (
    CUSTOMER_IDENTITY_SCHEMA_VERSION,
    CustomerIdentityPayloadError,
    deserialize_customer_identity_payload,
    serialize_customer_identity_payload,
)


def _canonical_identity(
    *,
    middle_name: str | None = "Qodir o‘g‘li",
) -> CanonicalCustomerIdentity:
    return canonicalize_customer_identity(
        first_name="Ali",
        last_name="Valiyev",
        middle_name=middle_name,
        jshshir="12345678901234",
        document_type=CustomerDocumentType.ID_CARD,
        document_number="AA 1234567",
    )


def test_customer_identity_enum_allowlists_and_parsers_are_exact() -> None:
    assert tuple(CustomerDocumentType) == (
        CustomerDocumentType.PASSPORT,
        CustomerDocumentType.ID_CARD,
    )
    assert tuple(CustomerDocumentStatus) == (
        CustomerDocumentStatus.CURRENT,
        CustomerDocumentStatus.SUPERSEDED,
    )
    assert parse_customer_document_type("PASSPORT") is CustomerDocumentType.PASSPORT
    assert parse_customer_document_status("CURRENT") is CustomerDocumentStatus.CURRENT

    for parser, value in (
        (parse_customer_document_type, "passport"),
        (parse_customer_document_type, "DRIVER_LICENSE"),
        (parse_customer_document_status, "current"),
        (parse_customer_document_status, "DELETED"),
    ):
        with pytest.raises(ValueError, match="Unknown"):
            parser(value)


def test_customer_identity_enums_serialize_to_exact_string_values() -> None:
    encoded = json.dumps(
        {
            "document_type": CustomerDocumentType.ID_CARD,
            "status": CustomerDocumentStatus.SUPERSEDED,
        },
        separators=(",", ":"),
    )

    assert encoded == '{"document_type":"ID_CARD","status":"SUPERSEDED"}'


def test_names_collapse_unicode_whitespace_without_unicode_normalization() -> None:
    composed = canonicalize_customer_name("  Élon\u00a0\u2003Valiyev  ")
    decomposed = canonicalize_customer_name("E\u0301lon Valiyev")

    assert composed.as_crypto_plaintext() == "Élon Valiyev"
    assert decomposed.as_crypto_plaintext() == "E\u0301lon Valiyev"
    assert composed.as_crypto_plaintext() != decomposed.as_crypto_plaintext()


@pytest.mark.parametrize("category_value", ["A\x00B", "A\u200dB", "A\ue000B"])
def test_names_reject_control_format_and_private_use(category_value: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        canonicalize_customer_name(category_value)


def test_names_reject_surrogates_and_enforce_code_point_bounds() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        canonicalize_customer_name("A\ud800B")
    assert len(canonicalize_customer_name("A" * 100).as_crypto_plaintext()) == 100
    for raw in ("", " \t\n", "A" * 101):
        with pytest.raises(ValueError, match="1 to 100"):
            canonicalize_customer_name(raw)


def test_optional_middle_name_maps_blank_to_null() -> None:
    assert canonicalize_optional_customer_name(None) is None
    assert canonicalize_optional_customer_name("  \u2003 ") is None
    assert (
        canonicalize_optional_customer_name("  Qodir  o‘g‘li ").as_crypto_plaintext()
        == "Qodir o‘g‘li"
    )


def test_jshshir_requires_exactly_fourteen_ascii_digits() -> None:
    assert canonicalize_jshshir(" 12345678901234 ").as_crypto_plaintext() == (
        "12345678901234"
    )
    for raw in (
        "1234567890123",
        "123456789012345",
        "1234567890123A",
        "１２３４５６７８９０１２３４",
        "1234567 8901234",
    ):
        with pytest.raises(ValueError, match="14 ASCII digits"):
            canonicalize_jshshir(raw)


def test_document_number_ascii_uppercases_and_preserves_allowed_separators() -> None:
    number = canonicalize_customer_document_number("  aa-12 345  ")

    assert number.as_crypto_plaintext() == "AA-12 345"
    for raw in ("A123", "A" * 33, "AB_123", "AB\t123", "ß1234"):
        with pytest.raises(ValueError, match="invalid"):
            canonicalize_customer_document_number(raw)


def test_canonical_identity_and_sensitive_values_have_redacted_repr_and_str() -> None:
    identity = _canonical_identity()
    rendered = " ".join(
        (
            repr(identity),
            repr(identity.first_name),
            str(identity.first_name),
            repr(identity.jshshir),
            str(identity.jshshir),
            repr(identity.document_number),
            str(identity.document_number),
        )
    )

    for sensitive in ("Ali", "Valiyev", "12345678901234", "AA 1234567"):
        assert sensitive not in rendered
    assert "ID_CARD" in repr(identity)
    with pytest.raises(FrozenInstanceError):
        identity.middle_name = None  # type: ignore[misc]


def test_canonical_payload_serialization_is_exact_and_deterministic() -> None:
    identity = _canonical_identity()

    payload = serialize_customer_identity_payload(identity)

    assert CUSTOMER_IDENTITY_SCHEMA_VERSION == 1
    assert payload == (
        b'{"first_name":"Ali","last_name":"Valiyev",'
        b'"middle_name":"Qodir o\xe2\x80\x98g\xe2\x80\x98li",'
        b'"jshshir":"12345678901234","document_type":"ID_CARD",'
        b'"document_number":"AA 1234567"}'
    )
    assert serialize_customer_identity_payload(identity) == payload
    assert deserialize_customer_identity_payload(payload) == identity


def test_canonical_payload_preserves_middle_name_null() -> None:
    identity = _canonical_identity(middle_name=None)
    payload = serialize_customer_identity_payload(identity)

    assert b'"middle_name":null' in payload
    assert deserialize_customer_identity_payload(payload).middle_name is None


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        (
            b'{"first_name":"Ali","first_name":"Vali",'
            b'"last_name":"Valiyev","middle_name":null,'
            b'"jshshir":"12345678901234","document_type":"ID_CARD",'
            b'"document_number":"AA 1234567"}'
        ),
        (
            b'{"last_name":"Valiyev","first_name":"Ali",'
            b'"middle_name":null,"jshshir":"12345678901234",'
            b'"document_type":"ID_CARD","document_number":"AA 1234567"}'
        ),
        (
            b'{"first_name":" Ali ","last_name":"Valiyev",'
            b'"middle_name":null,"jshshir":"12345678901234",'
            b'"document_type":"ID_CARD","document_number":"AA 1234567"}'
        ),
        (
            b'{"first_name":"Ali", "last_name":"Valiyev",'
            b'"middle_name":null,"jshshir":"12345678901234",'
            b'"document_type":"ID_CARD","document_number":"AA 1234567"}'
        ),
        (
            b'{"first_name":"Ali","last_name":"Valiyev",'
            b'"middle_name":null,"jshshir":12345678901234,'
            b'"document_type":"ID_CARD","document_number":"AA 1234567"}'
        ),
        (
            b'{"first_name":"Ali","last_name":"Valiyev",'
            b'"middle_name":null,"jshshir":"12345678901234",'
            b'"document_type":"ID_CARD"}'
        ),
        (
            b'{"first_name":"Ali","last_name":"Valiyev",'
            b'"middle_name":null,"jshshir":"12345678901234",'
            b'"document_type":"ID_CARD","document_number":"AA 1234567",'
            b'"extra":"forbidden"}'
        ),
    ],
)
def test_payload_deserialization_fails_closed_for_noncanonical_inputs(
    payload: bytes,
) -> None:
    with pytest.raises(
        CustomerIdentityPayloadError,
        match="Customer identity payload is invalid",
    ) as caught:
        deserialize_customer_identity_payload(payload)

    assert caught.value.__cause__ is None
    assert "Ali" not in repr(caught.value)


@pytest.mark.parametrize("schema_version", [0, 2, -1, True])
def test_payload_rejects_unsupported_schema_version(schema_version: int) -> None:
    identity = _canonical_identity()

    with pytest.raises(CustomerIdentityPayloadError):
        serialize_customer_identity_payload(
            identity,
            schema_version=schema_version,
        )
    with pytest.raises(CustomerIdentityPayloadError):
        deserialize_customer_identity_payload(
            serialize_customer_identity_payload(identity),
            schema_version=schema_version,
        )
