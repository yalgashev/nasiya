from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from uuid import uuid4

import pytest

import app.customer_activation.router as activation_router_module
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
    CUSTOMER_ACTIVATION_PUBLIC_ERROR_CODES,
    CustomerActivationWebCopy,
    get_customer_activation_copy,
    get_customer_activation_error_message,
    present_customer_activation_readiness,
    resolve_customer_activation_language,
)
from app.otp.code import OtpCode
from app.otp.message import format_registration_otp_message
from app.otp.web_presentation import OtpWebLanguage
from app.security_headers import CONTENT_SECURITY_POLICY

M11_ERROR_CODES = (
    ErrorCode.OTP_INVALID,
    ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
    ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
    ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
    ErrorCode.TELEGRAM_CONTACT_REQUIRED,
    ErrorCode.TELEGRAM_PHONE_MISMATCH,
    ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED,
)
ACTIVATION_TEMPLATE = Path("app/templates/customer/activation.html")
APPLICATION_CSS = Path("app/static/css/app.css")
MANUAL_ACTIVATION_MOBILE_CHECKLIST = (
    "Chrome 320px: no horizontal scroll and all actions are fully visible",
    "Chrome 430px: request, verify, and new-code follow thumb-friendly order",
    "Keyboard: every interactive control has a visible focus indicator",
    "Screen reader: OTP label, help, status, and textual errors are announced",
    "Telegram in-app browser: text OTP keyboard and one-time-code hint remain usable",
    "Double submit: server PRG/idempotency remains authoritative",
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
        ErrorCode.TELEGRAM_CONTACT_REQUIRED: HTTPStatus.CONFLICT,
        ErrorCode.TELEGRAM_PHONE_MISMATCH: HTTPStatus.CONFLICT,
        ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED: HTTPStatus.CONFLICT,
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


def test_activation_copy_has_matching_uz_latn_and_ru_keys_with_fallback() -> None:
    uz_copy = get_customer_activation_copy(OtpWebLanguage.UZ_LATN)
    ru_copy = get_customer_activation_copy(OtpWebLanguage.RU)

    assert isinstance(uz_copy, CustomerActivationWebCopy)
    assert tuple(field.name for field in fields(uz_copy)) == tuple(
        field.name for field in fields(ru_copy)
    )
    assert all(getattr(uz_copy, field.name) for field in fields(uz_copy))
    assert all(getattr(ru_copy, field.name) for field in fields(ru_copy))
    with pytest.raises(FrozenInstanceError):
        uz_copy.heading = "mutated"  # type: ignore[misc]
    for language in OtpWebLanguage:
        assert all(
            get_customer_activation_error_message(language, code)
            for code in CUSTOMER_ACTIVATION_PUBLIC_ERROR_CODES
        )


@pytest.mark.parametrize(
    ("locale_cookie", "accept_language", "expected"),
    (
        (None, None, OtpWebLanguage.UZ_LATN),
        (None, "en-US", OtpWebLanguage.UZ_LATN),
        (None, "ru-RU,uz;q=0.8", OtpWebLanguage.RU),
        (None, "uz-Latn,ru;q=0.8", OtpWebLanguage.UZ_LATN),
        ("ru", "uz", OtpWebLanguage.RU),
        ("uz-Latn", "ru", OtpWebLanguage.UZ_LATN),
        ("unsupported", "ru", OtpWebLanguage.RU),
    ),
)
def test_activation_language_resolution_has_uz_fallback_and_cookie_precedence(
    locale_cookie: str | None,
    accept_language: str | None,
    expected: OtpWebLanguage,
) -> None:
    assert (
        resolve_customer_activation_language(locale_cookie, accept_language) is expected
    )


def test_activation_error_copy_is_complete_localized_and_code_free() -> None:
    assert set(CUSTOMER_ACTIVATION_PUBLIC_ERROR_CODES) == {
        ErrorCode.CSRF_FAILED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.CUSTOMER_DRAFT_REQUIRED,
        ErrorCode.TELEGRAM_NOT_LINKED,
        ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED,
        ErrorCode.OFFER_UNAVAILABLE,
        ErrorCode.OTP_INVALID,
        ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
        ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
        ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
    }
    for language in OtpWebLanguage:
        for code in CUSTOMER_ACTIVATION_PUBLIC_ERROR_CODES:
            message = get_customer_activation_error_message(language, code)
            assert message
            assert message != code.value
            assert code.value not in message
            assert "_" not in message


def test_web_and_telegram_registration_copy_are_semantically_consistent() -> None:
    uz_web = get_customer_activation_copy(OtpWebLanguage.UZ_LATN)
    ru_web = get_customer_activation_copy(OtpWebLanguage.RU)
    uz_telegram = format_registration_otp_message(
        code=OtpCode("004271"),
        ttl_seconds=300,
        locale="uz-Latn",
    ).casefold()
    ru_telegram = format_registration_otp_message(
        code=OtpCode("004271"),
        ttl_seconds=300,
        locale="ru",
    ).casefold()

    assert "faollashtirish" in uz_web.heading.casefold()
    assert "faollashtirish" in uz_telegram
    assert "активац" in ru_web.heading.casefold()
    assert "aktivats" in ru_telegram
    for copy in (uz_web, ru_web):
        rendered = " ".join(getattr(copy, field.name) for field in fields(copy))
        assert all(
            forbidden not in rendered.casefold()
            for forbidden in (
                "offer version",
                "offer language",
                "acceptance language",
                "legal review",
            )
        )


def test_activation_templates_autoescape_and_csp_forbid_inline_code() -> None:
    source = ACTIVATION_TEMPLATE.read_text(encoding="utf-8")
    normalized = source.casefold()

    assert "{{ request" not in source
    assert "|safe" not in source
    assert "markup(" not in normalized
    assert "<script" not in normalized
    assert "<style" not in normalized
    assert " style=" not in normalized
    assert "javascript:" not in normalized
    assert all(
        f" on{event}=" not in normalized
        for event in ("click", "submit", "load", "error", "input", "change")
    )


def test_activation_prg_urls_flash_and_headers_are_no_store_and_secret_free() -> None:
    assert ErrorCode.OTP_INVALID in activation_router_module._SAFE_QUERY_ERROR_CODES
    forbidden_values = (
        "004271",
        "+998900001488",
        "12345678901234",
        "SYNTHETIC-DOCUMENT-488",
        str(uuid4()),
        "synthetic-session-cookie-secret",
    )
    responses = [
        activation_router_module._activation_redirect(),
        activation_router_module._activation_redirect(notice="otp-pending"),
        *(
            activation_router_module._activation_redirect(error_code=error_code)
            for error_code in CUSTOMER_ACTIVATION_PUBLIC_ERROR_CODES
        ),
    ]

    for response in responses:
        rendered = " ".join(
            (
                response.headers["location"],
                " ".join(f"{key}: {value}" for key, value in response.headers.items()),
            )
        )
        assert response.status_code == 303
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["location"].startswith("/customer/activation")
        assert all(value not in rendered for value in forbidden_values)


def test_activation_csp_remains_exact_self_only_policy() -> None:
    assert CONTENT_SECURITY_POLICY == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    )


