from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User


@dataclass(frozen=True, slots=True, repr=False)
class _LockedActorTargetUsers:
    actor: User
    target: User
    _session: Session

    def __repr__(self) -> str:
        return "_LockedActorTargetUsers(actor=<redacted>, target=<redacted>)"


def get_by_phone(session: Session, normalized_phone: str) -> User | None:
    statement = select(User).where(User.phone == normalized_phone)
    return session.execute(statement).scalar_one_or_none()


def find_user_id_by_phone(session: Session, normalized_phone: str) -> UUID | None:
    statement = select(User.id).where(User.phone == normalized_phone)
    return session.scalar(statement)


def add_user(session: Session, user: User) -> User:
    session.add(user)
    return user


def lock_actor_and_target_users_for_update(
    session: Session,
    *,
    actor_user_id: UUID,
    target_user_id: UUID,
) -> _LockedActorTargetUsers | None:
    """Lock the actor/target User set once, in UUID-ascending order."""

    _validate_user_id(actor_user_id)
    _validate_user_id(target_user_id)
    ordered_ids = tuple(sorted({actor_user_id, target_user_id}))
    statement = (
        select(User)
        .where(User.id.in_(ordered_ids))
        .order_by(User.id.asc())
        .with_for_update()
    )
    users = {user.id: user for user in session.scalars(statement)}
    if len(users) != len(ordered_ids):
        return None
    return _LockedActorTargetUsers(
        actor=users[actor_user_id],
        target=users[target_user_id],
        _session=session,
    )


def _validate_locked_actor_target_users(
    session: Session,
    locked_users: object,
) -> _LockedActorTargetUsers:
    if not isinstance(locked_users, _LockedActorTargetUsers):
        raise TypeError(
            "locked_users must come from lock_actor_and_target_users_for_update"
        )
    if locked_users._session is not session:
        raise RuntimeError("locked_users was created by a different SQLAlchemy session")
    return locked_users


def _validate_user_id(user_id: UUID) -> None:
    if not isinstance(user_id, UUID):
        raise TypeError("User id must be a UUID")
