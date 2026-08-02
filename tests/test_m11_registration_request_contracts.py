from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.customer_activation.contracts import (
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    CustomerAlreadyActive,
    RegistrationOtpCooldown,
    RegistrationOtpPendingDelivery,
    RegistrationOtpPrerequisiteFailed,
    RegistrationOtpRateLimited,
    RegistrationPrerequisiteError,
    RequestNewRegistrationOtpCode,
    RequestRegistrationOtp,
)
from app.otp.crypto import OtpBrowserBindingDigest
from app.telegram.client_ip import ResolvedClientIp

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
_DIGEST = "b" * 64
_IP = "203.0.113.31"
_NOW = datetime(2026, 8, 2, 10, 15, tzinfo=UTC)


def _actor() -> CustomerActivationActor:
    return CustomerActivationActor(user_id=_USER_ID)


def _browser() -> CustomerActivationBrowserContext:
    return CustomerActivationBrowserContext(
        current_session_id=_SESSION_ID,
        browser_binding_digest=OtpBrowserBindingDigest(_DIGEST),
    )


@pytest.mark.parametrize(
    "command_type",
    [RequestRegistrationOtp, RequestNewRegistrationOtpCode],
)
def test_issue_and_new_code_commands_have_only_server_context(command_type) -> None:
    command = command_type(
        actor=_actor(),
        browser=_browser(),
        trusted_client_ip=ResolvedClientIp(_IP),
        now=_NOW,
    )

    assert tuple(field.name for field in fields(command)) == (
        "actor",
        "browser",
        "trusted_client_ip",
        "now",
    )
    assert command.now == _NOW
    forbidden = {
        "phone",
        "purpose",
        "customer_id",
        "challenge_id",
        "dispatch_id",
        "telegram_link_id",
        "acceptance_id",
        "identity_id",
        "document_id",
        "object_file_id",
        "code",
        "raw_code",
    }
    assert forbidden.isdisjoint(field.name for field in fields(command))


def test_issue_and_new_code_commands_require_typed_aware_context() -> None:
    base = {
        "actor": _actor(),
        "browser": _browser(),
        "trusted_client_ip": ResolvedClientIp(_IP),
        "now": _NOW,
    }
    invalid = (
        {"actor": _USER_ID},
        {"browser": _SESSION_ID},
        {"trusted_client_ip": _IP},
        {"now": datetime(2026, 8, 2, 10, 15)},
    )

    for override in invalid:
        with pytest.raises((TypeError, ValueError)):
            RequestRegistrationOtp(**{**base, **override})  # type: ignore[arg-type]


def test_command_and_context_repr_redact_identity_session_digest_ip_and_time() -> None:
    command = RequestNewRegistrationOtpCode(
        actor=_actor(),
        browser=_browser(),
        trusted_client_ip=ResolvedClientIp(_IP),
        now=_NOW,
    )
    rendered = " ".join((repr(command), repr(command.actor), repr(command.browser)))

    for forbidden in (
        str(_USER_ID),
        str(_SESSION_ID),
        _DIGEST,
        _IP,
        _NOW.isoformat(),
    ):
        assert forbidden not in rendered
    assert "redacted" in rendered


def test_request_results_are_exact_safe_identifier_free_variants() -> None:
    results = (
        RegistrationOtpPendingDelivery(),
        CustomerAlreadyActive(),
        RegistrationOtpPrerequisiteFailed(
            RegistrationPrerequisiteError.CUSTOMER_IDENTITY_UNAVAILABLE
        ),
        RegistrationOtpCooldown(),
        RegistrationOtpRateLimited(),
    )

    assert [type(result).__name__ for result in results] == [
        "RegistrationOtpPendingDelivery",
        "CustomerAlreadyActive",
        "RegistrationOtpPrerequisiteFailed",
        "RegistrationOtpCooldown",
        "RegistrationOtpRateLimited",
    ]
    forbidden_fields = {
        "challenge_id",
        "dispatch_id",
        "provider_status",
        "delivery_status",
        "telegram_link_id",
        "code",
    }
    for result in results:
        assert forbidden_fields.isdisjoint(field.name for field in fields(result))


def test_new_code_contract_cannot_carry_or_resend_an_old_code() -> None:
    command_fields = {field.name for field in fields(RequestNewRegistrationOtpCode)}

    assert {"code", "raw_code", "old_code", "challenge_id"}.isdisjoint(command_fields)
    with pytest.raises(TypeError):
        RequestNewRegistrationOtpCode(
            actor=_actor(),
            browser=_browser(),
            trusted_client_ip=ResolvedClientIp(_IP),
            now=_NOW,
            raw_code="123456",  # type: ignore[call-arg]
        )
