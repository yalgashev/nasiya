import pytest

from app.offers.enums import (
    M9_RUNTIME_ACCEPTANCE_PURPOSE,
    OfferPurpose,
    parse_offer_purpose,
    require_m9_runtime_acceptance_purpose,
)


def test_offer_purpose_values_are_stable_and_closed() -> None:
    assert tuple(purpose.value for purpose in OfferPurpose) == (
        "REGISTRATION",
        "DEBT_ACCEPTANCE",
    )
    assert str(OfferPurpose.REGISTRATION) == "REGISTRATION"
    assert str(OfferPurpose.DEBT_ACCEPTANCE) == "DEBT_ACCEPTANCE"


@pytest.mark.parametrize("purpose", list(OfferPurpose))
def test_offer_purpose_parses_exact_persisted_value(
    purpose: OfferPurpose,
) -> None:
    assert parse_offer_purpose(purpose.value) is purpose


@pytest.mark.parametrize(
    "value",
    ["registration", "DEBT", " DEBT_ACCEPTANCE", "DEBT_ACCEPTANCE ", ""],
)
def test_offer_purpose_rejects_unknown_or_noncanonical_value(value: str) -> None:
    with pytest.raises(ValueError, match="Unknown offer purpose"):
        parse_offer_purpose(value)


def test_m9_runtime_acceptance_is_registration_only() -> None:
    assert M9_RUNTIME_ACCEPTANCE_PURPOSE is OfferPurpose.REGISTRATION
    assert (
        require_m9_runtime_acceptance_purpose(OfferPurpose.REGISTRATION)
        is OfferPurpose.REGISTRATION
    )

    with pytest.raises(
        ValueError,
        match="M9 runtime acceptance supports REGISTRATION only",
    ):
        require_m9_runtime_acceptance_purpose(OfferPurpose.DEBT_ACCEPTANCE)
