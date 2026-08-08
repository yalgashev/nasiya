"""Forward-ordered live locks for own-customer debt decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.values import DebtId
from app.offers.contracts import ResolvedCurrentOffer
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.policy import OfferVersionCompletenessPolicy
from app.offers.repository import SqlAlchemyCurrentOfferResolver
from app.shop.enums import ShopStatus
from app.shop.repository import _LockedShop, lock_shop_for_update
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer
from app.telegram.models import TelegramLink
from app.telegram.repository import (
    get_telegram_link_by_user_for_update,
    is_otp_eligible_telegram_link,
)

__all__ = (
    "CustomerDebtLockResult",
    "CustomerDebtOfferLockResult",
    "CustomerDebtPredecessorResult",
    "LockedCustomerDebt",
    "LockedCustomerDebtOffer",
    "discover_own_customer_debt",
    "lock_customer_debt_after_offer",
    "lock_customer_debt_offer",
    "lock_customer_debt_predecessors",
    "read_discovered_debt_status",
)


@dataclass(frozen=True, slots=True, repr=False)
class _DiscoveredOwnCustomerDebt:
    debt_id: UUID = field(repr=False)
    shop_customer_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)

    def __repr__(self) -> str:
        return "_DiscoveredOwnCustomerDebt(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _LockedCustomerDebtPredecessors:
    authority: CustomerDebtAuthority = field(repr=False)
    candidate: _DiscoveredOwnCustomerDebt = field(repr=False)
    locked_shop: _LockedShop = field(repr=False)
    user: User = field(repr=False)
    telegram_link: TelegramLink = field(repr=False)
    customer: Customer = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "_LockedCustomerDebtPredecessors(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedCustomerDebtOffer:
    resolved: ResolvedCurrentOffer = field(repr=False)
    _predecessors: _LockedCustomerDebtPredecessors = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedCustomerDebtOffer(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedCustomerDebt:
    row: Debt = field(repr=False)
    shop_customer: ShopCustomer = field(repr=False)
    _predecessors: _LockedCustomerDebtPredecessors = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedCustomerDebt(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtPredecessorResult:
    error: ErrorCode | None
    locked: _LockedCustomerDebtPredecessors | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(
            self.locked, _LockedCustomerDebtPredecessors
        ):
            raise ValueError("Customer debt predecessor result is invalid")
        allowed_errors = {
            None,
            ErrorCode.DEBT_UNAVAILABLE,
            ErrorCode.SHOP_SUSPENDED,
        }
        if self.error not in allowed_errors:
            raise ValueError("Customer debt predecessor error is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtOfferLockResult:
    error: ErrorCode | None
    locked: LockedCustomerDebtOffer | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(self.locked, LockedCustomerDebtOffer):
            raise ValueError("Customer debt offer result is invalid")
        if self.error not in {None, ErrorCode.OFFER_UNAVAILABLE}:
            raise ValueError("Customer debt offer error is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDebtLockResult:
    error: ErrorCode | None
    locked: LockedCustomerDebt | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(self.locked, LockedCustomerDebt):
            raise ValueError("Customer debt lock result is invalid")
        if self.error not in {None, ErrorCode.DEBT_UNAVAILABLE}:
            raise ValueError("Customer debt lock error is invalid")


def discover_own_customer_debt(
    session: Session,
    *,
    authority: CustomerDebtAuthority,
    debt_id: DebtId,
) -> _DiscoveredOwnCustomerDebt | None:
    """Non-locking discovery through Customer -> ShopCustomer -> Debt only."""

    _require_authority(authority)
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    statement = (
        select(Debt.id, ShopCustomer.id, ShopCustomer.shop_id, Customer.id)
        .select_from(Customer)
        .join(ShopCustomer, ShopCustomer.customer_id == Customer.id)
        .join(Debt, Debt.shop_customer_id == ShopCustomer.id)
        .where(
            Customer.id == authority.customer_id,
            Customer.user_id == authority.user_id,
            Debt.id == debt_id.as_uuid(),
        )
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        return None
    return _DiscoveredOwnCustomerDebt(
        debt_id=row[0],
        shop_customer_id=row[1],
        shop_id=row[2],
        customer_id=row[3],
    )


def lock_customer_debt_predecessors(
    session: Session,
    *,
    authority: CustomerDebtAuthority,
    candidate: _DiscoveredOwnCustomerDebt | None,
    allow_suspended_shop: bool = False,
) -> CustomerDebtPredecessorResult:
    """Lock active Shop -> User -> TelegramLink -> active own Customer."""

    _require_authority(authority)
    if not isinstance(allow_suspended_shop, bool):
        raise TypeError("allow_suspended_shop must be a bool")
    if candidate is None or candidate.customer_id != authority.customer_id:
        return CustomerDebtPredecessorResult(error=ErrorCode.DEBT_UNAVAILABLE)
    locked_shop = lock_shop_for_update(session, shop_id=ShopId(candidate.shop_id))
    if locked_shop is None:
        return CustomerDebtPredecessorResult(error=ErrorCode.DEBT_UNAVAILABLE)
    if locked_shop.shop.status != ShopStatus.ACTIVE.value and not allow_suspended_shop:
        return CustomerDebtPredecessorResult(error=ErrorCode.SHOP_SUSPENDED)
    user = session.scalar(
        select(User).where(User.id == authority.user_id).with_for_update()
    )
    if user is None or not user.is_active:
        return CustomerDebtPredecessorResult(error=ErrorCode.DEBT_UNAVAILABLE)
    telegram_link = get_telegram_link_by_user_for_update(session, user)
    if not is_otp_eligible_telegram_link(
        telegram_link, expected_user_id=authority.user_id
    ):
        return CustomerDebtPredecessorResult(error=ErrorCode.DEBT_UNAVAILABLE)
    assert telegram_link is not None
    customer = session.scalar(
        select(Customer)
        .where(
            Customer.id == authority.customer_id,
            Customer.user_id == authority.user_id,
            Customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        )
        .with_for_update()
    )
    if customer is None:
        return CustomerDebtPredecessorResult(error=ErrorCode.DEBT_UNAVAILABLE)
    return CustomerDebtPredecessorResult(
        error=None,
        locked=_LockedCustomerDebtPredecessors(
            authority=authority,
            candidate=candidate,
            locked_shop=locked_shop,
            user=user,
            telegram_link=telegram_link,
            customer=customer,
            _session=session,
        ),
    )


def read_discovered_debt_status(
    session: Session, *, locked: _LockedCustomerDebtPredecessors
) -> DebtStatus | None:
    predecessors = _validate_predecessors(session, locked)
    value = session.scalar(
        select(Debt.status).where(
            Debt.id == predecessors.candidate.debt_id,
            Debt.shop_customer_id == predecessors.candidate.shop_customer_id,
        )
    )
    return None if value is None else DebtStatus(value)


def lock_customer_debt_offer(
    session: Session,
    *,
    locked: _LockedCustomerDebtPredecessors,
    language: OfferLanguage,
) -> CustomerDebtOfferLockResult:
    predecessors = _validate_predecessors(session, locked)
    if not isinstance(language, OfferLanguage):
        raise TypeError("language must be an OfferLanguage")
    resolver = SqlAlchemyCurrentOfferResolver(session)
    resolved = resolver.resolve_current_debt_for_acceptance(language=language)
    if resolved is None:
        return CustomerDebtOfferLockResult(error=ErrorCode.OFFER_UNAVAILABLE)
    current = resolver.lock_current_version_with_all_texts(
        purpose=OfferPurpose.DEBT_ACCEPTANCE
    )
    if current is None or current[0].id != resolved.version.id:
        return CustomerDebtOfferLockResult(error=ErrorCode.OFFER_UNAVAILABLE)
    version, texts = current
    complete = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=version.id,
        variants=(text.variant for text in texts),
    )
    if not complete.complete:
        return CustomerDebtOfferLockResult(error=ErrorCode.OFFER_UNAVAILABLE)
    return CustomerDebtOfferLockResult(
        error=None,
        locked=LockedCustomerDebtOffer(
            resolved=resolved,
            _predecessors=predecessors,
            _session=session,
        ),
    )


def lock_customer_debt_after_offer(
    session: Session,
    *,
    locked: _LockedCustomerDebtPredecessors,
    offer: LockedCustomerDebtOffer | None,
) -> CustomerDebtLockResult:
    predecessors = _validate_predecessors(session, locked)
    if offer is not None:
        validated_offer = _validate_offer(session, offer)
        if validated_offer._predecessors is not predecessors:
            raise ValueError("Locked offer belongs to different predecessors")
    candidate = predecessors.candidate
    shop_customer = session.scalar(
        select(ShopCustomer)
        .where(
            ShopCustomer.id == candidate.shop_customer_id,
            ShopCustomer.shop_id == candidate.shop_id,
            ShopCustomer.customer_id == candidate.customer_id,
        )
        .with_for_update()
    )
    if shop_customer is None:
        return CustomerDebtLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    debt = session.scalar(
        select(Debt)
        .where(
            Debt.id == candidate.debt_id,
            Debt.shop_customer_id == shop_customer.id,
        )
        .with_for_update()
    )
    if debt is None:
        return CustomerDebtLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    return CustomerDebtLockResult(
        error=None,
        locked=LockedCustomerDebt(
            row=debt,
            shop_customer=shop_customer,
            _predecessors=predecessors,
            _session=session,
        ),
    )


def _validate_predecessors(
    session: Session, token: object
) -> _LockedCustomerDebtPredecessors:
    if not isinstance(token, _LockedCustomerDebtPredecessors):
        raise TypeError("locked predecessors must come from the customer resolver")
    if token._session is not session:
        raise RuntimeError("locked predecessors belong to another session")
    return token


def _validate_offer(session: Session, token: object) -> LockedCustomerDebtOffer:
    if not isinstance(token, LockedCustomerDebtOffer):
        raise TypeError("locked offer must come from the customer resolver")
    if token._session is not session:
        raise RuntimeError("locked offer belongs to another session")
    _validate_predecessors(session, token._predecessors)
    return token


def _validate_locked_customer_debt(
    session: Session, token: object
) -> LockedCustomerDebt:
    if not isinstance(token, LockedCustomerDebt):
        raise TypeError("locked debt must come from the customer resolver")
    if token._session is not session:
        raise RuntimeError("locked debt belongs to another session")
    _validate_predecessors(session, token._predecessors)
    return token


def _require_authority(authority: CustomerDebtAuthority) -> None:
    if not isinstance(authority, CustomerDebtAuthority):
        raise TypeError("authority must be a CustomerDebtAuthority")
