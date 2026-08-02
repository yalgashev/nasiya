from dataclasses import fields
from datetime import UTC, datetime
from http import HTTPStatus
from uuid import uuid4

import pytest

from app.audit.contracts import (
    CUSTOMER_ACTIVATION_FROM_STATUS,
    CUSTOMER_ACTIVATION_METHOD,
    CUSTOMER_ACTIVATION_TO_STATUS,
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    CustomerActivatedAuditPayload,
)
from app.audit.redaction import redact_audit_payload
from app.auth.error_codes import ErrorCode, get_error_definition
from app.customer_activation.contracts import (
    RegistrationReadinessComponent,
    RegistrationReadinessComponentStatus,
    RegistrationReadinessComponentView,
    RegistrationReadinessState,
    RegistrationReadinessView,
)
from app.customer_activation.presentation import (
    CustomerActivationWebCopy,
    get_customer_activation_copy,
    get_customer_activation_error_message,
    present_customer_activation_readiness,
    resolve_customer_activation_language,
)
from app.otp.web_presentation import OtpWebLanguage

M11_ERROR_CODES = (
    ErrorCode.OTP_INVALID,
    ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
    ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
    ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
)


def build_readiness(
    state: RegistrationReadinessState,
) -> RegistrationReadinessView:
    return RegistrationReadinessView(
        state=state,
        components=tuple(
            RegistrationReadinessComponentView(
                component=component,
                status=RegistrationReadinessComponentStatus.COMPLETE,
            )
            for component in RegistrationReadinessComponent
        ),
    )


def build_activation_audit(metadata: dict[str, object]) -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.CUSTOMER_ACTIVATED,
        actor_kind=AuditActorKind.USER,
        actor_user_id=uuid4(),
        object_type=AuditObjectType.CUSTOMER,
        object_id=uuid4(),
        occurred_at=datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
        candidate_metadata=metadata,
    )


def test_m11_error_catalog_additions_have_safe_stable_status_and_message() -> None:
    expected_statuses = {
        ErrorCode.OTP_INVALID: HTTPStatus.UNPROCESSABLE_ENTITY,
        ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED: HTTPStatus.CONFLICT,
        ErrorCode.CUSTOMER_ACTIVATION_CHANGED: HTTPStatus.CONFLICT,
        ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER: HTTPStatus.CONFLICT,
    }

    for code in M11_ERROR_CODES:
        definition = get_error_definition(code)
        assert definition.http_status == expected_statuses[code]
        assert definition.user_message
        rendered = definition.user_message.casefold()
        assert all(
            forbidden not in rendered
            for forbidden in (
                "uuid",
                "jshshir",
                "session",
                "constraint",
                "provider",
                "sql",
            )
        )


def test_customer_activated_audit_contract_emits_exact_three_constants() -> None:
    metadata = CustomerActivatedAuditPayload().as_candidate_metadata()
    event = build_activation_audit(dict(metadata))

    assert event.object_type is AuditObjectType.CUSTOMER
    assert redact_audit_payload(event) == {
        "from_status": CUSTOMER_ACTIVATION_FROM_STATUS,
        "to_status": CUSTOMER_ACTIVATION_TO_STATUS,
        "activation_method": CUSTOMER_ACTIVATION_METHOD,
    }


def test_customer_activated_audit_drops_unknown_sensitive_candidate_keys() -> None:
    metadata = dict(CustomerActivatedAuditPayload().as_candidate_metadata())
    metadata.update(
        {
            "raw_otp": "123456",
            "customer_id": str(uuid4()),
            "phone": "+998900000000",
            "session_token": "secret-session-token",
            "jshshir": "12345678901234",
        }
    )

    payload = redact_audit_payload(build_activation_audit(metadata))

    assert tuple(payload) == ("from_status", "to_status", "activation_method")
    assert not set(metadata).difference(payload).intersection(payload)
    assert "123456" not in repr(payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("from_status", "active"),
        ("to_status", "draft"),
        ("activation_method", "CUSTOM"),
    ],
)
def test_customer_activated_audit_rejects_invalid_constants(
    key: str,
    value: str,
) -> None:
    metadata = dict(CustomerActivatedAuditPayload().as_candidate_metadata())
    metadata[key] = value

    with pytest.raises(ValueError, match="Audit activation"):
        redact_audit_payload(build_activation_audit(metadata))


def test_readiness_and_completed_presentations_are_pii_free() -> None:
    ready = present_customer_activation_readiness(
        build_readiness(RegistrationReadinessState.READY_FOR_OTP)
    )
    completed = present_customer_activation_readiness(
        build_readiness(RegistrationReadinessState.ACTIVE)
    )

    assert ready.ready_for_otp is True
    assert ready.completed is False
    assert completed.completed is True
    assert completed.ready_for_otp is False
    assert not hasattr(ready, "customer_id")
    assert not hasattr(ready, "identity_revision")
    assert not hasattr(ready, "document_id")


def test_uz_latn_and_ru_copy_contracts_have_exact_key_parity() -> None:
    uz_copy = get_customer_activation_copy(OtpWebLanguage.UZ_LATN)
    ru_copy = get_customer_activation_copy(OtpWebLanguage.RU)

    assert isinstance(uz_copy, CustomerActivationWebCopy)
    assert tuple(field.name for field in fields(uz_copy)) == tuple(
        field.name for field in fields(ru_copy)
    )
    assert all(getattr(uz_copy, field.name) for field in fields(uz_copy))
    assert all(getattr(ru_copy, field.name) for field in fields(ru_copy))
    assert resolve_customer_activation_language(None, "en-US") is OtpWebLanguage.UZ_LATN
    for language in OtpWebLanguage:
        assert all(
            get_customer_activation_error_message(language, code)
            for code in M11_ERROR_CODES
        )
