from typing import Final

from app.offers.enums import OfferStatus

_ALLOWED_STATUS_TRANSITIONS: Final[frozenset[tuple[OfferStatus, OfferStatus]]] = (
    frozenset(
        {
            (OfferStatus.DRAFT, OfferStatus.APPROVED),
            (OfferStatus.APPROVED, OfferStatus.CURRENT),
        }
    )
)


def is_offer_status_transition_allowed(
    source: OfferStatus,
    target: OfferStatus,
) -> bool:
    return (source, target) in _ALLOWED_STATUS_TRANSITIONS


def require_offer_status_transition(
    source: OfferStatus,
    target: OfferStatus,
) -> OfferStatus:
    if not is_offer_status_transition_allowed(source, target):
        raise ValueError("Offer status transition is not allowed")
    return target


def require_atomic_current_replacement_demotion(
    source: OfferStatus,
    target: OfferStatus,
) -> OfferStatus:
    if source is not OfferStatus.CURRENT or target is not OfferStatus.APPROVED:
        raise ValueError("Current demotion requires atomic replacement")
    return target
