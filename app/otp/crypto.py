from __future__ import annotations

import hashlib
import hmac
import re
from typing import Final
from uuid import UUID

from pydantic import SecretStr

from app.otp.code import OtpCode
from app.otp.contracts import OtpPurpose

_HEX_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_CODE_MAC_DOMAIN: Final = "nasiya.otp.code_mac"
_BROWSER_BINDING_DOMAIN: Final = "nasiya.otp.browser_binding"
_CANONICAL_VERSION: Final = "1"


class OtpCodeMac:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("OTP code MAC must be a string")
        if _HEX_SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("OTP code MAC must be lowercase SHA-256 hex")
        self._value = value

    def as_stored_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "OtpCodeMac(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-otp-code-mac>"


class OtpBrowserBindingDigest:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("OTP browser binding digest must be a string")
        if _HEX_SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("OTP browser binding digest must be lowercase SHA-256 hex")
        self._value = value

    def as_stored_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "OtpBrowserBindingDigest(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-otp-browser-binding-digest>"


def compute_otp_code_mac(
    *,
    otp_hmac_key: SecretStr,
    challenge_id: UUID,
    user_id: UUID,
    purpose: OtpPurpose,
    code: OtpCode,
) -> OtpCodeMac:
    canonical_input = _canonical_otp_code_mac_input(
        challenge_id=challenge_id,
        user_id=user_id,
        purpose=purpose,
        code=code,
    )
    return OtpCodeMac(_hmac_sha256_hex(otp_hmac_key, canonical_input))


def verify_otp_code_mac(
    *,
    otp_hmac_key: SecretStr,
    challenge_id: UUID,
    user_id: UUID,
    purpose: OtpPurpose,
    code: OtpCode,
    stored_mac: OtpCodeMac | str,
) -> bool:
    try:
        normalized_stored_mac = (
            stored_mac if isinstance(stored_mac, OtpCodeMac) else OtpCodeMac(stored_mac)
        )
    except ValueError:
        return False
    expected_mac = compute_otp_code_mac(
        otp_hmac_key=otp_hmac_key,
        challenge_id=challenge_id,
        user_id=user_id,
        purpose=purpose,
        code=code,
    )
    return hmac.compare_digest(
        expected_mac.as_stored_value(),
        normalized_stored_mac.as_stored_value(),
    )


def derive_browser_binding_digest(
    *,
    otp_hmac_key: SecretStr,
    session_id: UUID,
    csrf_secret: str,
) -> OtpBrowserBindingDigest:
    if not isinstance(session_id, UUID):
        raise ValueError("OTP browser binding session id must be a UUID")
    if not isinstance(csrf_secret, str) or not csrf_secret:
        raise ValueError("OTP browser binding session secret is required")
    canonical_input = _canonical_bytes(
        domain_tag=_BROWSER_BINDING_DOMAIN,
        fields=(
            ("session_id", str(session_id)),
            ("csrf_secret", csrf_secret),
        ),
    )
    return OtpBrowserBindingDigest(_hmac_sha256_hex(otp_hmac_key, canonical_input))


def _canonical_otp_code_mac_input(
    *,
    challenge_id: UUID,
    user_id: UUID,
    purpose: OtpPurpose,
    code: OtpCode,
) -> bytes:
    if not isinstance(challenge_id, UUID):
        raise ValueError("OTP challenge id must be a UUID")
    if not isinstance(user_id, UUID):
        raise ValueError("OTP user id must be a UUID")
    if not isinstance(purpose, OtpPurpose):
        raise ValueError("OTP purpose must be typed")
    if not isinstance(code, OtpCode):
        raise ValueError("OTP code is required")
    return _canonical_bytes(
        domain_tag=_CODE_MAC_DOMAIN,
        fields=(
            ("challenge_id", str(challenge_id)),
            ("user_id", str(user_id)),
            ("purpose", purpose.value),
            ("code", code.as_internal_value()),
        ),
    )


def _canonical_bytes(
    *,
    domain_tag: str,
    fields: tuple[tuple[str, str], ...],
) -> bytes:
    canonical_fields = (
        ("version", _CANONICAL_VERSION),
        ("domain", domain_tag),
        *fields,
    )
    output = bytearray()
    for field_name, field_value in canonical_fields:
        encoded_name = field_name.encode("utf-8")
        encoded_value = field_value.encode("utf-8")
        output.extend(len(encoded_name).to_bytes(2, "big"))
        output.extend(encoded_name)
        output.extend(len(encoded_value).to_bytes(4, "big"))
        output.extend(encoded_value)
    return bytes(output)


def _hmac_sha256_hex(otp_hmac_key: SecretStr, canonical_input: bytes) -> str:
    if not isinstance(otp_hmac_key, SecretStr):
        raise ValueError("OTP HMAC key must be configured")
    secret = otp_hmac_key.get_secret_value()
    if not secret:
        raise ValueError("OTP HMAC key must be configured")
    return hmac.new(
        secret.encode("utf-8"),
        canonical_input,
        hashlib.sha256,
    ).hexdigest()
