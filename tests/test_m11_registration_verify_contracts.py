from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.customer_activation.contracts import (
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    RegistrationOtpCandidateLookupKey,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    VerifyRegistrationOtp,
    parse_registration_otp_candidate,
)
from app.otp.contracts import OtpPurpose
from app.otp.crypto import OtpBrowserBindingDigest

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
_DIGEST = "c" * 64
_NOW = datetime(2026, 8, 2, 11, 30, tzinfo=UTC)


def _actor() -> CustomerActivationActor:
    return CustomerActivationActor(_USER_ID)


def _browser() -> CustomerActivationBrowserContext:
    return CustomerActivationBrowserContext(
        current_session_id=_SESSION_ID,
        browser_binding_digest=OtpBrowserBindingDigest(_DIGEST),
    )


def test_verify_command_has_only_server_context_candidate_string_and_time() -> None:
    command = VerifyRegistrationOtp(
        actor=_actor(),
        browser=_browser(),
        candidate_code="004271",
        now=_NOW,
    )

    assert tuple(field.name for field in fields(command)) == (
        "actor",
        "browser",
        "candidate_code",
        "now",
    )
    assert {
        "purpose",
        "customer_id",
        "challenge_id",
        "dispatch_id",
        "telegram_link_id",
        "acceptance_id",
        "identity_id",
        "document_id",
        "object_file_id",
        "session_id",
    }.isdisjoint(field.name for field in fields(command))


def test_verify_command_repr_redacts_code_actor_session_digest_and_time() -> None:
    command = VerifyRegistrationOtp(
        actor=_actor(),
        browser=_browser(),
        candidate_code="004271",
        now=_NOW,
    )
    rendered = repr(command)

    for forbidden in (
        "004271",
        str(_USER_ID),
        str(_SESSION_ID),
        _DIGEST,
        _NOW.isoformat(),
    ):
        assert forbidden not in rendered
    assert "candidate_code=<redacted>" in rendered


@pytest.mark.parametrize("raw_code", ["000000", " 004271\n", "999999"])
def test_registration_candidate_parser_accepts_exact_ascii_code(raw_code: str) -> None:
    candidate = parse_registration_otp_candidate(raw_code)

    assert candidate.requires_dummy_mac is False
    assert candidate.code is not None
    assert candidate.code.as_internal_value() == raw_code.strip()
    assert raw_code.strip() not in repr(candidate)


@pytest.mark.parametrize(
    "raw_code",
    ["", "12345", "1234567", "１２３４５６", "123 456", "ABC123"],
)
def test_malformed_registration_candidate_becomes_redacted_dummy_contract(
    raw_code: str,
) -> None:
    candidate = parse_registration_otp_candidate(raw_code)

    assert candidate.requires_dummy_mac is True
    assert candidate.code is None
    if raw_code:
        assert raw_code not in repr(candidate)


def test_candidate_lookup_key_is_browser_bound_and_internal_registration_only() -> None:
    key = RegistrationOtpCandidateLookupKey(
        browser_binding_digest=OtpBrowserBindingDigest(_DIGEST)
    )

    assert tuple(field.name for field in fields(key)) == (
        "browser_binding_digest",
        "purpose",
    )
    assert key.purpose is OtpPurpose.REGISTRATION
    assert _DIGEST not in repr(key)
    with pytest.raises(TypeError):
        RegistrationOtpCandidateLookupKey(  # type: ignore[call-arg]
            browser_binding_digest=OtpBrowserBindingDigest(_DIGEST),
            purpose=OtpPurpose.LOGIN,
        )


def test_verification_outcomes_are_exact_safe_and_detail_free() -> None:
    assert tuple(outcome.value for outcome in RegistrationOtpVerificationOutcome) == (
        "ACTIVATED",
        "ALREADY_ACTIVE",
        "OTP_INVALID",
        "CUSTOMER_ACTIVATION_CHANGED",
        "RATE_LIMITED",
        "SESSION_EXPIRED",
        "PREREQUISITE_FAILED",
    )
    for outcome in RegistrationOtpVerificationOutcome:
        result = RegistrationOtpVerificationResult(outcome)
        assert tuple(field.name for field in fields(result)) == ("outcome",)
        rendered = repr(result)
        for forbidden in (
            "challenge_id",
            "attempt",
            "gate",
            "customer_id",
            "provider",
        ):
            assert forbidden not in rendered


def test_verify_contract_rejects_untyped_or_naive_values_without_echo() -> None:
    with pytest.raises(TypeError):
        VerifyRegistrationOtp(
            actor=_actor(),
            browser=_browser(),
            candidate_code=4271,  # type: ignore[arg-type]
            now=_NOW,
        )
    with pytest.raises(ValueError):
        VerifyRegistrationOtp(
            actor=_actor(),
            browser=_browser(),
            candidate_code="sensitive-malformed-candidate",
            now=datetime(2026, 8, 2, 11, 30),
        )
