import logging
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from app.otp.code import OtpCode
from app.otp.contracts import OtpPurpose
from app.otp.crypto import compute_otp_code_mac, verify_otp_code_mac
from app.settings import Settings

TEST_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya"
RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-leakage"
OLD_OTP_HMAC_KEY = "old-otp-hmac-key-for-rotation-leakage-32-chars"
NEW_OTP_HMAC_KEY = "new-otp-hmac-key-for-rotation-leakage-32-chars"
RAW_CODE = "004271"
CHALLENGE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")


def test_otp_key_rotation_makes_existing_mac_unverifiable() -> None:
    old_key = SecretStr(OLD_OTP_HMAC_KEY)
    new_key = SecretStr(NEW_OTP_HMAC_KEY)
    mac = compute_otp_code_mac(
        otp_hmac_key=old_key,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=OtpCode(RAW_CODE),
    )

    assert verify_otp_code_mac(
        otp_hmac_key=old_key,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=OtpCode(RAW_CODE),
        stored_mac=mac,
    )
    assert (
        verify_otp_code_mac(
            otp_hmac_key=new_key,
            challenge_id=CHALLENGE_ID,
            user_id=USER_ID,
            purpose=OtpPurpose.LOGIN,
            code=OtpCode(RAW_CODE),
            stored_mac=mac,
        )
        is False
    )


def test_secret_and_code_do_not_appear_in_logs_or_settings_dump(caplog) -> None:
    settings = Settings(
        _env_file=None,
        debug=False,
        database_url=TEST_DATABASE_URL,
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=OLD_OTP_HMAC_KEY,
    )
    code = OtpCode(RAW_CODE)
    mac = compute_otp_code_mac(
        otp_hmac_key=settings.require_otp_hmac_key(),
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=code,
    )
    logger = logging.getLogger("tests.otp_secret_leakage")

    with caplog.at_level(logging.INFO):
        logger.info("settings %r", settings.model_dump())
        logger.info("objects %s %r %s %r", code, code, mac, mac)

    leak_surface = "\n".join(
        [
            repr(settings.model_dump()),
            str(settings.require_otp_hmac_key()),
            repr(settings.require_otp_hmac_key()),
            repr(code),
            str(code),
            repr(mac),
            str(mac),
            caplog.text,
        ]
    )
    assert OLD_OTP_HMAC_KEY not in leak_surface
    assert RAW_CODE not in leak_surface


def test_otp_modules_have_no_logging_metrics_keyring_or_fallback() -> None:
    otp_dir = Path(__file__).resolve().parents[1] / "app" / "otp"
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(otp_dir.glob("*.py"))
        if path.name != "__init__.py"
    ).casefold()

    for forbidden in (
        "logging",
        "logger",
        "print(",
        "metric",
        "key_ring",
        "keyring",
        "fallback",
        "plaintext",
        ".sha256(",
        "asdict",
    ):
        assert forbidden not in source_text

    assert "otp_key_rotated_restart_required" not in source_text
