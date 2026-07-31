from enum import StrEnum
from typing import Final


class OfferPurpose(StrEnum):
    REGISTRATION = "REGISTRATION"
    DEBT_ACCEPTANCE = "DEBT_ACCEPTANCE"


class OfferLanguage(StrEnum):
    UZ_LATN = "UZ_LATN"
    UZ_CYRL = "UZ_CYRL"
    RU = "RU"


class OfferStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    CURRENT = "CURRENT"


M9_RUNTIME_ACCEPTANCE_PURPOSE: Final = OfferPurpose.REGISTRATION


def parse_offer_purpose(value: str) -> OfferPurpose:
    try:
        return OfferPurpose(value)
    except ValueError:
        raise ValueError("Unknown offer purpose") from None


def parse_offer_language(value: str) -> OfferLanguage:
    try:
        return OfferLanguage(value)
    except ValueError:
        raise ValueError("Unknown offer language") from None


def parse_offer_status(value: str) -> OfferStatus:
    try:
        return OfferStatus(value)
    except ValueError:
        raise ValueError("Unknown offer status") from None


def require_m9_runtime_acceptance_purpose(
    purpose: OfferPurpose,
) -> OfferPurpose:
    if purpose is not M9_RUNTIME_ACCEPTANCE_PURPOSE:
        raise ValueError("M9 runtime acceptance supports REGISTRATION only")
    return purpose
