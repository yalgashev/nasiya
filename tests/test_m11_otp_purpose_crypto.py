from __future__ import annotations

import ast
import inspect
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.otp.crypto as otp_crypto_module
import app.otp.dispatcher as otp_dispatcher_module
from app.auth.models import User
from app.customer.models import Customer
from app.customer_document.models import CustomerDocument
from app.offers.models import OfferAcceptance
from app.otp.code import OtpCode, generate_otp_code
from app.otp.contracts import OtpChallengeEventAction, OtpPurpose
from app.otp.crypto import (
    OtpBrowserBindingDigest,
    OtpCodeMac,
    compute_otp_code_mac,
    derive_browser_binding_digest,
    verify_otp_code_mac,
)
from app.otp.models import OtpChallenge
from app.otp.repository import activate_challenge, create_pending_challenge
from app.settings import OtpHmacKeySettingsError, Settings
from app.telegram.models import TelegramLink

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OTP_SOURCE_ROOT = PROJECT_ROOT / "app" / "otp"
OTP_KEY = SecretStr("test-otp-hmac-key-for-golden-vector-32-chars")
ROTATED_OTP_KEY = SecretStr("m11-purpose-crypto-rotated-key-at-least-32-characters")
CHALLENGE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
LOGIN_GOLDEN_MAC = "30806ab6408768b3c0785d1ba75304185c8eea7f30013104424c524e85669057"
REGISTRATION_GOLDEN_MAC = (
    "24efd786378b6d7734a4d57e9324cecc9579bcdd749791960d4ae5d6fbcccdce"
)


def _code() -> OtpCode:
    return OtpCode("004271")


def _compute(purpose: OtpPurpose, *, key: SecretStr = OTP_KEY) -> OtpCodeMac:
    return compute_otp_code_mac(
        otp_hmac_key=key,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=purpose,
        code=_code(),
    )


def test_otp_purpose_and_event_action_sets_are_exact() -> None:
    assert tuple(OtpPurpose) == (OtpPurpose.LOGIN, OtpPurpose.REGISTRATION)
    assert tuple(OtpChallengeEventAction) == (
        OtpChallengeEventAction.ISSUED,
        OtpChallengeEventAction.DISPATCH_PREPARED,
        OtpChallengeEventAction.DISPATCH_RESULT,
        OtpChallengeEventAction.VERIFY_FAILED,
        OtpChallengeEventAction.CONSUMED,
        OtpChallengeEventAction.SUPERSEDED,
        OtpChallengeEventAction.EXPIRED,
        OtpChallengeEventAction.BURNED,
        OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE,
        OtpChallengeEventAction.INVALIDATED_BY_REGISTRATION_STATE_CHANGE,
    )


def test_login_mac_golden_vector_is_unchanged() -> None:
    assert _compute(OtpPurpose.LOGIN).as_stored_value() == LOGIN_GOLDEN_MAC


def test_registration_mac_is_domain_separated_and_uses_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _compute(OtpPurpose.REGISTRATION)
    compare_calls: list[tuple[str, str]] = []
    original_compare = otp_crypto_module.hmac.compare_digest

    def compare_spy(left: str, right: str) -> bool:
        compare_calls.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(
        otp_crypto_module.hmac,
        "compare_digest",
        compare_spy,
    )

    assert registration.as_stored_value() == REGISTRATION_GOLDEN_MAC
    assert registration.as_stored_value() != LOGIN_GOLDEN_MAC
    assert not verify_otp_code_mac(
        otp_hmac_key=OTP_KEY,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.REGISTRATION,
        code=_code(),
        stored_mac=LOGIN_GOLDEN_MAC,
    )
    assert not verify_otp_code_mac(
        otp_hmac_key=OTP_KEY,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.LOGIN,
        code=_code(),
        stored_mac=registration,
    )
    assert len(compare_calls) == 2
    assert all(len(left) == len(right) == 64 for left, right in compare_calls)


def test_six_ascii_digits_leading_zero_and_binding_substitutions_are_exact() -> None:
    generated = generate_otp_code(lambda upper_bound: 42)
    first_binding = derive_browser_binding_digest(
        otp_hmac_key=OTP_KEY,
        session_id=SESSION_ID,
        csrf_secret="synthetic-browser-secret",
    )
    rotated_binding = derive_browser_binding_digest(
        otp_hmac_key=OTP_KEY,
        session_id=SESSION_ID,
        csrf_secret="synthetic-rotated-browser-secret",
    )

    assert generated.as_internal_value() == "000042"
    assert first_binding.as_stored_value() != rotated_binding.as_stored_value()
    assert not verify_otp_code_mac(
        otp_hmac_key=OTP_KEY,
        challenge_id=CHALLENGE_ID,
        user_id=UUID("22222222-2222-4222-8222-222222222223"),
        purpose=OtpPurpose.REGISTRATION,
        code=_code(),
        stored_mac=REGISTRATION_GOLDEN_MAC,
    )
    for malformed in ("4271", "0000042", "１２３４５６", "42 710"):
        with pytest.raises(ValueError, match="six ASCII digits") as exc_info:
            OtpCode(malformed)
        assert malformed not in str(exc_info.value)


