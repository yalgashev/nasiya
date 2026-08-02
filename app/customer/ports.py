from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID


class CustomerLifecycleStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class CustomerLifecycleState:
    status: CustomerLifecycleStatus
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CustomerLifecycleStatus):
            raise TypeError("Customer lifecycle status is invalid")
        created_at = _as_utc(self.created_at)
        updated_at = _as_utc(self.updated_at)
        activated_at = None if self.activated_at is None else _as_utc(self.activated_at)
        if updated_at < created_at:
            raise ValueError("Customer lifecycle timestamps are invalid")
        if self.status is CustomerLifecycleStatus.DRAFT and activated_at is not None:
            raise ValueError("Draft customer cannot have activation time")
        if self.status is CustomerLifecycleStatus.ACTIVE and activated_at is None:
            raise ValueError("Active customer requires activation time")
        if activated_at is not None and (
            activated_at < created_at or updated_at < activated_at
        ):
            raise ValueError("Customer activation timestamps are invalid")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "activated_at", activated_at)


class CustomerActivationTransitionOutcome(StrEnum):
    ACTIVATED = "ACTIVATED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class CustomerActivationTransitionResult:
    outcome: CustomerActivationTransitionOutcome
    state: CustomerLifecycleState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, CustomerActivationTransitionOutcome):
            raise TypeError("Customer activation transition outcome is invalid")
        if self.state is not None and not isinstance(
            self.state, CustomerLifecycleState
        ):
            raise TypeError("Customer lifecycle state is invalid")
        if self.outcome is CustomerActivationTransitionOutcome.MISSING:
            if self.state is not None:
                raise ValueError("Missing customer cannot have lifecycle state")
            return
        if (
            self.state is None
            or self.state.status is not CustomerLifecycleStatus.ACTIVE
        ):
            raise ValueError("Activation result requires active customer state")


def transition_customer_to_active(
    state: CustomerLifecycleState | None,
    *,
    now: datetime,
) -> CustomerActivationTransitionResult:
    current_time = _as_utc(now)
    if state is None:
        return CustomerActivationTransitionResult(
            outcome=CustomerActivationTransitionOutcome.MISSING
        )
    if not isinstance(state, CustomerLifecycleState):
        raise TypeError("Customer lifecycle state is invalid")
    if state.status is CustomerLifecycleStatus.ACTIVE:
        return CustomerActivationTransitionResult(
            outcome=CustomerActivationTransitionOutcome.ALREADY_ACTIVE,
            state=state,
        )
    if current_time < state.updated_at:
        raise ValueError("Customer activation time is invalid")
    active_state = CustomerLifecycleState(
        status=CustomerLifecycleStatus.ACTIVE,
        created_at=state.created_at,
        updated_at=current_time,
        activated_at=current_time,
    )
    return CustomerActivationTransitionResult(
        outcome=CustomerActivationTransitionOutcome.ACTIVATED,
        state=active_state,
    )


@dataclass(frozen=True, slots=True, repr=False)
class OwnCustomerLifecycle:
    customer_id: UUID = field(repr=False)
    state: CustomerLifecycleState

    def __post_init__(self) -> None:
        if not isinstance(self.customer_id, UUID):
            raise TypeError("Own customer identity is invalid")
        if not isinstance(self.state, CustomerLifecycleState):
            raise TypeError("Customer lifecycle state is invalid")

    def __repr__(self) -> str:
        return f"OwnCustomerLifecycle(customer_id=<redacted>, state={self.state!r})"


@runtime_checkable
class OwnCustomerLifecyclePort(Protocol):
    def load_existing_own_customer(
        self,
        *,
        actor_user_id: UUID,
    ) -> OwnCustomerLifecycle | None: ...

    def lock_existing_own_customer(
        self,
        *,
        actor_user_id: UUID,
    ) -> OwnCustomerLifecycle | None: ...

    def transition_existing_own_customer_draft_to_active(
        self,
        *,
        actor_user_id: UUID,
        expected_status: CustomerLifecycleStatus,
        now: datetime,
    ) -> CustomerActivationTransitionResult: ...


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Customer lifecycle timestamp is invalid")
    return value.astimezone(UTC)
