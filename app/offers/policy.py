from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from app.auth.error_codes import ErrorCode
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.contracts import OfferTextVariant
from app.offers.enums import OfferLanguage


@dataclass(frozen=True, slots=True)
class OfferVersionCompletenessResult:
    languages: tuple[OfferLanguage, ...]
    missing_languages: tuple[OfferLanguage, ...]
    error: ErrorCode | None

    @property
    def complete(self) -> bool:
        return self.error is None


class OfferVersionCompletenessPolicy:
    def evaluate(
        self,
        *,
        offer_version_id: UUID,
        variants: Iterable[OfferTextVariant],
    ) -> OfferVersionCompletenessResult:
        if not isinstance(offer_version_id, UUID):
            raise ValueError("Offer version identity must be a UUID")

        valid_languages: set[OfferLanguage] = set()
        invalid = False
        for variant in variants:
            if (
                not isinstance(variant, OfferTextVariant)
                or variant.offer_version_id != offer_version_id
                or variant.language in valid_languages
                or not _has_valid_content(variant)
            ):
                invalid = True
                continue
            valid_languages.add(variant.language)

        languages = tuple(
            language for language in OfferLanguage if language in valid_languages
        )
        missing = tuple(
            language for language in OfferLanguage if language not in valid_languages
        )
        error = ErrorCode.OFFER_INCOMPLETE if invalid or missing else None
        return OfferVersionCompletenessResult(
            languages=languages,
            missing_languages=missing,
            error=error,
        )


def _has_valid_content(variant: OfferTextVariant) -> bool:
    try:
        canonical = canonicalize_offer_text(
            title=variant.title,
            body=variant.body,
        )
    except (TypeError, ValueError):
        return False
    return (
        canonical.title == variant.title
        and canonical.body == variant.body
        and compute_offer_content_hash(canonical) == variant.content_hash
    )
