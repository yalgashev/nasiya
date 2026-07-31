from uuid import UUID

import pytest

from app.auth.error_codes import ErrorCode, get_error_http_status
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.contracts import OfferTextVariant
from app.offers.enums import OfferLanguage
from app.offers.policy import OfferVersionCompletenessPolicy

VERSION_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_VERSION_ID = UUID("22222222-2222-4222-8222-222222222222")


def _variant(
    language: OfferLanguage,
    *,
    version_id: UUID = VERSION_ID,
) -> OfferTextVariant:
    canonical = canonicalize_offer_text(
        title=f"{language.value} title",
        body=f"{language.value} body",
    )
    return OfferTextVariant(
        offer_version_id=version_id,
        language=language,
        title=canonical.title,
        body=canonical.body,
        content_hash=compute_offer_content_hash(canonical),
    )


def _unsafe_variant(
    *,
    title: str,
    body: str,
    content_hash: str,
) -> OfferTextVariant:
    variant = object.__new__(OfferTextVariant)
    object.__setattr__(variant, "offer_version_id", VERSION_ID)
    object.__setattr__(variant, "language", OfferLanguage.UZ_LATN)
    object.__setattr__(variant, "title", title)
    object.__setattr__(variant, "body", body)
    object.__setattr__(variant, "content_hash", content_hash)
    return variant


def test_three_exact_languages_are_complete() -> None:
    variants = tuple(_variant(language) for language in OfferLanguage)

    result = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=VERSION_ID,
        variants=variants,
    )

    assert result.complete is True
    assert result.error is None
    assert result.languages == tuple(OfferLanguage)
    assert result.missing_languages == ()


@pytest.mark.parametrize("missing", list(OfferLanguage))
def test_each_missing_language_is_offer_incomplete(
    missing: OfferLanguage,
) -> None:
    variants = tuple(
        _variant(language) for language in OfferLanguage if language is not missing
    )

    result = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=VERSION_ID,
        variants=variants,
    )

    assert result.complete is False
    assert result.error is ErrorCode.OFFER_INCOMPLETE
    assert result.missing_languages == (missing,)
    assert get_error_http_status(result.error) == 422


def test_duplicate_language_is_offer_incomplete() -> None:
    variants = (
        *(_variant(language) for language in OfferLanguage),
        _variant(OfferLanguage.UZ_LATN),
    )

    result = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=VERSION_ID,
        variants=variants,
    )

    assert result.complete is False
    assert result.error is ErrorCode.OFFER_INCOMPLETE
    assert result.missing_languages == ()


def test_variant_from_another_version_is_offer_incomplete() -> None:
    variants = (
        _variant(OfferLanguage.UZ_LATN),
        _variant(OfferLanguage.UZ_CYRL),
        _variant(OfferLanguage.RU, version_id=OTHER_VERSION_ID),
    )

    result = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=VERSION_ID,
        variants=variants,
    )

    assert result.error is ErrorCode.OFFER_INCOMPLETE
    assert result.missing_languages == (OfferLanguage.RU,)


@pytest.mark.parametrize(
    "variant",
    [
        _unsafe_variant(title="", body="body", content_hash="a" * 64),
        _unsafe_variant(title="title", body="   ", content_hash="a" * 64),
        _unsafe_variant(title="title\r\n", body="body", content_hash="a" * 64),
        _unsafe_variant(title="title", body="body", content_hash="0" * 64),
    ],
)
def test_empty_noncanonical_or_hash_mismatched_variant_is_incomplete(
    variant: OfferTextVariant,
) -> None:
    variants = (
        variant,
        _variant(OfferLanguage.UZ_CYRL),
        _variant(OfferLanguage.RU),
    )

    result = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=VERSION_ID,
        variants=variants,
    )

    assert result.error is ErrorCode.OFFER_INCOMPLETE
    assert result.missing_languages == (OfferLanguage.UZ_LATN,)
