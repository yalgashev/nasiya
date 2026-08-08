"""Current complete DEBT_ACCEPTANCE offer gate for debt creation TX-B."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.debt.targeting import _LockedDebtTargetBeforeOffer, _validate_before_offer
from app.offers.contracts import OfferVersion
from app.offers.enums import OfferPurpose
from app.offers.policy import OfferVersionCompletenessPolicy
from app.offers.repository import SqlAlchemyCurrentOfferResolver


@dataclass(frozen=True, slots=True, repr=False)
class LockedDebtOfferGate:
    """No text/language snapshot; acceptance resolves its language later."""

    version: OfferVersion = field(repr=False)
    _locked_target: _LockedDebtTargetBeforeOffer = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedDebtOfferGate(version=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DebtOfferGateResult:
    error: ErrorCode | None
    locked_offer: LockedDebtOfferGate | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.error is None:
            if not isinstance(self.locked_offer, LockedDebtOfferGate):
                raise ValueError("Available debt offer requires locked state")
        elif (
            self.error is not ErrorCode.OFFER_UNAVAILABLE
            or self.locked_offer is not None
        ):
            raise ValueError("Debt offer gate result is invalid")

    def __repr__(self) -> str:
        return f"DebtOfferGateResult(error={self.error!r}, offer=<redacted>)"


def lock_current_complete_debt_offer(
    session: Session, *, locked_target: _LockedDebtTargetBeforeOffer
) -> DebtOfferGateResult:
    """Serialize with M9 offer switching and require all supported languages."""

    _validate_before_offer(session, locked_target)
    resolved = SqlAlchemyCurrentOfferResolver(
        session
    ).lock_current_version_with_all_texts(
        purpose=OfferPurpose.DEBT_ACCEPTANCE,
    )
    if resolved is None:
        return DebtOfferGateResult(error=ErrorCode.OFFER_UNAVAILABLE)
    current, texts = resolved
    completeness = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=current.id,
        variants=(text.variant for text in texts),
    )
    if not completeness.complete:
        return DebtOfferGateResult(error=ErrorCode.OFFER_UNAVAILABLE)
    return DebtOfferGateResult(
        error=None,
        locked_offer=LockedDebtOfferGate(
            version=current,
            _locked_target=locked_target,
            _session=session,
        ),
    )


def validate_locked_debt_offer(session: Session, token: object) -> LockedDebtOfferGate:
    if not isinstance(token, LockedDebtOfferGate):
        raise TypeError("locked_offer must come from debt offer gate")
    if token._session is not session:
        raise RuntimeError("locked offer belongs to a different session")
    return token
