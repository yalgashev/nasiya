from __future__ import annotations

from enum import StrEnum
from typing import Final


class OtpPurpose(StrEnum):
    LOGIN = "LOGIN"
    REGISTRATION = "REGISTRATION"


class OtpChallengeStatus(StrEnum):
    PENDING_DISPATCH = "PENDING_DISPATCH"
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    BURNED = "BURNED"
    INVALIDATED = "INVALIDATED"


class OtpDispatchStatus(StrEnum):
    PENDING = "PENDING"
    PREPARED = "PREPARED"
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class OtpChallengeEventAction(StrEnum):
    ISSUED = "ISSUED"
    DISPATCH_PREPARED = "DISPATCH_PREPARED"
    DISPATCH_RESULT = "DISPATCH_RESULT"
    VERIFY_FAILED = "VERIFY_FAILED"
    CONSUMED = "CONSUMED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    BURNED = "BURNED"
    INVALIDATED_BY_LINK_CHANGE = "INVALIDATED_BY_LINK_CHANGE"
    INVALIDATED_BY_REGISTRATION_STATE_CHANGE = (
        "INVALIDATED_BY_REGISTRATION_STATE_CHANGE"
    )


class OtpInternalOutcome(StrEnum):
    OTP_NOT_ELIGIBLE = "OTP_NOT_ELIGIBLE"
    OTP_PENDING = "OTP_PENDING"
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    OTP_SUPERSEDED = "OTP_SUPERSEDED"
    OTP_BURNED = "OTP_BURNED"
    OTP_LINK_CHANGED = "OTP_LINK_CHANGED"
    OTP_CONSUMED = "OTP_CONSUMED"
    OTP_DELIVERY_FAILED = "OTP_DELIVERY_FAILED"
    OTP_DELIVERY_UNKNOWN = "OTP_DELIVERY_UNKNOWN"
    OTP_CONFIGURATION_UNAVAILABLE = "OTP_CONFIGURATION_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    CSRF_FAILED = "CSRF_FAILED"


class OtpPublicOutcome(StrEnum):
    GENERIC_ACCEPTED = "GENERIC_ACCEPTED"
    GENERIC_INVALID = "GENERIC_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    CSRF_FAILED = "CSRF_FAILED"
    AUTHENTICATED = "AUTHENTICATED"


class OtpDeliveryFailureCode(StrEnum):
    TELEGRAM_FATAL_CREDENTIAL = "TELEGRAM_FATAL_CREDENTIAL"
    TELEGRAM_PROTOCOL = "TELEGRAM_PROTOCOL"
    TELEGRAM_TRANSIENT_NETWORK = "TELEGRAM_TRANSIENT_NETWORK"
    TELEGRAM_TRANSIENT_RATE_LIMIT = "TELEGRAM_TRANSIENT_RATE_LIMIT"
    TELEGRAM_TRANSIENT_SERVER = "TELEGRAM_TRANSIENT_SERVER"
    TELEGRAM_UNKNOWN = "TELEGRAM_UNKNOWN"


_GENERIC_ACCEPTED_OUTCOMES: Final = frozenset(
    {
        OtpInternalOutcome.OTP_NOT_ELIGIBLE,
        OtpInternalOutcome.OTP_PENDING,
        OtpInternalOutcome.OTP_DELIVERY_FAILED,
        OtpInternalOutcome.OTP_DELIVERY_UNKNOWN,
        OtpInternalOutcome.OTP_CONFIGURATION_UNAVAILABLE,
    }
)
_GENERIC_INVALID_OUTCOMES: Final = frozenset(
    {
        OtpInternalOutcome.OTP_INVALID,
        OtpInternalOutcome.OTP_EXPIRED,
        OtpInternalOutcome.OTP_SUPERSEDED,
        OtpInternalOutcome.OTP_BURNED,
        OtpInternalOutcome.OTP_LINK_CHANGED,
    }
)


def parse_otp_purpose(value: str) -> OtpPurpose:
    try:
        return OtpPurpose(value)
    except ValueError:
        raise ValueError("Unknown OTP purpose") from None


def parse_challenge_status(value: str) -> OtpChallengeStatus:
    try:
        return OtpChallengeStatus(value)
    except ValueError:
        raise ValueError("Unknown OTP challenge status") from None


def parse_dispatch_status(value: str) -> OtpDispatchStatus:
    try:
        return OtpDispatchStatus(value)
    except ValueError:
        raise ValueError("Unknown OTP dispatch status") from None


def parse_event_action(value: str) -> OtpChallengeEventAction:
    try:
        return OtpChallengeEventAction(value)
    except ValueError:
        raise ValueError("Unknown OTP event action") from None


def parse_delivery_failure_code(value: str) -> OtpDeliveryFailureCode:
    try:
        return OtpDeliveryFailureCode(value)
    except ValueError:
        raise ValueError("Unknown OTP delivery failure code") from None


def map_internal_outcome_to_public(
    outcome: OtpInternalOutcome,
) -> OtpPublicOutcome:
    if not isinstance(outcome, OtpInternalOutcome):
        raise ValueError("Unknown OTP internal outcome")
    if outcome in _GENERIC_ACCEPTED_OUTCOMES:
        return OtpPublicOutcome.GENERIC_ACCEPTED
    if outcome in _GENERIC_INVALID_OUTCOMES:
        return OtpPublicOutcome.GENERIC_INVALID
    if outcome is OtpInternalOutcome.RATE_LIMITED:
        return OtpPublicOutcome.RATE_LIMITED
    if outcome is OtpInternalOutcome.CSRF_FAILED:
        return OtpPublicOutcome.CSRF_FAILED
    if outcome is OtpInternalOutcome.OTP_CONSUMED:
        return OtpPublicOutcome.AUTHENTICATED
    raise ValueError("Unknown OTP internal outcome")
