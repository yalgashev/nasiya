import pytest

from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDeliveryFailureCode,
    OtpDispatchStatus,
    OtpInternalOutcome,
    OtpPublicOutcome,
    OtpPurpose,
    map_internal_outcome_to_public,
    parse_challenge_status,
    parse_delivery_failure_code,
    parse_dispatch_status,
    parse_event_action,
    parse_otp_purpose,
)


def test_otp_purpose_is_login_only() -> None:
    assert {purpose.value for purpose in OtpPurpose} == {"LOGIN"}
    assert parse_otp_purpose("LOGIN") is OtpPurpose.LOGIN

    with pytest.raises(ValueError, match="Unknown OTP purpose"):
        parse_otp_purpose("STEP_UP")


def test_challenge_statuses_are_closed() -> None:
    assert {status.value for status in OtpChallengeStatus} == {
        "PENDING_DISPATCH",
        "ACTIVE",
        "CONSUMED",
        "SUPERSEDED",
        "EXPIRED",
        "BURNED",
        "INVALIDATED",
    }
    assert parse_challenge_status("ACTIVE") is OtpChallengeStatus.ACTIVE

    with pytest.raises(ValueError, match="Unknown OTP challenge status"):
        parse_challenge_status("DELIVERED")


def test_dispatch_statuses_are_closed_without_delivered_or_read() -> None:
    assert {status.value for status in OtpDispatchStatus} == {
        "PENDING",
        "PREPARED",
        "SENT",
        "FAILED",
        "UNKNOWN",
        "CANCELLED",
    }
    assert "DELIVERED" not in {status.value for status in OtpDispatchStatus}
    assert "READ" not in {status.value for status in OtpDispatchStatus}
    assert parse_dispatch_status("UNKNOWN") is OtpDispatchStatus.UNKNOWN

    with pytest.raises(ValueError, match="Unknown OTP dispatch status"):
        parse_dispatch_status("DELIVERED")


def test_event_actions_are_closed() -> None:
    assert {action.value for action in OtpChallengeEventAction} == {
        "ISSUED",
        "DISPATCH_PREPARED",
        "DISPATCH_RESULT",
        "VERIFY_FAILED",
        "CONSUMED",
        "SUPERSEDED",
        "EXPIRED",
        "BURNED",
        "INVALIDATED_BY_LINK_CHANGE",
    }
    assert parse_event_action("ISSUED") is OtpChallengeEventAction.ISSUED

    with pytest.raises(ValueError, match="Unknown OTP event action"):
        parse_event_action("RAW_CODE_STORED")


def test_public_mapping_keeps_pre_auth_failures_generic() -> None:
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.OTP_NOT_ELIGIBLE)
        is OtpPublicOutcome.GENERIC_ACCEPTED
    )
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.OTP_DELIVERY_UNKNOWN)
        is OtpPublicOutcome.GENERIC_ACCEPTED
    )
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.OTP_CONFIGURATION_UNAVAILABLE)
        is OtpPublicOutcome.GENERIC_ACCEPTED
    )
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.OTP_INVALID)
        is OtpPublicOutcome.GENERIC_INVALID
    )
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.OTP_LINK_CHANGED)
        is OtpPublicOutcome.GENERIC_INVALID
    )
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.RATE_LIMITED)
        is OtpPublicOutcome.RATE_LIMITED
    )
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.CSRF_FAILED)
        is OtpPublicOutcome.CSRF_FAILED
    )
    assert (
        map_internal_outcome_to_public(OtpInternalOutcome.OTP_CONSUMED)
        is OtpPublicOutcome.AUTHENTICATED
    )

    with pytest.raises(ValueError, match="Unknown OTP internal outcome"):
        map_internal_outcome_to_public("OTP_INVALID")  # type: ignore[arg-type]


def test_delivery_failure_code_allowlist_is_sanitized_and_closed() -> None:
    assert {code.value for code in OtpDeliveryFailureCode} == {
        "TELEGRAM_FATAL_CREDENTIAL",
        "TELEGRAM_PROTOCOL",
        "TELEGRAM_TRANSIENT_NETWORK",
        "TELEGRAM_TRANSIENT_RATE_LIMIT",
        "TELEGRAM_TRANSIENT_SERVER",
        "TELEGRAM_UNKNOWN",
    }
    assert (
        parse_delivery_failure_code("TELEGRAM_TRANSIENT_NETWORK")
        is OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_NETWORK
    )

    with pytest.raises(ValueError, match="Unknown OTP delivery failure code"):
        parse_delivery_failure_code("raw chat 123456")
