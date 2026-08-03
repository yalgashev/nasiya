from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.otp.dispatch_service as dispatch_service_module
from app.auth.models import AuthRateLimit, User
from app.customer.models import Customer
from app.customer_activation.contracts import (
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    RegistrationOtpPendingDelivery,
)
from app.customer_activation.presentation import get_customer_activation_copy
from app.customer_activation.rate_limit import RegistrationIssuanceRateLimitPolicy
from app.customer_activation.service import (
    AuthenticatedActivationContext,
    issue_registration_otp,
)
from app.main import create_app
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDeliveryFailureCode,
    OtpDispatchStatus,
    OtpPurpose,
)
from app.otp.crypto import compute_otp_code_mac, verify_otp_code_mac
from app.otp.dispatch_service import (
    prepare_next_otp_dispatch,
    record_otp_delivery_result,
    recover_stale_prepared_dispatches,
)
from app.otp.dispatcher import ShutdownController, build_parser, run_dispatch_loop
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.provider import (
    OtpDeliverySendResult,
    OtpDeliverySendStatus,
    TelegramOtpProvider,
    TelegramOtpTarget,
)
from app.otp.repository import (
    create_pending_dispatch,
    create_pending_registration_challenge,
)
from app.otp.web_presentation import OtpWebLanguage
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink
from tests.m11_seed import (
    NOW,
    REGISTRATION_DIGEST,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)

pytestmark = pytest.mark.integration
OTP_HMAC_KEY = SecretStr("test-m11-dispatcher-hmac-key-at-least-32-chars")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def seed_pending_registration(
    session: Session,
) -> tuple[OtpChallenge, OtpDispatch]:
    snapshot = seed_registration_snapshot(
        session,
        phone="+998900001341",
    )
    challenge = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=NOW,
    )
    dispatch = create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    return challenge, dispatch


def registration_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=database_url,
        session_cookie_secure=False,
        rate_limit_hmac_key="test-m11-e2e-rate-key-at-least-32-characters",
        otp_hmac_key=OTP_HMAC_KEY,
    )


def activation_context(user_id: UUID) -> AuthenticatedActivationContext:
    return AuthenticatedActivationContext(
        actor=CustomerActivationActor(user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=user_id,
            browser_binding_digest=REGISTRATION_DIGEST,
        ),
        trusted_client_ip=ResolvedClientIp("203.0.113.51"),
        _canonical_account_phone="+998900001341",
    )


