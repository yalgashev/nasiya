"""Route-agnostic active-customer linking under the frozen M12 lock order."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    AuditWriter,
    ShopCustomerDefaultsUpdatedAuditPayload,
    ShopCustomerLinkedAuditPayload,
    ShopCustomerPolicyUpdatedAuditPayload,
)
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.error_codes import ErrorCode
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import (
    lock_actor_shop_staff_for_update,
    lock_shop_for_update,
    read_locked_shop_defaults,
    update_locked_shop_defaults,
)
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    DetachedShopCustomerAuthority,
    LinkShopCustomerCommand,
    ShopCustomerLinkOutcome,
    ShopCustomerLinkResult,
    ShopCustomerPolicy,
    ShopCustomerPolicyUpdateResult,
    ShopCustomerRevision,
    ShopDefaultCreditPolicyUpdate,
    ShopDefaultCreditPolicyUpdateResult,
    ShopDefaultPolicyUpdateOutcome,
    UpdateShopCustomerPolicyCommand,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.policy import (
    ShopCustomerAuthorizationContext,
    ShopCustomerCapability,
)
from app.shop_customer.repository import (
    _mark_shop_customer_predecessors_locked,
    insert_shop_customer,
    lock_shop_customer_by_pair,
    lock_shop_customer_by_tenant_locator,
    update_locked_shop_customer,
)
from app.shop_customer.targeting import (
    discover_target_user_id,
    resolve_locked_eligible_target,
)
from app.shop_customer.values import (
    CreditLimitUzbekistanSom,
    MaxOpenDebts,
    ShopCustomerId,
)

AuditWriterFactory = Callable[[Session], AuditWriter]


class ShopCustomerLinkInternalError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Shop customer linking failed")

    def __repr__(self) -> str:
        return "ShopCustomerLinkInternalError()"


class ShopCustomerMutationDenied(PermissionError):
    """A stable, identifier-free denial for a live shop mutation recheck."""

    def __init__(self, error_code: ErrorCode) -> None:
        if error_code not in {ErrorCode.FORBIDDEN, ErrorCode.SHOP_SUSPENDED}:
            raise ValueError("Shop mutation denial code is invalid")
        self.error_code = error_code
        super().__init__("Shop customer mutation denied")

    def __repr__(self) -> str:
        return f"ShopCustomerMutationDenied(error_code={self.error_code.value!r})"


def update_shop_default_credit_policy(
    session: Session,
    *,
    authority: DetachedShopCustomerAuthority,
    command: ShopDefaultCreditPolicyUpdate,
    now: datetime,
    audit_writer: AuditWriter,
) -> ShopDefaultCreditPolicyUpdateResult:
    """Update an owner's prospective default pair in the caller-owned TX-C.

    The Shop row is the stale-form authority and is deliberately locked before
    the actor's membership.  The function never opens ShopCustomer rows: a
    default is copied only by a later link transaction.
    """

    if not isinstance(authority, DetachedShopCustomerAuthority):
        raise TypeError("authority must be server-derived")
    if not isinstance(command, ShopDefaultCreditPolicyUpdate):
        raise TypeError("command must be a ShopDefaultCreditPolicyUpdate")
    if not isinstance(audit_writer, AuditWriter):
        raise TypeError("audit_writer must implement AuditWriter")

    locked_shop = lock_shop_for_update(
        session,
        shop_id=ShopId(authority.current_shop_id),
    )
    if locked_shop is None:
        raise ShopCustomerMutationDenied(ErrorCode.FORBIDDEN)
    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=UserId(authority.actor_user_id),
    )
    if locked_staff is None:
        raise ShopCustomerMutationDenied(ErrorCode.FORBIDDEN)

    shop_status = ShopStatus(locked_shop.shop.status)
    if shop_status is ShopStatus.SUSPENDED:
        raise ShopCustomerMutationDenied(ErrorCode.SHOP_SUSPENDED)

    authorization = ShopCustomerAuthorizationContext(
        role=ShopRole(locked_staff.staff.role),
        shop_status=shop_status,
        membership_active=locked_staff.staff.is_active,
        # Platform-admin status is intentionally not consulted: it cannot
        # replace this locked, live shop membership.
        is_platform_admin=False,
    )
    if not authorization.allows(ShopCustomerCapability.UPDATE_DEFAULTS):
        raise ShopCustomerMutationDenied(ErrorCode.FORBIDDEN)

    current_defaults = read_locked_shop_defaults(session, locked_shop=locked_shop)
    if locked_shop.shop.updated_at != command.expected_updated_at.value:
        return ShopDefaultCreditPolicyUpdateResult(
            outcome=ShopDefaultPolicyUpdateOutcome.STALE
        )
    if current_defaults == command.new_defaults:
        return ShopDefaultCreditPolicyUpdateResult(
            outcome=ShopDefaultPolicyUpdateOutcome.NO_CHANGE,
            defaults=current_defaults,
        )

    update_locked_shop_defaults(
        session,
        locked_shop=locked_shop,
        defaults=command.new_defaults,
        now=now,
    )
    audit_writer.append(
        event=AuditEvent(
            event_type=AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=authority.actor_user_id,
            object_type=AuditObjectType.SHOP,
            object_id=locked_shop.shop.id,
            occurred_at=now,
            candidate_metadata=ShopCustomerDefaultsUpdatedAuditPayload(
                old_defaults=current_defaults,
                new_defaults=command.new_defaults,
            ).as_candidate_metadata(),
        )
    )
    return ShopDefaultCreditPolicyUpdateResult(
        outcome=ShopDefaultPolicyUpdateOutcome.CHANGED,
        defaults=command.new_defaults,
    )


def update_shop_customer_policy(
    session: Session,
    *,
    authority: DetachedShopCustomerAuthority,
    command: UpdateShopCustomerPolicyCommand,
    now: datetime,
    audit_writer: AuditWriter,
) -> ShopCustomerPolicyUpdateResult:
    """Apply one tenant-scoped policy replacement in the caller-owned TX-C.

    A path locator narrows the row only after the current Shop and actor
    membership are locked; it never grants cross-tenant authority on its own.
    """

    if not isinstance(authority, DetachedShopCustomerAuthority):
        raise TypeError("authority must be server-derived")
    if not isinstance(command, UpdateShopCustomerPolicyCommand):
        raise TypeError("command must be an UpdateShopCustomerPolicyCommand")
    if not isinstance(audit_writer, AuditWriter):
        raise TypeError("audit_writer must implement AuditWriter")

    locked_shop = lock_shop_for_update(
        session,
        shop_id=ShopId(authority.current_shop_id),
    )
    if locked_shop is None:
        raise ShopCustomerMutationDenied(ErrorCode.FORBIDDEN)
    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=UserId(authority.actor_user_id),
    )
    if locked_staff is None:
        raise ShopCustomerMutationDenied(ErrorCode.FORBIDDEN)

    shop_status = ShopStatus(locked_shop.shop.status)
    if shop_status is ShopStatus.SUSPENDED:
        raise ShopCustomerMutationDenied(ErrorCode.SHOP_SUSPENDED)

    authorization = ShopCustomerAuthorizationContext(
        role=ShopRole(locked_staff.staff.role),
        shop_status=shop_status,
        membership_active=locked_staff.staff.is_active,
        is_platform_admin=False,
    )
    if not authorization.allows(ShopCustomerCapability.UPDATE_POLICY):
        raise ShopCustomerMutationDenied(ErrorCode.FORBIDDEN)

    locked_shop_customer = lock_shop_customer_by_tenant_locator(
        session,
        locked_shop=locked_shop,
        shop_customer_id=command.locator.shop_customer_id,
    )
    if locked_shop_customer is None:
        return ShopCustomerPolicyUpdateResult.unavailable()

    current = locked_shop_customer.row
    if current.revision != command.expected_revision.value:
        return ShopCustomerPolicyUpdateResult.stale()
    old_policy = ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(current.credit_limit_uzs),
        max_open_debts=MaxOpenDebts(current.max_open_debts),
        list_status=ShopCustomerListStatus(current.list_status),
    )
    if old_policy == command.new_policy:
        return ShopCustomerPolicyUpdateResult.no_change(command)

    result = ShopCustomerPolicyUpdateResult.changed(command)
    update_locked_shop_customer(
        session,
        locked_shop_customer=locked_shop_customer,
        policy=command.new_policy,
        revision=result.revision,
        now=now,
    )
    audit_writer.append(
        event=AuditEvent(
            event_type=AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=authority.actor_user_id,
            object_type=AuditObjectType.SHOP_CUSTOMER,
            object_id=current.id,
            occurred_at=now,
            candidate_metadata=ShopCustomerPolicyUpdatedAuditPayload(
                old_policy=old_policy,
                new_policy=command.new_policy,
                revision=result.revision,
            ).as_candidate_metadata(),
        )
    )
    return result


def coordinate_link_active_customer(
    session_factory: sessionmaker[Session],
    *,
    command: LinkShopCustomerCommand,
    now: datetime,
    audit_writer_factory: AuditWriterFactory = SqlAlchemyAuditWriter,
) -> ShopCustomerLinkResult:
    """Own TX-C and return only a redacted domain result."""

    if not isinstance(command, LinkShopCustomerCommand):
        raise TypeError("command must be a LinkShopCustomerCommand")
    try:
        with session_factory.begin() as session:
            target_user_id = discover_target_user_id(
                session,
                target_phone=command.target_phone,
            )
            if target_user_id is None:
                return ShopCustomerLinkResult.unavailable()
            return link_active_customer(
                session,
                command=command,
                target_user_id=target_user_id,
                now=now,
                audit_writer=audit_writer_factory(session),
            )
    except ShopCustomerLinkInternalError:
        raise
    except Exception:
        raise ShopCustomerLinkInternalError() from None


def link_active_customer(
    session: Session,
    *,
    command: LinkShopCustomerCommand,
    target_user_id: UUID,
    now: datetime,
    audit_writer: AuditWriter,
) -> ShopCustomerLinkResult:
    """Apply the mutation without owning the borrowed SQLAlchemy session."""

    if not isinstance(command, LinkShopCustomerCommand):
        raise TypeError("command must be a LinkShopCustomerCommand")
    if not isinstance(target_user_id, UUID):
        raise TypeError("target_user_id must come from server discovery")
    if not isinstance(audit_writer, AuditWriter):
        raise TypeError("audit_writer must implement AuditWriter")

    authority = command.authority
    locked_shop = lock_shop_for_update(
        session,
        shop_id=ShopId(authority.current_shop_id),
    )
    if locked_shop is None:
        return ShopCustomerLinkResult.unavailable()
    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=UserId(authority.actor_user_id),
    )
    if locked_staff is None:
        return ShopCustomerLinkResult.unavailable()

    locked_target = resolve_locked_eligible_target(
        session,
        locked_staff=locked_staff,
        target_user_id=target_user_id,
        expected_phone=command.target_phone,
    )
    if locked_target is None:
        return ShopCustomerLinkResult.unavailable()
    authorization = ShopCustomerAuthorizationContext(
        role=ShopRole(locked_staff.staff.role),
        shop_status=ShopStatus(locked_shop.shop.status),
        membership_active=locked_staff.staff.is_active,
        is_platform_admin=locked_target.locked_users.actor.is_platform_admin,
    )
    if not authorization.allows(ShopCustomerCapability.LINK_CUSTOMER):
        return ShopCustomerLinkResult.unavailable()

    predecessors = _mark_shop_customer_predecessors_locked(
        session,
        locked_shop=locked_shop,
        locked_customer=locked_target.locked_customer,
    )
    existing = lock_shop_customer_by_pair(
        session,
        locked_predecessors=predecessors,
    )
    if existing is not None:
        return ShopCustomerLinkResult(
            shop_customer_id=ShopCustomerId(existing.row.id),
            outcome=ShopCustomerLinkOutcome.ALREADY_LINKED,
        )

    snapshot = read_locked_shop_defaults(
        session,
        locked_shop=locked_shop,
    ).for_new_link()
    row = insert_shop_customer(
        session,
        locked_predecessors=predecessors,
        shop_customer_id=ShopCustomerId(uuid4()),
        snapshot=snapshot,
        created_by_user_id=UserId(authority.actor_user_id),
        now=now,
    )
    if row is None:
        raced = lock_shop_customer_by_pair(
            session,
            locked_predecessors=predecessors,
        )
        if raced is None:
            raise RuntimeError("Expected shop customer conflict did not converge")
        return ShopCustomerLinkResult(
            shop_customer_id=ShopCustomerId(raced.row.id),
            outcome=ShopCustomerLinkOutcome.ALREADY_LINKED,
        )

    audit_writer.append(
        event=AuditEvent(
            event_type=AuditEventType.SHOP_CUSTOMER_LINKED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=authority.actor_user_id,
            object_type=AuditObjectType.SHOP_CUSTOMER,
            object_id=row.id,
            occurred_at=now,
            candidate_metadata=ShopCustomerLinkedAuditPayload(
                policy=snapshot.policy,
                revision=ShopCustomerRevision(1),
            ).as_candidate_metadata(),
        )
    )
    return ShopCustomerLinkResult(
        shop_customer_id=ShopCustomerId(row.id),
        outcome=ShopCustomerLinkOutcome.CREATED,
    )
