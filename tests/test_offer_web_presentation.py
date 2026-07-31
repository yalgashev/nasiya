from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.web_presentation import (
    OfferWebLanguage,
    get_offer_legal_language_tag,
    get_offer_web_copy,
)


def test_offer_web_copy_is_typed_immutable_and_has_only_two_ui_locales() -> None:
    assert tuple(OfferWebLanguage) == (
        OfferWebLanguage.UZ_LATN,
        OfferWebLanguage.RU,
    )
    assert {language.value for language in OfferWebLanguage} == {"uz", "ru"}

    for language in OfferWebLanguage:
        copy = get_offer_web_copy(language)
        assert copy.admin_list.heading
        assert copy.admin_create.heading
        assert copy.admin_detail.legal_texts_heading
        assert copy.registration.heading
        assert copy.account_registration_offer_link
        assert set(copy.purpose_labels) == set(OfferPurpose)
        assert set(copy.status_labels) == set(OfferStatus)
        assert set(copy.legal_language_labels) == set(OfferLanguage)
        with pytest.raises(FrozenInstanceError):
            copy.registration.heading = "mutated"  # type: ignore[misc]
        with pytest.raises(TypeError):
            copy.legal_language_labels[OfferLanguage.RU] = "mutated"  # type: ignore[index]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (OfferLanguage.UZ_LATN, "uz-Latn"),
        (OfferLanguage.UZ_CYRL, "uz-Cyrl"),
        (OfferLanguage.RU, "ru"),
    ],
)
def test_legal_language_tags_are_independent_from_ui_locale(
    language: OfferLanguage,
    expected: str,
) -> None:
    assert get_offer_legal_language_tag(language) == expected


def test_offer_templates_keep_legal_content_autoescaped_and_scriptless() -> None:
    template_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("app/templates/offers").glob("*.html"))
    )

    assert "|safe" not in template_source
    assert "<script" not in template_source.casefold()
    assert "<style" not in template_source.casefold()
    assert "style=" not in template_source.casefold()
