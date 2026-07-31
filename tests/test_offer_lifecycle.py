import itertools

import pytest

from app.offers.enums import OfferStatus, parse_offer_status
from app.offers.lifecycle import (
    is_offer_status_transition_allowed,
    require_atomic_current_replacement_demotion,
    require_offer_status_transition,
)

_ORDINARY_ALLOWED = {
    (OfferStatus.DRAFT, OfferStatus.APPROVED),
    (OfferStatus.APPROVED, OfferStatus.CURRENT),
}


def test_offer_status_values_are_stable_and_closed() -> None:
    assert tuple(status.value for status in OfferStatus) == (
        "DRAFT",
        "APPROVED",
        "CURRENT",
    )
    assert tuple(parse_offer_status(status.value) for status in OfferStatus) == tuple(
        OfferStatus
    )

    with pytest.raises(ValueError, match="Unknown offer status"):
        parse_offer_status("RETIRED")


@pytest.mark.parametrize(
    ("source", "target"),
    list(itertools.product(OfferStatus, repeat=2)),
)
def test_ordinary_transition_table_is_exact(
    source: OfferStatus,
    target: OfferStatus,
) -> None:
    expected = (source, target) in _ORDINARY_ALLOWED
    assert is_offer_status_transition_allowed(source, target) is expected

    if expected:
        assert require_offer_status_transition(source, target) is target
    else:
        with pytest.raises(
            ValueError,
            match="Offer status transition is not allowed",
        ):
            require_offer_status_transition(source, target)


def test_current_to_approved_requires_atomic_replacement_boundary() -> None:
    assert not is_offer_status_transition_allowed(
        OfferStatus.CURRENT,
        OfferStatus.APPROVED,
    )
    assert (
        require_atomic_current_replacement_demotion(
            OfferStatus.CURRENT,
            OfferStatus.APPROVED,
        )
        is OfferStatus.APPROVED
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        pair
        for pair in itertools.product(OfferStatus, repeat=2)
        if pair != (OfferStatus.CURRENT, OfferStatus.APPROVED)
    ],
)
def test_atomic_replacement_demotion_rejects_every_other_pair(
    source: OfferStatus,
    target: OfferStatus,
) -> None:
    with pytest.raises(
        ValueError,
        match="Current demotion requires atomic replacement",
    ):
        require_atomic_current_replacement_demotion(source, target)
