import pytest

from app.offers.enums import OfferLanguage, parse_offer_language


def test_offer_language_values_are_stable_and_closed() -> None:
    assert tuple(language.value for language in OfferLanguage) == (
        "UZ_LATN",
        "UZ_CYRL",
        "RU",
    )


@pytest.mark.parametrize("language", list(OfferLanguage))
def test_offer_language_parsing_and_serialization_are_exact(
    language: OfferLanguage,
) -> None:
    assert parse_offer_language(language.value) is language
    assert str(language) == language.value


@pytest.mark.parametrize(
    "value",
    [
        "uz",
        "ru",
        "uz-Latn",
        "uz-Cyrl",
        "UZ",
        "UZ_LATN ",
        " UZ_CYRL",
        "",
    ],
)
def test_offer_language_rejects_ui_locale_and_noncanonical_values(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="Unknown offer language"):
        parse_offer_language(value)
