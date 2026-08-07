from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    ShopCustomerDefaultsUpdatedAuditPayload,
    ShopCustomerLinkedAuditPayload,
    ShopCustomerPolicyUpdatedAuditPayload,
)
from app.audit.redaction import redact_audit_payload
from app.shop_customer.contracts import (
    ShopCustomerPolicy,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.values import CreditLimitUzbekistanSom, MaxOpenDebts

_ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
_OBJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
_NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _policy(
    *,
    credit_limit: str = "1000000",
    max_open_debts: int = 2,
    list_status: ShopCustomerListStatus = ShopCustomerListStatus.NORMAL,
) -> ShopCustomerPolicy:
    return ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal(credit_limit)),
        max_open_debts=MaxOpenDebts(max_open_debts),
        list_status=list_status,
    )


def _event(
    event_type: AuditEventType,
    object_type: AuditObjectType,
    metadata: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        actor_kind=AuditActorKind.USER,
        actor_user_id=_ACTOR_ID,
        object_type=object_type,
        object_id=_OBJECT_ID,
        occurred_at=_NOW,
        candidate_metadata=metadata,
    )


def test_m12_audit_contracts_have_exact_event_object_and_safe_payload_shapes() -> None:
    linked = ShopCustomerLinkedAuditPayload(
        policy=_policy(), revision=ShopCustomerRevision(1)
    )
    policy_updated = ShopCustomerPolicyUpdatedAuditPayload(
        old_policy=_policy(),
        new_policy=_policy(
            credit_limit="900000",
            max_open_debts=3,
            list_status=ShopCustomerListStatus.WHITELISTED,
        ),
        revision=ShopCustomerRevision(2),
    )
    defaults_updated = ShopCustomerDefaultsUpdatedAuditPayload(
        old_defaults=ShopDefaultCreditPolicy(),
        new_defaults=ShopDefaultCreditPolicy(
            credit_limit=CreditLimitUzbekistanSom(Decimal("900000")),
            max_open_debts=MaxOpenDebts(3),
        ),
    )

    assert redact_audit_payload(
        _event(
            AuditEventType.SHOP_CUSTOMER_LINKED,
            AuditObjectType.SHOP_CUSTOMER,
            dict(linked.as_candidate_metadata()),
        )
    ) == {
        "outcome": "created",
        "credit_limit_uzs": 1000000,
        "max_open_debts": 2,
        "list_status": "normal",
        "revision": 1,
    }
    assert redact_audit_payload(
        _event(
            AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED,
            AuditObjectType.SHOP_CUSTOMER,
            dict(policy_updated.as_candidate_metadata()),
        )
    ) == {
        "old_credit_limit_uzs": 1000000,
        "new_credit_limit_uzs": 900000,
        "old_max_open_debts": 2,
        "new_max_open_debts": 3,
        "old_list_status": "normal",
        "new_list_status": "whitelisted",
        "revision": 2,
    }
    assert redact_audit_payload(
        _event(
            AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED,
            AuditObjectType.SHOP,
            dict(defaults_updated.as_candidate_metadata()),
        )
    ) == {
        "old_default_credit_limit_uzs": 1000000,
        "new_default_credit_limit_uzs": 900000,
        "old_default_max_open_debts": 2,
        "new_default_max_open_debts": 3,
    }


def test_m12_audit_payloads_drop_sensitive_metadata_and_representations() -> None:
    payload = ShopCustomerLinkedAuditPayload(
        policy=_policy(), revision=ShopCustomerRevision(1)
    )
    forbidden = {
        "phone": "+998901234567",
        "customer_id": _OBJECT_ID,
        "shop_customer_id": _OBJECT_ID,
        "session": "secret-session",
        "provider": "secret-provider-detail",
    }
    metadata = {**payload.as_candidate_metadata(), **forbidden}
    event = _event(
        AuditEventType.SHOP_CUSTOMER_LINKED,
        AuditObjectType.SHOP_CUSTOMER,
        metadata,
    )

    redacted = redact_audit_payload(event)
    rendered = repr(event) + repr(payload) + repr(redacted)
    assert set(forbidden).isdisjoint(redacted)
    assert all(str(value) not in rendered for value in forbidden.values())
    assert str(_ACTOR_ID) not in rendered
    assert str(_OBJECT_ID) not in rendered

    with pytest.raises(ValueError, match="must be real"):
        ShopCustomerPolicyUpdatedAuditPayload(
            old_policy=_policy(),
            new_policy=_policy(),
            revision=ShopCustomerRevision(2),
        )