def test_dispatcher_prepares_typed_registration_message_after_live_recheck(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        challenge, dispatch = seed_pending_registration(session)
        challenge_id = challenge.id
        dispatch_id = dispatch.id
        user_id = challenge.user_id
        assert user_id is not None
    row_locks: list[str] = []

    def capture_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.split())
        if "FOR UPDATE" in normalized:
            row_locks.append(normalized)

    event.listen(m2_test_database, "before_cursor_execute", capture_lock)
    try:
        with Session(m2_test_database) as session, session.begin():
            prepared = prepare_next_otp_dispatch(
                session,
                otp_hmac_key=OTP_HMAC_KEY,
                now=NOW + timedelta(seconds=1),
                ttl_seconds=300,
                registration_ttl_seconds=180,
                claim_stale_seconds=60,
                code_generator=lambda _upper: 42,
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_lock)

    with Session(m2_test_database) as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        events = tuple(
            session.scalars(
                select(OtpChallengeEvent)
                .where(OtpChallengeEvent.challenge_id == challenge_id)
                .order_by(
                    OtpChallengeEvent.occurred_at.asc(),
                    OtpChallengeEvent.id.asc(),
                )
            )
        )

    assert prepared is not None
    assert prepared.purpose is OtpPurpose.REGISTRATION
    assert prepared.ttl_seconds == 180
    assert prepared.code.as_internal_value() == "000042"
    assert "000042" not in repr(prepared)
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert challenge.expires_at == NOW + timedelta(seconds=181)
    expected_mac = compute_otp_code_mac(
        otp_hmac_key=OTP_HMAC_KEY,
        challenge_id=challenge_id,
        user_id=user_id,
        purpose=OtpPurpose.REGISTRATION,
        code=OtpCode("000042"),
    )
    assert challenge.code_mac == expected_mac.as_stored_value()
    assert not verify_otp_code_mac(
        otp_hmac_key=OTP_HMAC_KEY,
        challenge_id=challenge_id,
        user_id=user_id,
        purpose=OtpPurpose.LOGIN,
        code=OtpCode("000042"),
        stored_mac=challenge.code_mac,
    )
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.PREPARED.value
    assert [(row.action, row.safe_code) for row in events] == [
        (OtpChallengeEventAction.DISPATCH_PREPARED.value, None)
    ]
    tables = (
        "otp_dispatches",
        "otp_challenges",
        "users",
        "telegram_links",
        "customers",
    )
    positions = [
        next(index for index, statement in enumerate(row_locks) if table in statement)
        for table in tables
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    ("changed_state", "expected_action"),
    (
        (
            "link",
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE,
        ),
        (
            "phone_verification",
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE,
        ),
        (
            "customer",
            OtpChallengeEventAction.INVALIDATED_BY_REGISTRATION_STATE_CHANGE,
        ),
    ),
)
def test_registration_dispatcher_state_change_cancels_without_send(
    m2_test_database: Engine,
    changed_state: str,
    expected_action: OtpChallengeEventAction,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        challenge, dispatch = seed_pending_registration(session)
        challenge_id = challenge.id
        dispatch_id = dispatch.id
        if changed_state == "link":
            link = session.get(TelegramLink, challenge.telegram_link_id)
            assert link is not None
            link.telegram_chat_id = None
            link.unlinked_at = NOW + timedelta(seconds=1)
            link.phone_verified_at = None
            link.updated_at = NOW + timedelta(seconds=1)
        elif changed_state == "phone_verification":
            link = session.get(TelegramLink, challenge.telegram_link_id)
            assert link is not None
            link.phone_verified_at = None
            link.updated_at = NOW + timedelta(seconds=1)
        else:
            customer = session.get(Customer, challenge.customer_id)
            assert customer is not None
            customer.onboarding_status = "active"
            customer.activated_at = NOW + timedelta(seconds=1)
            customer.updated_at = NOW + timedelta(seconds=1)

    with Session(m2_test_database) as session, session.begin():
        prepared = prepare_next_otp_dispatch(
            session,
            otp_hmac_key=OTP_HMAC_KEY,
            now=NOW + timedelta(seconds=2),
            ttl_seconds=300,
            registration_ttl_seconds=180,
            claim_stale_seconds=60,
            code_generator=lambda _upper: 999_999,
        )

    with Session(m2_test_database) as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        events = tuple(
            session.scalars(
                select(OtpChallengeEvent).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )

    assert prepared is None
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.code_mac is None
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert [row.action for row in events] == [expected_action.value]


def test_registration_dispatcher_cross_owner_cancels_before_code_or_provider(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    test_database_url: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        challenge, dispatch = seed_pending_registration(session)
        challenge_id = challenge.id
        dispatch_id = dispatch.id
        link = session.get(TelegramLink, challenge.telegram_link_id)
        assert link is not None
        other_user = User(
            phone="+998900001498",
            password_hash=None,
            is_active=True,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(other_user)
        session.flush()
        link.user_id = other_user.id
        link.updated_at = NOW + timedelta(seconds=1)

    code_generator_calls: list[object] = []

    def code_generator_canary(*args: object, **kwargs: object) -> object:
        code_generator_calls.append((args, kwargs))
        raise AssertionError("Cross-owner dispatch reached OTP generation")

    monkeypatch.setattr(
        dispatch_service_module,
        "generate_otp_code",
        code_generator_canary,
    )

    class StopAfterFirstIdle(ShutdownController):
        async def wait_async(self, seconds: float) -> bool:
            _ = seconds
            self.request()
            return True

    class ProviderCanary:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def send_otp(self, **kwargs: object) -> OtpDeliverySendResult:
            self.calls.append(kwargs)
            raise AssertionError("Cross-owner dispatch reached Telegram provider")

    provider = ProviderCanary()
    asyncio.run(
        run_dispatch_loop(
            provider,  # type: ignore[arg-type]
            session_factory=sessionmaker(
                bind=m2_test_database,
                expire_on_commit=False,
            ),
            otp_hmac_key=OTP_HMAC_KEY,
            settings=registration_settings(test_database_url),
            shutdown=StopAfterFirstIdle(),
            now_factory=lambda: NOW + timedelta(seconds=2),
        )
    )

    with Session(m2_test_database) as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        events = tuple(
            session.scalars(
                select(OtpChallengeEvent)
                .where(OtpChallengeEvent.challenge_id == challenge_id)
                .order_by(
                    OtpChallengeEvent.occurred_at.asc(),
                    OtpChallengeEvent.id.asc(),
                )
            )
        )
        assert challenge is not None
        assert dispatch is not None

    assert (
        challenge.status,
        challenge.failed_attempts,
        challenge.code_mac,
        challenge.activated_at,
        challenge.expires_at,
        challenge.consumed_at,
        challenge.terminal_at,
    ) == (
        OtpChallengeStatus.INVALIDATED.value,
        0,
        None,
        None,
        None,
        None,
        NOW + timedelta(seconds=2),
    )
    assert (
        dispatch.status,
        dispatch.claimed_at,
        dispatch.prepared_at,
        dispatch.sent_at,
        dispatch.terminal_at,
        dispatch.failure_code,
        dispatch.updated_at,
    ) == (
        OtpDispatchStatus.CANCELLED.value,
        NOW + timedelta(seconds=2),
        None,
        None,
        NOW + timedelta(seconds=2),
        None,
        NOW + timedelta(seconds=2),
    )
    assert [(row.action, row.safe_code) for row in events] == [
        (
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
            "OTP_LINK_CHANGED",
        )
    ]
    assert code_generator_calls == []
    assert provider.calls == []


@dataclass
class FakeTelegramClient:
    engine: Engine
    calls: list[tuple[int, str, int]] = field(default_factory=list)

    async def send_message(
        self,
        *,
        chat_id: VerifiedPrivateTelegramChatIdentity,
        text: str,
        timeout_seconds: int,
    ) -> None:
        assert self.engine.pool.checkedout() == 0  # type: ignore[attr-defined]
        self.calls.append((chat_id.as_bigint(), text, timeout_seconds))


def test_registration_provider_sends_safe_copy_with_zero_open_session(
    m2_test_database: Engine,
) -> None:
    client = FakeTelegramClient(m2_test_database)
    provider = TelegramOtpProvider(
        bot_api_client=client,  # type: ignore[arg-type]
        send_timeout_seconds=5,
    )

    result = asyncio.run(
        provider.send_otp(
            target=TelegramOtpTarget(
                chat_identity=VerifiedPrivateTelegramChatIdentity(9_980_001_341)
            ),
            code=OtpCode("000042"),
            locale="uz-Latn",
            ttl_seconds=180,
            purpose=OtpPurpose.REGISTRATION,
        )
    )

    assert result.status.value == "SENT"
    assert len(client.calls) == 1
    _chat_id, text, timeout = client.calls[0]
    assert timeout == 5
    assert "Faollashtirish kodi: 000042" in text
    assert "3 daqiqa" in text
    assert "hech kimga bermang" in text
    assert "so'ramagan" in text
    assert "+998" not in text
    assert "JSHSHIR" not in text


def test_dispatcher_external_send_has_no_open_session_and_records_result_once(
    m2_test_database: Engine,
    test_database_url: str,
) -> None:
    settings = registration_settings(test_database_url)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001341",
        )
    with Session(m2_test_database) as session, session.begin():
        user = session.get(User, snapshot.user_id)
        assert user is not None
        rate = RegistrationIssuanceRateLimitPolicy(
            session=session,
            settings=settings,
        ).check_and_record(
            current_user=user,
            client_ip=ResolvedClientIp("203.0.113.51"),
            now=NOW + timedelta(seconds=1),
        )
        assert rate.allowed
    with Session(m2_test_database) as session, session.begin():
        issued = issue_registration_otp(
            session,
            context=activation_context(snapshot.user_id),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=NOW + timedelta(seconds=1),
        )
        assert isinstance(issued, RegistrationOtpPendingDelivery)
    with Session(m2_test_database) as session, session.begin():
        prepared = prepare_next_otp_dispatch(
            session,
            otp_hmac_key=settings.require_otp_hmac_key(),
            now=NOW + timedelta(seconds=2),
            ttl_seconds=settings.otp_login_ttl_seconds,
            registration_ttl_seconds=(
                settings.require_registration_otp_config().ttl_seconds
            ),
            claim_stale_seconds=settings.otp_dispatch_claim_stale_seconds,
            code_generator=lambda _upper: 7,
        )
        assert prepared is not None

    client = FakeTelegramClient(m2_test_database)
    provider = TelegramOtpProvider(
        bot_api_client=client,  # type: ignore[arg-type]
        send_timeout_seconds=5,
    )
    send_result = asyncio.run(
        provider.send_otp(
            target=prepared.target,
            code=prepared.code,
            locale=prepared.locale,
            ttl_seconds=prepared.ttl_seconds,
            purpose=prepared.purpose,
        )
    )
    assert send_result.status is OtpDeliverySendStatus.SENT
    assert m2_test_database.pool.checkedout() == 0  # type: ignore[attr-defined]
    with Session(m2_test_database) as session, session.begin():
        assert record_otp_delivery_result(
            session,
            dispatch_id=prepared.dispatch_id,
            result=send_result,
            now=NOW + timedelta(seconds=3),
        )

    with Session(m2_test_database) as session:
        rates = tuple(session.scalars(select(AuthRateLimit)))
        challenge = session.scalar(select(OtpChallenge))
        dispatch = session.scalar(select(OtpDispatch))
        events = tuple(
            session.scalars(
                select(OtpChallengeEvent).order_by(OtpChallengeEvent.occurred_at)
            )
        )

    assert len(rates) == 3
    assert {row.attempt_count for row in rates} == {1}
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert challenge.user_id == snapshot.user_id
    assert challenge.customer_id == snapshot.customer_id
    assert challenge.registration_offer_acceptance_id == (
        snapshot.registration_offer_acceptance_id
    )
    assert challenge.customer_identity_revision == (
        snapshot.customer_identity_revision.value
    )
    assert challenge.customer_document_id == snapshot.customer_document_id
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.SENT.value
    assert [row.action for row in events] == [
        OtpChallengeEventAction.ISSUED.value,
        OtpChallengeEventAction.DISPATCH_PREPARED.value,
        OtpChallengeEventAction.DISPATCH_RESULT.value,
    ]
    assert [row.safe_code for row in events] == [None, None, "OTP_SENT"]
    assert len(client.calls) == 1
    _chat_id, message, _timeout = client.calls.pop()
    assert "Faollashtirish kodi: 000007" in message
    assert str(snapshot.customer_id) not in message
    assert str(snapshot.registration_offer_acceptance_id) not in message
    assert str(snapshot.customer_document_id) not in message
    rendered = f"{prepared!r} {challenge!r} {issued!r}"
    assert "000007" not in rendered
    assert challenge.code_mac not in rendered
    assert str(snapshot.customer_id) not in rendered


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_failure", "expected_safe_code"),
    (
        (
            OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT),
            OtpDispatchStatus.SENT,
            None,
            "OTP_SENT",
        ),
        (
            OtpDeliverySendResult(
                status=OtpDeliverySendStatus.FAILED,
                failure_code=OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER,
            ),
            OtpDispatchStatus.FAILED,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER.value,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER.value,
        ),
        (
            OtpDeliverySendResult(
                status=OtpDeliverySendStatus.UNKNOWN,
                failure_code="PROVIDER_REQUEST_123456",
            ),
            OtpDispatchStatus.UNKNOWN,
            "OTP_UNKNOWN",
            "OTP_UNKNOWN",
        ),
    ),
)
def test_registration_dispatch_result_is_safe_and_locks_forward(
    m2_test_database: Engine,
    result: OtpDeliverySendResult,
    expected_status: OtpDispatchStatus,
    expected_failure: str | None,
    expected_safe_code: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        challenge, dispatch = seed_pending_registration(session)
        challenge_id = challenge.id
        dispatch_id = dispatch.id
    with Session(m2_test_database) as session, session.begin():
        prepared = prepare_next_otp_dispatch(
            session,
            otp_hmac_key=OTP_HMAC_KEY,
            now=NOW + timedelta(seconds=1),
            ttl_seconds=300,
            registration_ttl_seconds=180,
            claim_stale_seconds=60,
            code_generator=lambda _upper: 123_456,
        )
        assert prepared is not None
    row_locks: list[str] = []

    def capture_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.split())
        if "FOR UPDATE" in normalized:
            row_locks.append(normalized)

    event.listen(m2_test_database, "before_cursor_execute", capture_lock)
    try:
        with Session(m2_test_database) as session, session.begin():
            recorded = record_otp_delivery_result(
                session,
                dispatch_id=dispatch_id,
                result=result,
                now=NOW + timedelta(seconds=2),
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_lock)

    with Session(m2_test_database) as session:
        dispatch = session.get(OtpDispatch, dispatch_id)
        challenge = session.get(OtpChallenge, challenge_id)
        events = tuple(
            session.scalars(
                select(OtpChallengeEvent).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )

    assert recorded
    assert dispatch is not None
    assert dispatch.status == expected_status.value
    assert dispatch.failure_code == expected_failure
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert [(row.action, row.safe_code) for row in events][-1] == (
        OtpChallengeEventAction.DISPATCH_RESULT.value,
        expected_safe_code,
    )
    assert "PROVIDER_REQUEST_123456" not in " ".join(
        str(value)
        for value in (dispatch.failure_code, *(row.safe_code for row in events))
    )
    dispatch_lock = next(
        index
        for index, statement in enumerate(row_locks)
        if "otp_dispatches" in statement
    )
    challenge_lock = next(
        index
        for index, statement in enumerate(row_locks)
        if "otp_challenges" in statement
    )
    assert dispatch_lock < challenge_lock


def test_post_send_link_change_does_not_activate_or_revalidate_result(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        challenge, dispatch = seed_pending_registration(session)
        challenge_id = challenge.id
        dispatch_id = dispatch.id
        customer_id = challenge.customer_id
        link_id = challenge.telegram_link_id
    with Session(m2_test_database) as session, session.begin():
        prepared = prepare_next_otp_dispatch(
            session,
            otp_hmac_key=OTP_HMAC_KEY,
            now=NOW + timedelta(seconds=1),
            ttl_seconds=300,
            registration_ttl_seconds=180,
            claim_stale_seconds=60,
            code_generator=lambda _upper: 123_456,
        )
        assert prepared is not None
    with Session(m2_test_database) as session, session.begin():
        link = session.get(TelegramLink, link_id)
        assert link is not None
        link.telegram_chat_id = None
        link.unlinked_at = NOW + timedelta(seconds=2)
        link.phone_verified_at = None
        link.updated_at = NOW + timedelta(seconds=2)
    with Session(m2_test_database) as session, session.begin():
        assert record_otp_delivery_result(
            session,
            dispatch_id=dispatch_id,
            result=OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT),
            now=NOW + timedelta(seconds=3),
        )

    with Session(m2_test_database) as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        customer = session.get(Customer, customer_id)

    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.SENT.value
    assert customer is not None
    assert customer.onboarding_status == "draft"
    assert customer.activated_at is None


def test_registration_stale_prepared_becomes_unknown_once_without_resend(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        challenge, dispatch = seed_pending_registration(session)
        challenge_id = challenge.id
        dispatch_id = dispatch.id
    with Session(m2_test_database) as session, session.begin():
        prepared = prepare_next_otp_dispatch(
            session,
            otp_hmac_key=OTP_HMAC_KEY,
            now=NOW + timedelta(seconds=1),
            ttl_seconds=300,
            registration_ttl_seconds=180,
            claim_stale_seconds=60,
            code_generator=lambda _upper: 654_321,
        )
        assert prepared is not None
    with Session(m2_test_database) as session, session.begin():
        recovered = recover_stale_prepared_dispatches(
            session,
            now=NOW + timedelta(seconds=62),
            stale_seconds=60,
            limit=20,
        )
    with Session(m2_test_database) as session, session.begin():
        recovered_again = recover_stale_prepared_dispatches(
            session,
            now=NOW + timedelta(seconds=63),
            stale_seconds=60,
            limit=20,
        )

    with Session(m2_test_database) as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        event_codes = tuple(
            session.scalars(
                select(OtpChallengeEvent.safe_code).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            )
        )

    assert recovered == 1
    assert recovered_again == 0
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.UNKNOWN.value
    assert dispatch.failure_code == "OTP_DISPATCH_STALE_PREPARED"
    assert event_codes.count("OTP_DISPATCH_STALE_PREPARED") == 1


def test_dispatcher_failure_unknown_and_stopped_modes_preserve_existing_process(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    web = compose.split("  web:", 1)[1].split("  telegram-worker:", 1)[0]
    worker = compose.split("  telegram-worker:", 1)[1].split(
        "  otp-dispatcher:",
        1,
    )[0]
    dispatcher = compose.split("  otp-dispatcher:", 1)[1].split(
        "\nvolumes:",
        1,
    )[0]
    registration_env = (
        "OTP_REGISTRATION_TTL_SECONDS",
        "OTP_REGISTRATION_MAX_VERIFY_ATTEMPTS",
        "OTP_REGISTRATION_RESEND_COOLDOWN_SECONDS",
        "OTP_REGISTRATION_RATE_LIMIT_WINDOW_SECONDS",
        "OTP_REGISTRATION_RATE_LIMIT_PHONE_ATTEMPTS",
        "OTP_REGISTRATION_RATE_LIMIT_USER_ATTEMPTS",
        "OTP_REGISTRATION_RATE_LIMIT_IP_ATTEMPTS",
    )

    assert compose.count("  otp-dispatcher:\n") == 1
    assert compose.count('["python", "-m", "app.otp.dispatcher", "run"]') == 1
    assert '["python", "-m", "app.telegram.worker", "run"]' in worker
    assert "TELEGRAM_BOT_TOKEN" not in web
    assert "TELEGRAM_BOT_TOKEN" in dispatcher
    assert all(name in web for name in registration_env)
    assert registration_env[0] in dispatcher
    assert all(name not in worker for name in registration_env)
    assert all(f"{name}=" in env_example for name in registration_env)
    assert build_parser().parse_args(["run"]).command == "run"
    assert build_parser().parse_args(["healthcheck"]).command == "healthcheck"

    monkeypatch.setattr(
        "app.telegram.bot_api.create_telegram_http_client",
        lambda *_args, **_kwargs: pytest.fail("web must not create Telegram transport"),
    )
    application = create_app(
        settings=Settings(
            _env_file=None,
            app_environment="testing",
            debug=False,
            database_url=test_database_url,
            session_cookie_secure=False,
            rate_limit_hmac_key=("test-m11-process-rate-key-at-least-32-characters"),
        )
    )
    with TestClient(application) as client:
        health = client.get("/health")
        password_login = client.get("/auth/login")
    application.state.database_engine.dispose()

    pending_copy = get_customer_activation_copy(
        OtpWebLanguage.UZ_LATN
    ).delivery_pending_notice
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert password_login.status_code == 200
    assert "Telegram orqali yuborish uchun qabul qilindi" in pending_copy
    assert "token" not in pending_copy.casefold()
    assert "dispatcher" not in pending_copy.casefold()
