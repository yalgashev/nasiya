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
    ShopCustomerLinkedAuditPayload,
)
from app.audit.repository import SqlAlchemyAuditWriter
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import (
    lock_actor_shop_staff_for_update,
    lock_shop_for_update,
    read_locked_shop_defaults,
)
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    LinkShopCustomerCommand,
    ShopCustomerLinkOutcome,
    ShopCustomerLinkResult,
    ShopCustomerRevision,
)
from app.shop_customer.policy import (
    ShopCustomerAuthorizationContext,
    ShopCustomerCapability,
)
from app.shop_customer.repository import (
    _mark_shop_customer_predecessors_locked,
    insert_shop_customer,
    lock_shop_customer_by_pair,
)
from app.shop_customer.targeting import (
    discover_target_user_id,
    resolve_locked_eligible_target,
)
from app.shop_customer.values import ShopCustomerId

AuditWriterFactory = Callable[[Session], AuditWriter]


class ShopCustomerLinkInternalError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Shop customer linking failed")

    def __repr__(self) -> str:
        return "ShopCustomerLinkInternalError()"


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
