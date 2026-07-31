from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.offers.contracts import RegistrationOfferAcceptance
from app.offers.enums import OfferLanguage, OfferPurpose

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
VERSION_ID = UUID("22222222-2222-4222-8222-222222222222")
TEXT_ID = UUID("33333333-3333-4333-8333-333333333333")
ACCEPTED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
CONTENT_HASH = "a" * 64


def _acceptance(**overrides: object) -> RegistrationOfferAcceptance:
    values: dict[str, object] = {
        "user_id": USER_ID,
        "offer_version_id": VERSION_ID,
        "offer_text_id": TEXT_ID,
        "purpose": OfferPurpose.REGISTRATION,
        "language": OfferLanguage.UZ_LATN,
        "version_number": 3,
        "content_hash": CONTENT_HASH,
        "accepted_at": ACCEPTED_AT,
        "user_agent": "Mozilla Test Browser",
    }
    values.update(overrides)
    return RegistrationOfferAcceptance(**values)


def test_registration_acceptance_captures_exact_evidence_fields() -> None:
    acceptance = _acceptance()

    assert acceptance.user_id == USER_ID
    assert acceptance.offer_version_id == VERSION_ID
    assert acceptance.offer_text_id == TEXT_ID
    assert acceptance.purpose is OfferPurpose.REGISTRATION
    assert acceptance.language is OfferLanguage.UZ_LATN
    assert acceptance.version_number == 3
    assert acceptance.content_hash == CONTENT_HASH
    assert acceptance.accepted_at == ACCEPTED_AT
    assert acceptance.user_agent == "Mozilla Test Browser"


def test_acceptance_is_immutable() -> None:
    acceptance = _acceptance()

    with pytest.raises(FrozenInstanceError):
        acceptance.content_hash = "b" * 64


def test_m9_acceptance_rejects_debt_purpose() -> None:
    with pytest.raises(
        ValueError,
        match="M9 runtime acceptance supports REGISTRATION only",
    ):
        _acceptance(purpose=OfferPurpose.DEBT_ACCEPTANCE)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("user_id", "not-a-uuid", "user_id must be a UUID"),
        ("offer_version_id", "not-a-uuid", "offer_version_id must be a UUID"),
        ("offer_text_id", "not-a-uuid", "offer_text_id must be a UUID"),
        ("purpose", "REGISTRATION", "Offer purpose is invalid"),
        ("language", "UZ_LATN", "Offer language is invalid"),
        ("version_number", 0, "version number must be positive"),
        ("version_number", True, "version number must be positive"),
        ("content_hash", "A" * 64, "content hash is invalid"),
        ("content_hash", "a" * 63, "content hash is invalid"),
    ],
)
def test_acceptance_rejects_invalid_evidence_fields(
    field_name: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _acceptance(**{field_name: value})


def test_accepted_at_must_be_aware_and_is_normalized_to_utc() -> None:
    with pytest.raises(ValueError, match="accepted_at must be timezone-aware"):
        _acceptance(accepted_at=ACCEPTED_AT.replace(tzinfo=None))

    plus_five = timezone(timedelta(hours=5))
    acceptance = _acceptance(accepted_at=datetime(2026, 7, 31, 17, 0, tzinfo=plus_five))
    assert acceptance.accepted_at == ACCEPTED_AT
    assert acceptance.accepted_at.tzinfo is UTC


@pytest.mark.parametrize(
    "user_agent",
    [
        "",
        " " * 2,
        "Mozilla  Browser",
        " Mozilla",
        "Mozilla ",
        "Mozilla\nBrowser",
        "Mozilla\u200bBrowser",
        "A" * 513,
    ],
)
def test_acceptance_requires_bounded_normalized_user_agent(
    user_agent: str,
) -> None:
    with pytest.raises(ValueError, match="user agent|User agent"):
        _acceptance(user_agent=user_agent)


def test_acceptance_allows_none_and_512_character_user_agent() -> None:
    assert _acceptance(user_agent=None).user_agent is None
    assert _acceptance(user_agent="A" * 512).user_agent == "A" * 512


def test_acceptance_repr_redacts_user_agent() -> None:
    acceptance = _acceptance(user_agent="SECRET RAW USER AGENT")

    rendered = repr(acceptance)

    assert "SECRET" not in rendered
    assert "user_agent=<redacted>" in rendered
