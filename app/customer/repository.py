from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.repository import (
    _LockedActorTargetUsers,
    _validate_locked_actor_target_users,
)
from app.customer.models import (
    CUSTOMER_ONBOARDING_STATUS_ACTIVE,
    CUSTOMER_ONBOARDING_STATUS_DRAFT,
    Customer,
)
from app.customer.ports import (
    CustomerActivationTransitionOutcome,
    CustomerActivationTransitionResult,
    CustomerLifecycleState,
    CustomerLifecycleStatus,
    transition_customer_to_active,
)


@dataclass(frozen=True, slots=True, repr=False)
class _LockedActiveTargetCustomer:
    customer: Customer
    locked_users: _LockedActorTargetUsers
    _session: Session

    def __repr__(self) -> str:
        return "_LockedActiveTargetCustomer(customer=<redacted>)"


def get_customer_by_user_id(session: Session, user_id: UUID) -> Customer | None:
    statement = select(Customer).where(Customer.user_id == user_id)
    return session.execute(statement).scalar_one_or_none()


def load_existing_own_customer(
    session: Session,
    *,
    actor_user_id: UUID,
) -> Customer | None:
    _validate_actor_user_id(actor_user_id)
    statement = select(Customer).where(Customer.user_id == actor_user_id)
    return session.scalar(statement)


def lock_existing_own_customer_for_update(
    session: Session,
    *,
    actor_user_id: UUID,
) -> Customer | None:
    _validate_actor_user_id(actor_user_id)
    statement = (
        select(Customer).where(Customer.user_id == actor_user_id).with_for_update()
    )
    return session.scalar(statement)


def lock_active_customer_for_target_user(
    session: Session,
    *,
    locked_users: _LockedActorTargetUsers,
) -> _LockedActiveTargetCustomer | None:
    """Lock only the active Customer owned by the already-locked target User."""

    users = _validate_locked_actor_target_users(session, locked_users)
    statement = (
        select(Customer)
        .where(
            Customer.user_id == users.target.id,
            Customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        )
        .with_for_update()
    )
    customer = session.scalar(statement)
    if customer is None:
        return None
    return _LockedActiveTargetCustomer(
        customer=customer,
        locked_users=users,
        _session=session,
    )


def _validate_locked_active_target_customer(
    session: Session,
    locked_customer: object,
) -> _LockedActiveTargetCustomer:
    if not isinstance(locked_customer, _LockedActiveTargetCustomer):
        raise TypeError(
            "locked_customer must come from lock_active_customer_for_target_user"
        )
    if locked_customer._session is not session:
        raise RuntimeError(
            "locked_customer was created by a different SQLAlchemy session"
        )
    _validate_locked_actor_target_users(session, locked_customer.locked_users)
    return locked_customer


def get_existing_own_customer_status(
    session: Session,
    *,
    actor_user_id: UUID,
) -> CustomerLifecycleStatus | None:
    customer = load_existing_own_customer(
        session,
        actor_user_id=actor_user_id,
    )
    if customer is None:
        return None
    return _customer_lifecycle_state(customer).status


def transition_existing_own_customer_draft_to_active(
    session: Session,
    *,
    actor_user_id: UUID,
    expected_status: CustomerLifecycleStatus,
    now: datetime,
) -> CustomerActivationTransitionResult:
    if expected_status is not CustomerLifecycleStatus.DRAFT:
        raise ValueError("Customer activation expected status must be draft")
    current_time = _as_utc(now)
    customer = lock_existing_own_customer_for_update(
        session,
        actor_user_id=actor_user_id,
    )
    if customer is None:
        return CustomerActivationTransitionResult(
            outcome=CustomerActivationTransitionOutcome.MISSING
        )

    transition = transition_customer_to_active(
        _customer_lifecycle_state(customer),
        now=current_time,
    )
    if transition.outcome is not CustomerActivationTransitionOutcome.ACTIVATED:
        return transition
    if transition.state is None:
        raise RuntimeError("Customer activation transition state is unavailable")

    customer.onboarding_status = transition.state.status.value
    customer.activated_at = transition.state.activated_at
    customer.updated_at = transition.state.updated_at
    session.add(customer)
    session.flush()
    return transition


def load_existing_own_customer_draft_for_update(
    session: Session,
    *,
    actor_user_id: UUID,
) -> Customer | None:
    _validate_actor_user_id(actor_user_id)
    statement = (
        select(Customer)
        .where(
            Customer.user_id == actor_user_id,
            Customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT,
        )
        .with_for_update()
    )
    return session.scalar(statement)


def create_customer_draft_if_missing(
    session: Session,
    user_id: UUID,
    now: datetime,
) -> Customer:
    current_time = _as_utc(now)
    insert_statement = insert(Customer).values(
        user_id=user_id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
        created_at=current_time,
        updated_at=current_time,
    )
    session.execute(
        insert_statement.on_conflict_do_nothing(index_elements=[Customer.user_id])
    )
    customer = get_customer_by_user_id(session, user_id)
    if customer is None:
        raise LookupError("customer draft was not created or found")
    return customer


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Customer timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _validate_actor_user_id(actor_user_id: UUID) -> None:
    if not isinstance(actor_user_id, UUID):
        raise TypeError("Actor user id must be a UUID")


def _customer_lifecycle_state(customer: Customer) -> CustomerLifecycleState:
    try:
        status = CustomerLifecycleStatus(customer.onboarding_status)
    except ValueError:
        raise ValueError("Stored customer lifecycle status is invalid") from None
    return CustomerLifecycleState(
        status=status,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
        activated_at=customer.activated_at,
    )
