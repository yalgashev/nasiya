"""Shop provisioning and staff/shop mutation service.

This module does not replace the production owner_application flow. It provides
the development/admin primitive that provisions a known owner into an active
shop inside the caller-owned transaction.

Shop status transitions are platform primitives. Production authorization for
platform admins is added in the admin milestone; in M5 they are called only by
production-guarded development CLI commands. actor_user_id may be nullable for
CLI/system calls.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import phone as phone_service
from app.auth import repository as auth_repository
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.shop import policy, repository
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction
from app.shop.values import ShopId, ShopStaffId, UserId

_EXPECTED_SHOP_ID_CONFLICT_CONSTRAINTS = frozenset({"pk_shops"})
_EXPECTED_STAFF_MEMBERSHIP_CONFLICT_CONSTRAINTS = frozenset(
    {"uq_shop_staff_shop_id_user_id"}
)
__all__ = (
    "add_staff",
    "change_staff_role",
    "provision_active_shop",
    "revoke_staff",
    "reactivate_shop",
    "suspend_shop",
)


class ProvisionActiveShopError(StrEnum):
    OWNER_NOT_FOUND = "owner_not_found"
    INVALID_NAME = "invalid_name"
    INVALID_PHONE = "invalid_phone"
    DUPLICATE_SHOP_ID = "duplicate_shop_id"


class AddStaffOutcome(StrEnum):
    ADDED = "added"
    REACTIVATED = "reactivated"
    ALREADY_ACTIVE = "already_active"


class ChangeStaffRoleOutcome(StrEnum):
    ROLE_CHANGED = "role_changed"
    ALREADY_ROLE = "already_role"


class RevokeStaffOutcome(StrEnum):
    REVOKED = "revoked"
    NOT_FOUND = "not_found"


class ShopStatusTransitionOutcome(StrEnum):
    TRANSITIONED = "transitioned"
    NOOP = "noop"


@dataclass(frozen=True)
class ProvisionedActiveShop:
    shop_id: UUID
    name: str
    status: ShopStatus


@dataclass(frozen=True)
class ProvisionActiveShopResult:
    shop: ProvisionedActiveShop | None = None
    error: ProvisionActiveShopError | None = None

    @property
    def succeeded(self) -> bool:
        return self.shop is not None and self.error is None


@dataclass(frozen=True)
class AddedShopStaff:
    staff_id: UUID
    role: ShopRole
    outcome: AddStaffOutcome


@dataclass(frozen=True)
class AddStaffResult:
    staff: AddedShopStaff | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.staff is not None and self.error is None


@dataclass(frozen=True)
class ChangedShopStaffRole:
    staff_id: UUID
    old_role: ShopRole
    new_role: ShopRole
    outcome: ChangeStaffRoleOutcome


@dataclass(frozen=True)
class ChangeStaffRoleResult:
    staff: ChangedShopStaffRole | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.staff is not None and self.error is None


@dataclass(frozen=True)
class RevokedShopStaff:
    staff_id: UUID | None
    old_role: ShopRole | None
    outcome: RevokeStaffOutcome


@dataclass(frozen=True)
class RevokeStaffResult:
    revocation: RevokedShopStaff | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.revocation is not None and self.error is None


@dataclass(frozen=True)
class ShopStatusTransition:
    shop_id: UUID
    status: ShopStatus
    outcome: ShopStatusTransitionOutcome


@dataclass(frozen=True)
class ShopStatusTransitionResult:
    transition: ShopStatusTransition | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.transition is not None and self.error is None


def provision_active_shop(
    session: Session,
    *,
    shop_id: ShopId,
    name: str,
    phone: str,
    address_text: str | None,
    owner_user_id: UserId,
    actor_user_id: UserId | None = None,
    now: datetime | None = None,
) -> ProvisionActiveShopResult:
    owner = session.get(User, owner_user_id)
    if owner is None:
        return ProvisionActiveShopResult(error=ProvisionActiveShopError.OWNER_NOT_FOUND)

    try:
        normalized_phone = phone_service.normalize_uzbekistan_phone(phone)
    except phone_service.PhoneNormalizationError:
        return ProvisionActiveShopResult(error=ProvisionActiveShopError.INVALID_PHONE)

    normalized_name = _normalize_shop_name(name)
    if normalized_name is None:
        return ProvisionActiveShopResult(error=ProvisionActiveShopError.INVALID_NAME)

    current_time = _coerce_now(now)
    try:
        with session.begin_nested():
            shop = repository.add_shop(
                session,
                shop_id=shop_id,
                name=normalized_name,
                phone=normalized_phone,
                address_text=address_text,
                status=ShopStatus.ACTIVE,
                now=current_time,
            )
            repository.add_shop_staff(
                session,
                shop_id=shop_id,
                user_id=owner_user_id,
                role=ShopRole.OWNER,
                now=current_time,
            )
            repository.add_shop_status_event(
                session,
                shop_id=shop_id,
                action=ShopStatusAction.ACTIVATED,
                actor_user_id=actor_user_id,
                reason=None,
                now=current_time,
            )
            repository.add_shop_staff_event(
                session,
                shop_id=shop_id,
                subject_user_id=owner_user_id,
                action=ShopStaffAction.ADDED,
                old_role=None,
                new_role=ShopRole.OWNER,
                actor_user_id=actor_user_id,
                now=current_time,
            )
            session.flush()
    except IntegrityError as exc:
        if _is_expected_shop_id_conflict(exc):
            return ProvisionActiveShopResult(
                error=ProvisionActiveShopError.DUPLICATE_SHOP_ID
            )
        raise

    return ProvisionActiveShopResult(
        shop=ProvisionedActiveShop(
            shop_id=shop.id,
            name=shop.name,
            status=ShopStatus(shop.status),
        )
    )


def suspend_shop(
    session: Session,
    *,
    shop_id: ShopId,
    actor_user_id: UserId | None,
    reason: str | None,
    now: datetime | None = None,
) -> ShopStatusTransitionResult:
    return _transition_shop_status(
        session,
        shop_id=shop_id,
        actor_user_id=actor_user_id,
        reason=reason,
        target_status=ShopStatus.SUSPENDED,
        event_action=ShopStatusAction.SUSPENDED,
        now=now,
    )


def reactivate_shop(
    session: Session,
    *,
    shop_id: ShopId,
    actor_user_id: UserId | None,
    reason: str | None,
    now: datetime | None = None,
) -> ShopStatusTransitionResult:
    return _transition_shop_status(
        session,
        shop_id=shop_id,
        actor_user_id=actor_user_id,
        reason=reason,
        target_status=ShopStatus.ACTIVE,
        event_action=ShopStatusAction.REACTIVATED,
        now=now,
    )


def revoke_staff(
    session: Session,
    *,
    shop_id: ShopId,
    actor_user_id: UserId,
    target_staff_id: ShopStaffId,
    now: datetime | None = None,
) -> RevokeStaffResult:
    current_time = _coerce_now(now)

    with session.begin_nested():
        locked_shop = repository.lock_shop_for_update(session, shop_id=shop_id)
        if locked_shop is None:
            return RevokeStaffResult(error=ErrorCode.FORBIDDEN)

        actor_staff = repository._lock_staff_for_user_for_update(
            session,
            locked_shop=locked_shop,
            user_id=actor_user_id,
        )
        if not _is_active_owner(actor_staff):
            return RevokeStaffResult(error=ErrorCode.FORBIDDEN)

        shop_status = ShopStatus(locked_shop.shop.status)
        if not policy.can_mutate_shop(shop_status):
            return RevokeStaffResult(error=ErrorCode.SHOP_SUSPENDED)

        target_staff = repository._lock_active_staff_by_id_for_update(
            session,
            locked_shop=locked_shop,
            staff_id=target_staff_id,
        )
        if target_staff is None:
            return _safe_staff_revoke_not_found_result()

        old_role = ShopRole(target_staff.role)
        if old_role is ShopRole.OWNER:
            owner_count = repository.count_active_owners(session, shop_id=shop_id)
            if owner_count <= 1:
                return RevokeStaffResult(error=ErrorCode.LAST_OWNER)

        target_staff.is_active = False
        target_staff.revoked_at = current_time
        target_staff.updated_at = current_time
        session.add(target_staff)
        repository.add_shop_staff_event(
            session,
            shop_id=shop_id,
            subject_user_id=UserId(target_staff.user_id),
            action=ShopStaffAction.REVOKED,
            old_role=old_role,
            new_role=None,
            actor_user_id=actor_user_id,
            now=current_time,
        )
        session.flush()

    return RevokeStaffResult(
        revocation=RevokedShopStaff(
            staff_id=target_staff.id,
            old_role=old_role,
            outcome=RevokeStaffOutcome.REVOKED,
        )
    )


def change_staff_role(
    session: Session,
    *,
    shop_id: ShopId,
    actor_user_id: UserId,
    target_staff_id: ShopStaffId,
    new_role: ShopRole | str,
    now: datetime | None = None,
) -> ChangeStaffRoleResult:
    current_time = _coerce_now(now)

    with session.begin_nested():
        locked_shop = repository.lock_shop_for_update(session, shop_id=shop_id)
        if locked_shop is None:
            return ChangeStaffRoleResult(error=ErrorCode.FORBIDDEN)

        actor_staff = repository._lock_staff_for_user_for_update(
            session,
            locked_shop=locked_shop,
            user_id=actor_user_id,
        )
        if not _is_active_owner(actor_staff):
            return ChangeStaffRoleResult(error=ErrorCode.FORBIDDEN)

        shop_status = ShopStatus(locked_shop.shop.status)
        if not policy.can_mutate_shop(shop_status):
            return ChangeStaffRoleResult(error=ErrorCode.SHOP_SUSPENDED)

        target_staff = repository._lock_active_staff_by_id_for_update(
            session,
            locked_shop=locked_shop,
            staff_id=target_staff_id,
        )
        if target_staff is None:
            return ChangeStaffRoleResult(error=ErrorCode.FORBIDDEN)

        coerced_new_role = _coerce_staff_role(new_role)
        if coerced_new_role is None:
            return ChangeStaffRoleResult(error=ErrorCode.VALIDATION_ERROR)

        old_role = ShopRole(target_staff.role)
        if old_role is coerced_new_role:
            return ChangeStaffRoleResult(
                staff=ChangedShopStaffRole(
                    staff_id=target_staff.id,
                    old_role=old_role,
                    new_role=old_role,
                    outcome=ChangeStaffRoleOutcome.ALREADY_ROLE,
                )
            )

        if old_role is ShopRole.OWNER and coerced_new_role is not ShopRole.OWNER:
            owner_count = repository.count_active_owners(session, shop_id=shop_id)
            if owner_count <= 1:
                return ChangeStaffRoleResult(error=ErrorCode.LAST_OWNER)

        target_staff.role = coerced_new_role.value
        target_staff.updated_at = current_time
        session.add(target_staff)
        repository.add_shop_staff_event(
            session,
            shop_id=shop_id,
            subject_user_id=UserId(target_staff.user_id),
            action=ShopStaffAction.ROLE_CHANGED,
            old_role=old_role,
            new_role=coerced_new_role,
            actor_user_id=actor_user_id,
            now=current_time,
        )
        session.flush()

    return ChangeStaffRoleResult(
        staff=ChangedShopStaffRole(
            staff_id=target_staff.id,
            old_role=old_role,
            new_role=ShopRole(target_staff.role),
            outcome=ChangeStaffRoleOutcome.ROLE_CHANGED,
        )
    )


def add_staff(
    session: Session,
    *,
    shop_id: ShopId,
    actor_user_id: UserId,
    phone: str,
    role: ShopRole | str,
    now: datetime | None = None,
) -> AddStaffResult:
    current_time = _coerce_now(now)

    try:
        with session.begin_nested():
            locked_shop = repository.lock_shop_for_update(session, shop_id=shop_id)
            if locked_shop is None:
                return AddStaffResult(error=ErrorCode.FORBIDDEN)

            actor_staff = repository._lock_staff_for_user_for_update(
                session,
                locked_shop=locked_shop,
                user_id=actor_user_id,
            )
            if not _is_active_owner(actor_staff):
                return AddStaffResult(error=ErrorCode.FORBIDDEN)

            shop_status = ShopStatus(locked_shop.shop.status)
            if not policy.can_mutate_shop(shop_status):
                return AddStaffResult(error=ErrorCode.SHOP_SUSPENDED)

            target_user = _get_target_user_by_phone(session, phone)
            if target_user is None:
                return AddStaffResult(error=ErrorCode.VALIDATION_ERROR)

            coerced_role = _coerce_staff_role(role)
            if coerced_role is None:
                return AddStaffResult(error=ErrorCode.VALIDATION_ERROR)

            target_user_id = UserId(target_user.id)
            existing_staff = repository._lock_staff_for_user_for_update(
                session,
                locked_shop=locked_shop,
                user_id=target_user_id,
            )
            if existing_staff is not None and existing_staff.is_active:
                return AddStaffResult(
                    staff=AddedShopStaff(
                        staff_id=existing_staff.id,
                        role=ShopRole(existing_staff.role),
                        outcome=AddStaffOutcome.ALREADY_ACTIVE,
                    )
                )

            if existing_staff is None:
                staff = repository.add_shop_staff(
                    session,
                    shop_id=shop_id,
                    user_id=target_user_id,
                    role=coerced_role,
                    now=current_time,
                )
                outcome = AddStaffOutcome.ADDED
            else:
                staff = existing_staff
                staff.is_active = True
                staff.revoked_at = None
                staff.role = coerced_role.value
                staff.updated_at = current_time
                session.add(staff)
                outcome = AddStaffOutcome.REACTIVATED

            repository.add_shop_staff_event(
                session,
                shop_id=shop_id,
                subject_user_id=target_user_id,
                action=ShopStaffAction.ADDED,
                old_role=None,
                new_role=coerced_role,
                actor_user_id=actor_user_id,
                now=current_time,
            )
            session.flush()
    except IntegrityError as exc:
        if _is_expected_staff_membership_conflict(exc):
            return AddStaffResult(error=ErrorCode.VALIDATION_ERROR)
        raise

    return AddStaffResult(
        staff=AddedShopStaff(
            staff_id=staff.id,
            role=ShopRole(staff.role),
            outcome=outcome,
        )
    )


def _normalize_shop_name(raw_name: str) -> str | None:
    if not isinstance(raw_name, str):
        return None

    normalized_name = " ".join(raw_name.split())
    if len(normalized_name) < 2 or len(normalized_name) > 120:
        return None
    return normalized_name


def _coerce_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Shop provisioning timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _is_expected_shop_id_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name in _EXPECTED_SHOP_ID_CONFLICT_CONSTRAINTS


def _transition_shop_status(
    session: Session,
    *,
    shop_id: ShopId,
    actor_user_id: UserId | None,
    reason: str | None,
    target_status: ShopStatus,
    event_action: ShopStatusAction,
    now: datetime | None,
) -> ShopStatusTransitionResult:
    current_time = _coerce_now(now)

    with session.begin_nested():
        locked_shop = repository.lock_shop_for_update(session, shop_id=shop_id)
        if locked_shop is None:
            return ShopStatusTransitionResult(error=ErrorCode.FORBIDDEN)

        normalized_reason = _normalize_required_reason(reason)
        if normalized_reason is None:
            return ShopStatusTransitionResult(error=ErrorCode.REASON_REQUIRED)

        current_status = ShopStatus(locked_shop.shop.status)
        if current_status is target_status:
            return ShopStatusTransitionResult(
                transition=ShopStatusTransition(
                    shop_id=locked_shop.shop.id,
                    status=current_status,
                    outcome=ShopStatusTransitionOutcome.NOOP,
                )
            )

        locked_shop.shop.status = target_status.value
        locked_shop.shop.updated_at = current_time
        session.add(locked_shop.shop)
        repository.add_shop_status_event(
            session,
            shop_id=shop_id,
            action=event_action,
            actor_user_id=actor_user_id,
            reason=normalized_reason,
            now=current_time,
        )
        session.flush()

    return ShopStatusTransitionResult(
        transition=ShopStatusTransition(
            shop_id=locked_shop.shop.id,
            status=ShopStatus(locked_shop.shop.status),
            outcome=ShopStatusTransitionOutcome.TRANSITIONED,
        )
    )


def _normalize_required_reason(reason: str | None) -> str | None:
    if not isinstance(reason, str):
        return None

    normalized_reason = reason.strip()
    if not normalized_reason:
        return None
    return normalized_reason


def _safe_staff_revoke_not_found_result() -> RevokeStaffResult:
    return RevokeStaffResult(
        revocation=RevokedShopStaff(
            staff_id=None,
            old_role=None,
            outcome=RevokeStaffOutcome.NOT_FOUND,
        )
    )


def _coerce_staff_role(role: ShopRole | str) -> ShopRole | None:
    if isinstance(role, ShopRole):
        return role
    try:
        return ShopRole(role)
    except ValueError:
        return None


def _is_active_owner(staff) -> bool:
    return staff is not None and staff.is_active and staff.role == ShopRole.OWNER.value


def _get_target_user_by_phone(session: Session, raw_phone: str) -> User | None:
    try:
        normalized_phone = phone_service.normalize_uzbekistan_phone(raw_phone)
    except phone_service.PhoneNormalizationError:
        return None
    return auth_repository.get_by_phone(session, normalized_phone)


def _is_expected_staff_membership_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name in _EXPECTED_STAFF_MEMBERSHIP_CONFLICT_CONSTRAINTS
