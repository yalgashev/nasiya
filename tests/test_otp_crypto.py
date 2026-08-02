import inspect
import logging
from dataclasses import is_dataclass
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr

import app.otp.crypto as otp_crypto
from app.otp.code import OtpCode
from app.otp.contracts import OtpPurpose
from app.otp.crypto import (
    OtpBrowserBindingDigest,
    OtpCodeMac,
    compute_otp_code_mac,
    derive_browser_binding_digest,
    verify_otp_code_mac,
)

OTP_HMAC_KEY = SecretStr("test-otp-hmac-key-for-golden-vector-32-chars")
OTHER_OTP_HMAC_KEY = SecretStr("other-otp-hmac-key-for-golden-vector-32-chars")
CHALLENGE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_CHALLENGE_ID = UUID("11111111-1111-4111-8111-111111111112")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_USER_ID = UUID("22222222-2222-4222-8222-222222222223")
SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_SESSION_ID = UUID("33333333-3333-4333-8333-333333333334")
CSRF_SECRET = "test-csrf-secret-for-browser-binding"
OTHER_CSRF_SECRET = "other-csrf-secret-for-browser-binding"
RAW_CODE = "004271"
GOLDEN_MAC = "30806ab6408768b3c0785d1ba75304185c8eea7f30013104424c524e85669057"
REGISTRATION_GOLDEN_MAC = (
    "24efd786378b6d7734a4d57e9324cecc9579bcdd749791960d4ae5d6fbcccdce"
)
GOLDEN_BINDING_DIGEST = (
    "e38ce00462277dd8bfd2c2301dd54b03e5b797ab38dd1cfa309aaed4a3fa6922"
)


def test_otp_code_mac_matches_golden_vector() -> None:
    mac = compute_otp_code_mac(
        otp_hmac_key=OTP_HMAC_KEY,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=OtpCode(RAW_CODE),
    )

    assert mac.as_stored_value() == GOLDEN_MAC


def test_registration_otp_code_mac_is_purpose_domain_separated() -> None:
    registration_mac = compute_otp_code_mac(
        otp_hmac_key=OTP_HMAC_KEY,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.REGISTRATION,
        code=OtpCode(RAW_CODE),
    )

    assert registration_mac.as_stored_value() == REGISTRATION_GOLDEN_MAC
    assert registration_mac.as_stored_value() != GOLDEN_MAC
    assert (
        verify_otp_code_mac(
            otp_hmac_key=OTP_HMAC_KEY,
            challenge_id=CHALLENGE_ID,
            user_id=USER_ID,
            purpose=OtpPurpose.REGISTRATION,
            code=OtpCode(RAW_CODE),
            stored_mac=GOLDEN_MAC,
        )
        is False
    )
    assert (
        verify_otp_code_mac(
            otp_hmac_key=OTP_HMAC_KEY,
            challenge_id=CHALLENGE_ID,
            user_id=USER_ID,
            purpose=OtpPurpose.LOGIN,
            code=OtpCode(RAW_CODE),
            stored_mac=registration_mac,
        )
        is False
    )


def test_otp_code_mac_verification_uses_compare_digest(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return True

    monkeypatch.setattr(otp_crypto.hmac, "compare_digest", compare_digest)

    assert verify_otp_code_mac(
        otp_hmac_key=OTP_HMAC_KEY,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=OtpCode(RAW_CODE),
        stored_mac=GOLDEN_MAC,
    )
    assert calls == [(GOLDEN_MAC, GOLDEN_MAC)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"challenge_id": OTHER_CHALLENGE_ID},
        {"user_id": OTHER_USER_ID},
        {"code": OtpCode("004272")},
        {"otp_hmac_key": OTHER_OTP_HMAC_KEY},
    ],
)
def test_otp_code_mac_rejects_substitution(kwargs: dict[str, object]) -> None:
    values = {
        "otp_hmac_key": OTP_HMAC_KEY,
        "challenge_id": CHALLENGE_ID,
        "user_id": USER_ID,
        "purpose": OtpPurpose.LOGIN,
        "code": OtpCode(RAW_CODE),
        "stored_mac": GOLDEN_MAC,
    }
    values.update(kwargs)

    assert verify_otp_code_mac(**values) is False


