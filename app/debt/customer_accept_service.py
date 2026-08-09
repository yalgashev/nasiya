"""Atomic own-customer acceptance of one pending M13 Debt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtAcceptedAuditPayload,
)
from app.audit.repository import append_audit_event
from app.auth.error_codes import ErrorCode
from app.debt.acceptance_repository import (
    append_locked_debt_acceptance,
    get_locked_debt_acceptance_replay,
)
from app.debt.business_time import tashkent_business_date, validate_acceptance_due_date
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.customer_decision_targeting import (
    discover_own_customer_debt,
    lock_customer_debt_after_offer,
    lock_customer_debt_offer,
    lock_customer_debt_predecessors,
    locked_customer_debt_customer,
    read_discovered_debt_status,
)
from app.debt.enums import DebtStatus
from app.debt.expiry_service import expire_locked_pending_debt_inline
from app.debt.overdue_ports import LockedCustomerGlobalHardBlockReadPort
from app.debt.repository import (
    LockedCustomerHardBlockScope,
    debt_aggregate_from_row,
    locked_customer_global_hard_block_reader_factory,
    mark_locked_customer_hard_block_scope,
    update_locked_debt,
)
from app.debt.values import (
    CustomerId,
    DebtId,
    DebtRevision,
)
from app.offers.contracts import DebtOfferAcceptanceStaleError
from app.offers.enums import OfferLanguage
from app.shop_customer.enums import ShopCustomerListStatus

__all__ = (
    "AcceptCustomerDebtCommand",
    "AcceptCustomerDebtResult",
    "CustomerDebtAcceptOutcome",
    "accept_own_customer_debt",
)


class CustomerDebtAcceptOutcome(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True, repr=False)
class AcceptCustomerDebtCommand:
    debt_id: DebtId = field(repr=False)
    expected_revision: DebtRevision
    language: OfferLanguage
    displayed_offer_text_id: UUID = field(repr=False)
    now: datetime
    raw_user_agent: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Accept debt identity is invalid")
        if not isinstance(self.expected_revision, DebtRevision):
            raise ValueError("Accept debt revision is invalid")
        if not isinstance(self.language, OfferLanguage):
            raise ValueError("Accept debt language is invalid")
        if not isinstance(self.displayed_offer_text_id, UUID):
            raise ValueError("Accept displayed offer text is invalid")
        if self.raw_user_agent is not None and not isinstance(self.raw_user_agent, str):
            raise ValueError("Accept debt user agent source is invalid")
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("Accept debt time must be timezone-aware")
        object.__setattr__(self, "now", self.now.astimezone(UTC))

    def __repr__(self) -> str:
        return (
            "AcceptCustomerDebtCommand(debt_id=<redacted>, "
            f"expected_revision={self.expected_revision.value!r}, "
            f"language={self.language.value!r}, displayed_offer=<redacted>, "
            f"now={self.now!r}, raw_user_agent=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class AcceptCustomerDebtResult:
    outcome: CustomerDebtAcceptOutcome | None
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(self.outcome, CustomerDebtAcceptOutcome):
            raise ValueError("Accept customer debt result is invalid")
        allowed_errors = {
            ErrorCode.DEBT_UNAVAILABLE,
            ErrorCode.SHOP_SUSPENDED,
            ErrorCode.OFFER_UNAVAILABLE,
            ErrorCode.OFFER_CHANGED,
            ErrorCode.CUSTOMER_BLACKLISTED,
            ErrorCode.CUSTOMER_RATING_BLOCKED,
            ErrorCode.DEBT_NOT_PENDING,
            ErrorCode.DEBT_EXPIRED,
        }
        if self.error is not None and self.error not in allowed_errors:
            raise ValueError("Accept customer debt error is invalid")


def accept_own_customer_debt(
    session: Session,
    *,
    authority: CustomerDebtAuthority | None,
    command: AcceptCustomerDebtCommand,
    global_hard_block_reader: LockedCustomerGlobalHardBlockReadPort | None = None,
    hard_block_clock: Callable[[], datetime] | None = None,
    hard_block_reader_factory: Callable[
        [Session, LockedCustomerHardBlockScope],
        LockedCustomerGlobalHardBlockReadPort,
    ] = locked_customer_global_hard_block_reader_factory,
) -> AcceptCustomerDebtResult:
    """Append evidence, activate Debt, and audit without owning the Session."""

    if not isinstance(command, AcceptCustomerDebtCommand):
        raise TypeError("command must be an AcceptCustomerDebtCommand")
    clock = hard_block_clock or _utc_now
    if not callable(clock):
        raise TypeError("hard_block_clock must be callable")
    if authority is None:
        return _failure(ErrorCode.DEBT_UNAVAILABLE)
    if not isinstance(authority, CustomerDebtAuthority):
        raise TypeError("authority must be a CustomerDebtAuthority")
    candidate = discover_own_customer_debt(
        session,
        authority=authority,
        debt_id=command.debt_id,
    )
    predecessors = lock_customer_debt_predecessors(
        session,
        authority=authority,
        candidate=candidate,
    )
    if predecessors.error is not None:
        return _failure(predecessors.error)
    assert predecessors.locked is not None

    observed_status = read_discovered_debt_status(session, locked=predecessors.locked)
    locked_offer = None
    if observed_status is DebtStatus.PENDING:
        offer_result = lock_customer_debt_offer(
            session,
            locked=predecessors.locked,
            language=command.language,
        )
        if offer_result.error is not None:
            return _failure(offer_result.error)
        locked_offer = offer_result.locked
        assert locked_offer is not None
    debt_result = lock_customer_debt_after_offer(
        session,
        locked=predecessors.locked,
        offer=locked_offer,
    )
    if debt_result.error is not None:
        return _failure(debt_result.error)
    assert debt_result.locked is not None
    locked_debt = debt_result.locked

    if locked_debt.row.status == DebtStatus.ACTIVE.value:
        replay = get_locked_debt_acceptance_replay(
            session,
            locked_debt=locked_debt,
            language=command.language,
            displayed_offer_text_id=command.displayed_offer_text_id,
            expected_revision=command.expected_revision,
        )
        if replay is not None:
            return AcceptCustomerDebtResult(outcome=CustomerDebtAcceptOutcome.REPLAY)
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    if locked_debt.row.status != DebtStatus.PENDING.value:
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    captured_now = _normalize_hard_block_now(clock())
    if expire_locked_pending_debt_inline(
        session,
        locked_debt=locked_debt,
        now=captured_now,
    ):
        return _failure(ErrorCode.DEBT_EXPIRED)
    if locked_debt.row.revision != command.expected_revision.value:
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    try:
        validate_acceptance_due_date(
            now=captured_now,
            due_date=locked_debt.row.due_date,
        )
    except ValueError:
        return _failure(ErrorCode.DEBT_EXPIRED)
    if (
        ShopCustomerListStatus(locked_debt.shop_customer.list_status)
        is ShopCustomerListStatus.BLACKLISTED
    ):
        return _failure(ErrorCode.CUSTOMER_BLACKLISTED)
    scope = mark_locked_customer_hard_block_scope(
        session,
        locked_customer=locked_customer_debt_customer(
            session, locked=predecessors.locked
        ),
    )
    if not callable(hard_block_reader_factory):
        raise TypeError("hard_block_reader_factory must be callable")
    reader = global_hard_block_reader or hard_block_reader_factory(
        session,
        scope,
    )
    if not isinstance(reader, LockedCustomerGlobalHardBlockReadPort):
        raise TypeError("reader must implement locked Customer hard-block port")
    if reader.read_global_hard_block(
        customer_id=CustomerId(authority.customer_id),
        as_of_business_date=tashkent_business_date(captured_now),
    ).is_blocked:
        return _failure(ErrorCode.CUSTOMER_RATING_BLOCKED)
    if locked_offer is None:
        raise RuntimeError("Pending acceptance is missing its locked offer")
    try:
        stored = append_locked_debt_acceptance(
            session,
            locked_debt=locked_debt,
            locked_offer=locked_offer,
            language=command.language,
            displayed_offer_text_id=command.displayed_offer_text_id,
            accepted_at=captured_now,
            raw_user_agent=command.raw_user_agent,
        )
    except DebtOfferAcceptanceStaleError:
        return _failure(ErrorCode.OFFER_CHANGED)
    transitioned = debt_aggregate_from_row(locked_debt.row).accept(now=captured_now)
    update_locked_debt(session, row=locked_debt.row, debt=transitioned)
    acceptance = stored.acceptance
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_ACCEPTED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=authority.user_id,
            object_type=AuditObjectType.DEBT,
            object_id=locked_debt.row.id,
            occurred_at=captured_now,
            candidate_metadata=DebtAcceptedAuditPayload(
                offer_version_number=acceptance.version_number,
                language=acceptance.language,
                content_hash=acceptance.content_hash,
            ).as_candidate_metadata(),
        ),
    )
    return AcceptCustomerDebtResult(outcome=CustomerDebtAcceptOutcome.ACCEPTED)


def _failure(error: ErrorCode) -> AcceptCustomerDebtResult:
    return AcceptCustomerDebtResult(outcome=None, error=error)


def _normalize_hard_block_now(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Hard-block clock must return an aware datetime")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
