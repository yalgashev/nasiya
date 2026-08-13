from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtClawbackAppliedAuditPayload,
    DebtOverdueAuditPayload,
    DebtReopenedAfterPaymentVoidAuditPayload,
    PaymentVoidedAuditPayload,
    create_debt_reopened_after_payment_void_audit_event,
    create_payment_voided_audit_event,
)
from app.audit.redaction import redact_audit_payload
from app.debt.enums import DebtOverdueSource, DebtStatus
from app.debt.presentation import DebtWebLanguage
from app.debt.values import ClawbackIncreaseUZS, DebtRevision
from app.idempotency.contracts import IdempotencyEndpoint, IdempotencyResultType
from app.payment.enums import PaymentVoidReason
from app.payment.presentation import (
    M18_PAYMENT_VOID_ROUTE_CONTRACTS,
    CustomerPaymentVoidPresentation,
    PaymentShopCapability,
    PaymentShopCapabilityContext,
    ShopPaymentVoidPresentation,
    get_payment_void_reason_label,
    shop_payment_capabilities,
)
from app.shop.enums import ShopRole, ShopStatus

ACTOR = UUID(int=1)
PAYMENT = UUID(int=2)
DEBT = UUID(int=3)
VOIDED_AT = datetime(2026, 8, 13, 12, tzinfo=UTC)
REVISION = DebtRevision(8)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    (
        (DebtStatus.ACTIVE, DebtStatus.ACTIVE),
        (DebtStatus.OVERDUE, DebtStatus.OVERDUE),
        (DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF),
        (DebtStatus.PAID, DebtStatus.ACTIVE),
        (DebtStatus.PAID, DebtStatus.OVERDUE),
        (DebtStatus.WRITTEN_OFF_SETTLED, DebtStatus.WRITTEN_OFF),
    ),
)
def test_payment_voided_user_payment_audit_has_exact_closed_payload(
    from_status: DebtStatus, to_status: DebtStatus
) -> None:
    payload = PaymentVoidedAuditPayload(
        reason=PaymentVoidReason.WRONG_DEBT,
        from_status=from_status,
        to_status=to_status,
        debt_revision_after=REVISION,
    )
    event = create_payment_voided_audit_event(
        actor_user_id=ACTOR,
        payment_id=PAYMENT,
        occurred_at=VOIDED_AT,
        voided_at=VOIDED_AT,
        current_revision=REVISION,
        payload=payload,
    )

    assert event.event_type is AuditEventType.PAYMENT_VOIDED
    assert event.actor_kind is AuditActorKind.USER
    assert event.actor_user_id == ACTOR
    assert event.object_type is AuditObjectType.PAYMENT
    assert event.object_id == PAYMENT
    assert redact_audit_payload(event) == {
        "reason": "wrong_debt",
        "from_status": from_status.value,
        "to_status": to_status.value,
        "debt_revision_after": 8,
    }


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    (
        (DebtStatus.PAID, DebtStatus.ACTIVE),
        (DebtStatus.PAID, DebtStatus.OVERDUE),
        (DebtStatus.WRITTEN_OFF_SETTLED, DebtStatus.WRITTEN_OFF),
    ),
)
def test_reopened_audit_exists_only_for_exact_status_change_pairs(
    from_status: DebtStatus, to_status: DebtStatus
) -> None:
    payload = DebtReopenedAfterPaymentVoidAuditPayload(
        from_status=from_status,
        to_status=to_status,
        debt_revision_after=REVISION,
    )
    event = create_debt_reopened_after_payment_void_audit_event(
        actor_user_id=ACTOR,
        debt_id=DEBT,
        occurred_at=VOIDED_AT,
        voided_at=VOIDED_AT,
        current_revision=REVISION,
        payload=payload,
    )

    assert event.event_type is AuditEventType.DEBT_REOPENED_AFTER_PAYMENT_VOID
    assert event.object_type is AuditObjectType.DEBT
    assert redact_audit_payload(event) == {
        "source": "payment_void",
        "from_status": from_status.value,
        "to_status": to_status.value,
        "debt_revision_after": 8,
    }

    with pytest.raises(ValueError, match="transition is invalid"):
        DebtReopenedAfterPaymentVoidAuditPayload(
            from_status=to_status,
            to_status=to_status,
            debt_revision_after=REVISION,
        )


def test_void_audit_time_revision_actor_object_and_json_types_are_strict() -> None:
    payload = PaymentVoidedAuditPayload(
        reason=PaymentVoidReason.INCORRECT_AMOUNT,
        from_status=DebtStatus.PAID,
        to_status=DebtStatus.ACTIVE,
        debt_revision_after=REVISION,
    )
    with pytest.raises(ValueError, match="time must match"):
        create_payment_voided_audit_event(
            actor_user_id=ACTOR,
            payment_id=PAYMENT,
            occurred_at=VOIDED_AT,
            voided_at=VOIDED_AT.replace(hour=13),
            current_revision=REVISION,
            payload=payload,
        )
    with pytest.raises(ValueError, match="revision must match"):
        create_payment_voided_audit_event(
            actor_user_id=ACTOR,
            payment_id=PAYMENT,
            occurred_at=VOIDED_AT,
            voided_at=VOIDED_AT,
            current_revision=DebtRevision(9),
            payload=payload,
        )
    with pytest.raises(ValueError, match="actor must be a user"):
        AuditEvent(
            event_type=AuditEventType.PAYMENT_VOIDED,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.PAYMENT,
            object_id=PAYMENT,
            occurred_at=VOIDED_AT,
            candidate_metadata=payload.as_candidate_metadata(),
        )
    event = create_payment_voided_audit_event(
        actor_user_id=ACTOR,
        payment_id=PAYMENT,
        occurred_at=VOIDED_AT,
        voided_at=VOIDED_AT,
        current_revision=REVISION,
        payload=payload,
    )
    for changed in (
        {**event.candidate_metadata, "reason": 1},
        {**event.candidate_metadata, "debt_revision_after": "8"},
        {**event.candidate_metadata, "actor_id": str(ACTOR)},
    ):
        corrupt = AuditEvent(
            event_type=AuditEventType.PAYMENT_VOIDED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR,
            object_type=AuditObjectType.PAYMENT,
            object_id=PAYMENT,
            occurred_at=VOIDED_AT,
            candidate_metadata=changed,
        )
        with pytest.raises(ValueError):
            redact_audit_payload(corrupt)


