from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.contracts import (
    OfferTextVariant,
    require_unique_offer_text_variants,
)
from app.offers.enums import OfferLanguage

VERSION_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_VERSION_ID = UUID("22222222-2222-4222-8222-222222222222")


def _variant(
    *,
    version_id: UUID = VERSION_ID,
    language: OfferLanguage = OfferLanguage.UZ_LATN,
    title: str = "Taklif",
    body: str = "Taklif matni",
) -> OfferTextVariant:
    canonical = canonicalize_offer_text(title=title, body=body)
    return OfferTextVariant(
        offer_version_id=version_id,
        language=language,
        title=canonical.title,
        body=canonical.body,
        content_hash=compute_offer_content_hash(canonical),
    )


def test_offer_text_variant_preserves_exact_canonical_content_and_hash() -> None:
    variant = _variant(title=" Sarlavha ", body="Qator 1\nQator 2")
    canonical = canonicalize_offer_text(
        title=variant.title,
        body=variant.body,
    )

    assert variant.offer_version_id == VERSION_ID
    assert variant.language is OfferLanguage.UZ_LATN
    assert variant.title == " Sarlavha "
    assert variant.body == "Qator 1\nQator 2"
    assert variant.content_hash == compute_offer_content_hash(canonical)


def test_offer_text_variant_rejects_noncanonical_line_endings() -> None:
    canonical = canonicalize_offer_text(title="Title", body="Line\r\nTwo")

    with pytest.raises(ValueError, match="must already be canonical"):
        OfferTextVariant(
            offer_version_id=VERSION_ID,
            language=OfferLanguage.RU,
            title="Title",
            body="Line\r\nTwo",
            content_hash=compute_offer_content_hash(canonical),
        )


@pytest.mark.parametrize(
    "content_hash",
    ["0" * 64, "A" * 64, "", "not-a-hash"],
)
def test_offer_text_variant_rejects_nonmatching_hash(
    content_hash: str,
) -> None:
    with pytest.raises(ValueError, match="does not match canonical text"):
        OfferTextVariant(
            offer_version_id=VERSION_ID,
            language=OfferLanguage.UZ_CYRL,
            title="Title",
            body="Body",
            content_hash=content_hash,
        )


def test_one_version_accepts_each_language_once() -> None:
    variants = (
        _variant(language=OfferLanguage.UZ_LATN),
        _variant(language=OfferLanguage.UZ_CYRL),
        _variant(language=OfferLanguage.RU),
    )

    assert require_unique_offer_text_variants(variants) == variants


def test_duplicate_language_in_one_version_is_rejected() -> None:
    first = _variant()
    duplicate = _variant(title="Boshqa", body="Boshqa matn")

    with pytest.raises(ValueError, match="already has this language"):
        require_unique_offer_text_variants((first, duplicate))


def test_same_language_is_valid_for_different_versions() -> None:
    variants = (
        _variant(version_id=VERSION_ID),
        _variant(version_id=OTHER_VERSION_ID),
    )

    assert require_unique_offer_text_variants(variants) == variants


def test_offer_text_variant_is_immutable_and_repr_omits_legal_content() -> None:
    variant = _variant(
        title="SECRET LEGAL TITLE",
        body="SECRET LEGAL BODY",
    )

    with pytest.raises(FrozenInstanceError):
        variant.body = "mutated"

    rendered = repr(variant)
    assert "SECRET" not in rendered
    assert variant.content_hash in rendered