@pytest.mark.parametrize("stored_mac", ["", "abc", "A" * 64, "0" * 63, "z" * 64])
def test_malformed_stored_mac_is_safe_failure(stored_mac: str) -> None:
    assert (
        verify_otp_code_mac(
            otp_hmac_key=OTP_HMAC_KEY,
            challenge_id=CHALLENGE_ID,
            user_id=USER_ID,
            purpose=OtpPurpose.LOGIN,
            code=OtpCode(RAW_CODE),
            stored_mac=stored_mac,
        )
        is False
    )


def test_otp_code_mac_rejects_client_purpose_strings() -> None:
    with pytest.raises(ValueError, match="OTP purpose must be typed"):
        compute_otp_code_mac(
            otp_hmac_key=OTP_HMAC_KEY,
            challenge_id=CHALLENGE_ID,
            user_id=USER_ID,
            purpose="LOGIN",  # type: ignore[arg-type]
            code=OtpCode(RAW_CODE),
        )

    with pytest.raises(ValueError, match="OTP purpose must be typed"):
        compute_otp_code_mac(
            otp_hmac_key=OTP_HMAC_KEY,
            challenge_id=CHALLENGE_ID,
            user_id=USER_ID,
            purpose="REGISTRATION",  # type: ignore[arg-type]
            code=OtpCode(RAW_CODE),
        )


def test_browser_binding_digest_matches_golden_vector() -> None:
    digest = derive_browser_binding_digest(
        otp_hmac_key=OTP_HMAC_KEY,
        session_id=SESSION_ID,
        csrf_secret=CSRF_SECRET,
    )

    assert digest.as_stored_value() == GOLDEN_BINDING_DIGEST


def test_browser_binding_digest_is_stable_per_session_and_secret() -> None:
    first = derive_browser_binding_digest(
        otp_hmac_key=OTP_HMAC_KEY,
        session_id=SESSION_ID,
        csrf_secret=CSRF_SECRET,
    )
    second = derive_browser_binding_digest(
        otp_hmac_key=OTP_HMAC_KEY,
        session_id=SESSION_ID,
        csrf_secret=CSRF_SECRET,
    )
    different_session = derive_browser_binding_digest(
        otp_hmac_key=OTP_HMAC_KEY,
        session_id=OTHER_SESSION_ID,
        csrf_secret=CSRF_SECRET,
    )
    different_secret = derive_browser_binding_digest(
        otp_hmac_key=OTP_HMAC_KEY,
        session_id=SESSION_ID,
        csrf_secret=OTHER_CSRF_SECRET,
    )

    assert first.as_stored_value() == second.as_stored_value()
    assert different_session.as_stored_value() != first.as_stored_value()
    assert different_secret.as_stored_value() != first.as_stored_value()


def test_crypto_value_objects_repr_str_and_logging_are_redacted(caplog) -> None:
    mac = OtpCodeMac(GOLDEN_MAC)
    digest = OtpBrowserBindingDigest(GOLDEN_BINDING_DIGEST)
    logger = logging.getLogger("tests.otp_crypto")

    with caplog.at_level(logging.INFO):
        logger.info("values %s %r %s %r", mac, mac, digest, digest)

    assert GOLDEN_MAC not in repr(mac)
    assert GOLDEN_MAC not in str(mac)
    assert GOLDEN_BINDING_DIGEST not in repr(digest)
    assert GOLDEN_BINDING_DIGEST not in str(digest)
    assert GOLDEN_MAC not in caplog.text
    assert GOLDEN_BINDING_DIGEST not in caplog.text
    assert "redacted" in caplog.text


@pytest.mark.parametrize(
    "value_object",
    [
        OtpCodeMac(GOLDEN_MAC),
        OtpBrowserBindingDigest(GOLDEN_BINDING_DIGEST),
    ],
)
def test_crypto_value_objects_have_no_generic_serialization_api(
    value_object: object,
) -> None:
    assert not is_dataclass(value_object)
    assert not hasattr(value_object, "__dict__")
    assert not hasattr(value_object, "dict")
    assert not hasattr(value_object, "model_dump")
    assert not hasattr(value_object, "json")
    assert not hasattr(value_object, "value")
    assert not hasattr(value_object, "raw")
    assert not hasattr(value_object, "secret")


def test_crypto_module_has_no_unkeyed_hash_or_equality_mac_compare() -> None:
    source = Path(inspect.getsourcefile(otp_crypto) or "").read_text(encoding="utf-8")

    assert ".sha256(" not in source
    assert "==" not in source
    assert "compare_digest" in source
