from __future__ import annotations

import re
import unicodedata
from typing import Final

from app.customer_identity.contracts import (
    CanonicalCustomerIdentity,
    CustomerDocumentNumber,
    CustomerDocumentType,
    CustomerName,
    Jshshir,
    parse_customer_document_type,
)

_FORBIDDEN_NAME_CATEGORIES: Final = frozenset({"Cc", "Cf", "Co", "Cs"})
_JSHSHIR_PATTERN: Final = re.compile(r"[0-9]{14}", flags=re.ASCII)
_DOCUMENT_NUMBER_PATTERN: Final = re.compile(
    r"[A-Z0-9 -]{5,32}",
    flags=re.ASCII,
)
_ASCII_LOWER: Final = "abcdefghijklmnopqrstuvwxyz"
_ASCII_UPPER: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_UPPER_TRANSLATION: Final = str.maketrans(_ASCII_LOWER, _ASCII_UPPER)


def canonicalize_customer_name(value: str) -> CustomerName:
    if not isinstance(value, str):
        raise ValueError("Customer name must be a string")
    canonical = " ".join(value.split())
    if not 1 <= len(canonical) <= 100:
        raise ValueError("Customer name must be 1 to 100 characters")
    if any(
        unicodedata.category(character) in _FORBIDDEN_NAME_CATEGORIES
        for character in canonical
    ):
        raise ValueError("Customer name contains a forbidden character")
    return CustomerName(canonical)


def canonicalize_optional_customer_name(value: str | None) -> CustomerName | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Customer middle name must be a string or null")
    if not value.strip():
        return None
    return canonicalize_customer_name(value)


def canonicalize_jshshir(value: str) -> Jshshir:
    if not isinstance(value, str):
        raise ValueError("JSHSHIR must be a string")
    canonical = value.strip()
    if _JSHSHIR_PATTERN.fullmatch(canonical) is None:
        raise ValueError("JSHSHIR must contain exactly 14 ASCII digits")
    return Jshshir(canonical)


def canonicalize_customer_document_number(value: str) -> CustomerDocumentNumber:
    if not isinstance(value, str):
        raise ValueError("Customer document number must be a string")
    canonical = value.strip().translate(_ASCII_UPPER_TRANSLATION)
    if _DOCUMENT_NUMBER_PATTERN.fullmatch(canonical) is None:
        raise ValueError("Customer document number is invalid")
    return CustomerDocumentNumber(canonical)


def canonicalize_customer_identity(
    *,
    first_name: str,
    last_name: str,
    middle_name: str | None,
    jshshir: str,
    document_type: CustomerDocumentType | str,
    document_number: str,
) -> CanonicalCustomerIdentity:
    parsed_document_type = (
        document_type
        if isinstance(document_type, CustomerDocumentType)
        else parse_customer_document_type(document_type)
    )
    return CanonicalCustomerIdentity(
        first_name=canonicalize_customer_name(first_name),
        last_name=canonicalize_customer_name(last_name),
        middle_name=canonicalize_optional_customer_name(middle_name),
        jshshir=canonicalize_jshshir(jshshir),
        document_type=parsed_document_type,
        document_number=canonicalize_customer_document_number(document_number),
    )