def test_activation_mobile_controls_and_otp_input_contract_are_exact() -> None:
    template = ACTIVATION_TEMPLATE.read_text(encoding="utf-8")
    css = APPLICATION_CSS.read_text(encoding="utf-8")
    compact_template = " ".join(template.split())

    assert '<label for="registration-otp-code">' in template
    assert (
        '<input id="registration-otp-code" name="code" type="text" '
        'inputmode="numeric" autocomplete="one-time-code" minlength="6" '
        'maxlength="6" required aria-describedby="registration-otp-help">'
    ) in compact_template
    assert 'type="number"' not in template.casefold()
    assert 'id="registration-otp-help"' in template
    assert 'role="alert"' in template
    assert 'role="status"' in template
    assert template.index('/otp/request"') < template.index('/otp/verify"')
    assert template.index('/otp/verify"') < template.index('/otp/new-code"')

    assert ".customer-activation-page" in css
    assert "@media (max-width: 430px)" in css
    assert "width: min(100% - 24px, 640px);" in css
    assert "min-height: 44px;" in css
    assert ".customer-activation-page input:focus-visible" in css
    assert ".customer-activation-page button:focus-visible" in css
    assert ".customer-activation-page a:focus-visible" in css
    assert "100vw" not in css
    assert "overflow-x: scroll" not in css
    assert "overflow-x: auto" not in css

    assert len(MANUAL_ACTIVATION_MOBILE_CHECKLIST) == 6
    assert any("320px" in item for item in MANUAL_ACTIVATION_MOBILE_CHECKLIST)
    assert any("430px" in item for item in MANUAL_ACTIVATION_MOBILE_CHECKLIST)
    assert any(
        "Telegram in-app browser" in item for item in MANUAL_ACTIVATION_MOBILE_CHECKLIST
    )
    assert any(
        "server PRG/idempotency" in item for item in MANUAL_ACTIVATION_MOBILE_CHECKLIST
    )