def test_paid_after_due_system_pair_accepts_only_payment_void_paid_source_pair() -> (
    None
):
    overdue = DebtOverdueAuditPayload(
        source=DebtOverdueSource.PAYMENT_VOID,
        overdue_revision=REVISION,
        business_date=VOIDED_AT.date(),
    )
    clawback = DebtClawbackAppliedAuditPayload(
        source=DebtOverdueSource.PAYMENT_VOID,
        balance_increase_uzs=ClawbackIncreaseUZS(Decimal("0")),
        overdue_revision=REVISION,
    )
    overdue_event = AuditEvent(
        event_type=AuditEventType.DEBT_OVERDUE,
        actor_kind=AuditActorKind.SYSTEM,
        actor_user_id=None,
        object_type=AuditObjectType.DEBT,
        object_id=DEBT,
        occurred_at=VOIDED_AT,
        candidate_metadata=overdue.as_candidate_metadata(),
    )
    clawback_event = AuditEvent(
        event_type=AuditEventType.DEBT_CLAWBACK_APPLIED,
        actor_kind=AuditActorKind.SYSTEM,
        actor_user_id=None,
        object_type=AuditObjectType.DEBT,
        object_id=DEBT,
        occurred_at=VOIDED_AT,
        candidate_metadata=clawback.as_candidate_metadata(),
    )

    assert redact_audit_payload(overdue_event)["from_status"] == "paid"
    assert redact_audit_payload(overdue_event)["source"] == "payment_void"
    assert redact_audit_payload(clawback_event) == {
        "source": "payment_void",
        "from_basis": "discounted",
        "to_basis": "original",
        "balance_increase_uzs": 0,
        "overdue_revision": 8,
    }
    wrong = dict(overdue.as_candidate_metadata())
    wrong["from_status"] = "active"
    with pytest.raises(ValueError, match="transition is invalid"):
        redact_audit_payload(
            AuditEvent(
                event_type=AuditEventType.DEBT_OVERDUE,
                actor_kind=AuditActorKind.SYSTEM,
                actor_user_id=None,
                object_type=AuditObjectType.DEBT,
                object_id=DEBT,
                occurred_at=VOIDED_AT,
                candidate_metadata=wrong,
            )
        )


def test_shop_and_customer_void_projections_have_one_way_privacy() -> None:
    label = get_payment_void_reason_label(
        DebtWebLanguage.UZ_LATN, PaymentVoidReason.DUPLICATE_PAYMENT
    )
    shop = ShopPaymentVoidPresentation(True, VOIDED_AT, label)
    customer = CustomerPaymentVoidPresentation(True, VOIDED_AT)

    assert set(field.name for field in fields(shop)) == {
        "is_voided",
        "voided_at",
        "reason_label",
    }
    assert set(field.name for field in fields(customer)) == {
        "is_voided",
        "voided_at",
    }
    for forbidden in (
        "actor",
        "reason",
        "rating",
        "cause",
        "shop_id",
        "payment_void_id",
        "key",
        "hash",
    ):
        assert forbidden not in customer.__dataclass_fields__
    assert "duplicate_payment" not in repr(shop)
    assert str(ACTOR) not in repr(customer)
    with pytest.raises(ValueError, match="incoherent"):
        CustomerPaymentVoidPresentation(False, VOIDED_AT)


def test_void_authority_endpoint_result_and_exact_two_ssr_route_inventory() -> None:
    for role in (ShopRole.OWNER, ShopRole.MANAGER):
        capabilities = shop_payment_capabilities(
            PaymentShopCapabilityContext(role, ShopStatus.ACTIVE, True)
        )
        assert {
            PaymentShopCapability.VOID_FORM,
            PaymentShopCapability.VOID,
        } <= capabilities
    for role in (ShopRole.CASHIER, None):
        capabilities = shop_payment_capabilities(
            PaymentShopCapabilityContext(role, ShopStatus.ACTIVE, role is not None)
        )
        assert PaymentShopCapability.VOID not in capabilities

    assert IdempotencyEndpoint.SHOP_PAYMENTS_VOID.value == "shop.payments.void"
    assert IdempotencyResultType.PAYMENT.value == "payment"
    assert [
        (contract.method, contract.path)
        for contract in M18_PAYMENT_VOID_ROUTE_CONTRACTS
    ] == [
        ("GET", "/shop/payments/{payment_id}/void"),
        ("POST", "/shop/payments/{payment_id}/void"),
    ]
    inventory = " ".join(contract.path for contract in M18_PAYMENT_VOID_ROUTE_CONTRACTS)
    for forbidden in ("/customer", "/admin", "/api", "fragment", "search"):
        assert forbidden not in inventory
