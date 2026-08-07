"""Transient exact-phone discovery and locked target eligibility."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.repository import (
    _LockedActorTargetUsers,
    find_user_id_by_phone,
    lock_actor_and_target_users_for_update,
)
from app.customer.repository import (
    _LockedActiveTargetCustomer,
    lock_active_customer_for_target_user,
)
from app.shop.repository import (
    _LockedActorShopStaff,
    _validate_locked_actor_shop_staff,
)
from app.shop_customer.contracts import (
    LockedEligibleShopCustomerTarget,
    TransientCanonicalShopCustomerPhone,
)
from app.shop_customer.values import CustomerId, UserId
from app.telegram.models import TelegramLink
from app.telegram.repository import (
    get_telegram_link_by_user_for_update,
    is_otp_eligible_telegram_link,
)


@dataclass(frozen=True, slots=True, repr=False)
class _LockedEligibleShopCustomerTarget:
    value: LockedEligibleShopCustomerTarget
    locked_users: _LockedActorTargetUsers
    locked_link: TelegramLink
    locked_customer: _LockedActiveTargetCustomer
    _session: Session

    def __repr__(self) -> str:
        return (
            "_LockedEligibleShopCustomerTarget("
            "value=<redacted>, locked_users=<redacted>, "
            "locked_link=<redacted>, locked_customer=<redacted>)"
        )


def discover_target_user_id(
    session: Session,
    *,
    target_phone: TransientCanonicalShopCustomerPhone,
) -> UUID | None:
    if not isinstance(target_phone, TransientCanonicalShopCustomerPhone):
        raise TypeError("target_phone must be transient canonical phone material")
    return find_user_id_by_phone(session, target_phone.for_server_lookup())


def resolve_locked_eligible_target(
    session: Session,
    *,
    locked_staff: _LockedActorShopStaff,
    target_user_id: UUID,
    expected_phone: TransientCanonicalShopCustomerPhone,
) -> _LockedEligibleShopCustomerTarget | None:
    """Recheck the complete live target chain under forward-ordered locks."""

    staff = _validate_locked_actor_shop_staff(session, locked_staff)
    if not isinstance(target_user_id, UUID):
        raise TypeError("target_user_id must come from server discovery")
    if not isinstance(expected_phone, TransientCanonicalShopCustomerPhone):
        raise TypeError("expected_phone must be transient canonical phone material")

    locked_users = lock_actor_and_target_users_for_update(
        session,
        actor_user_id=staff.staff.user_id,
        target_user_id=target_user_id,
    )
    if locked_users is None:
        return None
    target_user = locked_users.target
    if (
        not target_user.is_active
        or target_user.phone != expected_phone.for_server_lookup()
    ):
        return None

    locked_link = get_telegram_link_by_user_for_update(session, target_user)
    if not is_otp_eligible_telegram_link(
        locked_link,
        expected_user_id=target_user.id,
    ):
        return None

    locked_customer = lock_active_customer_for_target_user(
        session,
        locked_users=locked_users,
    )
    if locked_customer is None or locked_link is None:
        return None
    value = LockedEligibleShopCustomerTarget(
        user_id=UserId(target_user.id),
        customer_id=CustomerId(locked_customer.customer.id),
    )
    return _LockedEligibleShopCustomerTarget(
        value=value,
        locked_users=locked_users,
        locked_link=locked_link,
        locked_customer=locked_customer,
        _session=session,
    )


def _validate_locked_eligible_target(
    session: Session,
    locked_target: object,
) -> _LockedEligibleShopCustomerTarget:
    if not isinstance(locked_target, _LockedEligibleShopCustomerTarget):
        raise TypeError("locked_target must come from resolve_locked_eligible_target")
    if locked_target._session is not session:
        raise RuntimeError(
            "locked_target was created by a different SQLAlchemy session"
        )
    return locked_target