def test_missing_or_rotated_key_fails_closed_without_secret_disclosure() -> None:
    settings = Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url="postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key="m11-purpose-missing-key-rate-secret-at-least-32-chars",
    )
    with pytest.raises(OtpHmacKeySettingsError) as exc_info:
        settings.require_otp_hmac_key()

    assert "m11-purpose" not in str(exc_info.value)
    assert not verify_otp_code_mac(
        otp_hmac_key=ROTATED_OTP_KEY,
        challenge_id=CHALLENGE_ID,
        user_id=USER_ID,
        purpose=OtpPurpose.REGISTRATION,
        code=_code(),
        stored_mac=REGISTRATION_GOLDEN_MAC,
    )


def test_otp_code_mac_and_keys_remain_redacted_from_all_sinks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    code = _code()
    mac = _compute(OtpPurpose.REGISTRATION)
    binding = OtpBrowserBindingDigest("a" * 64)
    logger = logging.getLogger("tests.m11.otp.secret-containment")

    with caplog.at_level(logging.INFO):
        logger.info("otp values %s %r %s %r %s", code, code, mac, binding, OTP_KEY)
    rendered = " ".join(
        (
            repr(code),
            str(code),
            repr(mac),
            str(mac),
            repr(binding),
            str(binding),
            repr(OTP_KEY),
            str(OTP_KEY),
            caplog.text,
        )
    )

    assert "004271" not in rendered
    assert REGISTRATION_GOLDEN_MAC not in rendered
    assert "test-otp-hmac-key-for-golden-vector-32-chars" not in rendered
    assert "a" * 64 not in rendered
    assert "redacted" in rendered


@pytest.mark.integration
def test_database_persists_mac_only_and_never_raw_or_reversible_code(
    m2_test_database: Engine,
) -> None:
    assert {
        Customer.__tablename__,
        CustomerDocument.__tablename__,
        OfferAcceptance.__tablename__,
    } == {"customers", "customer_documents", "offer_acceptances"}
    now = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    with Session(m2_test_database) as session, session.begin():
        user = User(
            phone="+998900001398",
            password_hash=None,
            is_active=True,
            is_platform_admin=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
        link = TelegramLink(
            user_id=user.id,
            telegram_chat_id=9_980_001_398,
            linked_at=now,
            updated_at=now,
        )
        session.add(link)
        session.flush()
        challenge = create_pending_challenge(
            session,
            browser_binding_digest=OtpBrowserBindingDigest("b" * 64),
            now=now,
            purpose=OtpPurpose.LOGIN,
            user_id=user.id,
            telegram_link_id=link.id,
            telegram_linked_at=link.linked_at,
        )
        mac = compute_otp_code_mac(
            otp_hmac_key=OTP_KEY,
            challenge_id=challenge.id,
            user_id=user.id,
            purpose=OtpPurpose.LOGIN,
            code=OtpCode("004271"),
        )
        activate_challenge(
            session,
            challenge=challenge,
            code_mac=mac,
            activated_at=now,
            expires_at=now + timedelta(minutes=3),
        )
        session.flush()
        challenge_id = challenge.id

    with Session(m2_test_database) as session:
        stored = session.get(OtpChallenge, challenge_id)
        column_names = tuple(
            session.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'otp_challenges' ORDER BY ordinal_position"
                )
            )
        )
        stored_mac = session.scalar(
            select(OtpChallenge.code_mac).where(OtpChallenge.id == challenge_id)
        )

    assert stored is not None
    assert stored_mac == mac.as_stored_value()
    assert "004271" not in repr(stored)
    assert stored_mac not in repr(stored)
    assert "code_mac" in column_names
    assert {"code", "raw_code", "encrypted_code", "otp_hmac_key"}.isdisjoint(
        column_names
    )


def test_otp_source_has_one_hmac_and_one_raw_code_send_boundary() -> None:
    otp_sources = {
        path: path.read_text(encoding="utf-8")
        for path in sorted(OTP_SOURCE_ROOT.glob("*.py"))
    }
    imported_roots: set[str] = set()
    for path, source in otp_sources.items():
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    crypto_source = otp_sources[OTP_SOURCE_ROOT / "crypto.py"]
    combined_source = "\n".join(otp_sources.values())
    send_source = inspect.getsource(otp_dispatcher_module._send_prepared_otp)

    assert imported_roots.isdisjoint(
        {"Crypto", "cryptography", "jwt", "nacl", "passlib"}
    )
    assert crypto_source.count("hmac.new(") == 1
    assert crypto_source.count("hmac.compare_digest(") == 1
    assert combined_source.count("prepared.code") == 1
    assert "prepared.code" in send_source
    assert all(
        marker not in combined_source.casefold()
        for marker in (
            "encrypted_code",
            "decrypt_code",
            "reversible_code",
            "pickle.dumps",
            "base64.b64encode",
        )
    )
    for source_path in (
        OTP_SOURCE_ROOT / "code.py",
        OTP_SOURCE_ROOT / "crypto.py",
        PROJECT_ROOT / "app" / "customer_activation" / "service.py",
        PROJECT_ROOT / "app" / "customer_activation" / "router.py",
    ):
        source = source_path.read_text(encoding="utf-8").casefold()
        assert "logging." not in source
        assert "logger." not in source
        assert "print(" not in source
