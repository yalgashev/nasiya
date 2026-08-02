from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer_activation.service as activation_service_module
import app.telegram.service as telegram_service_module
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.customer.models import Customer
from app.customer_activation.contracts import (
    TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    CustomerLifecycleStatus,
    OrdinaryTelegramUnlinkOutcome,
    PreparedCustomerActivation,
    ProtectedActiveTelegramRelinkResult,
    ProtectedTelegramRelinkLinkDisposition,
    ProtectedTelegramRelinkOutcome,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    VerifyRegistrationOtp,
    decide_ordinary_telegram_unlink,
)
from app.customer_activation.service import verify_and_activate_registration_customer
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpPurpose,
)
from app.otp.crypto import OtpBrowserBindingDigest, compute_otp_code_mac
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import (
    activate_challenge,
    create_pending_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
)
from app.settings import Settings
from app.shop.models import Shop
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramChatAlreadyLinkedError,
    TelegramLinkOutcome,
    TelegramLinkTokenIssueError,
    consume_start_token,
    issue_relink_token,
    unlink,
)
from tests.m11_seed import (
    NOW as SEED_NOW,
)
from tests.m11_seed import (
    REGISTRATION_DIGEST,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)

_ = Shop
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "customer_status",
    [None, CustomerLifecycleStatus.DRAFT],
)
def test_no_customer_and_draft_keep_inherited_unlink_contract(
    customer_status: CustomerLifecycleStatus | None,
) -> None:
    result = decide_ordinary_telegram_unlink(customer_status)

    assert result.outcome is OrdinaryTelegramUnlinkOutcome.INHERITED_UNLINK_ALLOWED
    assert result.mutation_allowed is True
    assert result.error_code is None


def test_active_customer_ordinary_unlink_is_exact_zero_mutation_denial() -> None:
    result = decide_ordinary_telegram_unlink(CustomerLifecycleStatus.ACTIVE)

    assert result.outcome is OrdinaryTelegramUnlinkOutcome.ACTIVE_CUSTOMER_DENIED
    assert result.mutation_allowed is False
    assert result.error_code == TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER
    assert not hasattr(result, "event")
    assert not hasattr(result, "invalidated_otp_purposes")
    assert not hasattr(result, "customer_transition")


def test_successful_protected_active_relink_invalidates_both_otp_purposes() -> None:
    result = ProtectedActiveTelegramRelinkResult(
        outcome=ProtectedTelegramRelinkOutcome.RELINKED
    )

    assert result.customer_status is CustomerLifecycleStatus.ACTIVE
    assert (
        result.link_disposition
        is ProtectedTelegramRelinkLinkDisposition.GENERATION_REPLACED
    )
    assert result.invalidated_otp_purposes == (
        OtpPurpose.LOGIN,
        OtpPurpose.REGISTRATION,
    )


def test_protected_active_relink_collision_preserves_old_link_and_all_state() -> None:
    result = ProtectedActiveTelegramRelinkResult(
        outcome=ProtectedTelegramRelinkOutcome.CHAT_COLLISION
    )

    assert result.customer_status is CustomerLifecycleStatus.ACTIVE
    assert (
        result.link_disposition
        is ProtectedTelegramRelinkLinkDisposition.CURRENT_LINK_PRESERVED
    )
    assert result.invalidated_otp_purposes == ()
    assert not hasattr(result, "customer_transition")


def test_telegram_contracts_reject_untyped_state_and_outcome() -> None:
    with pytest.raises(TypeError, match="lifecycle status"):
        decide_ordinary_telegram_unlink("active")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="relink outcome"):
        ProtectedActiveTelegramRelinkResult(outcome="RELINKED")  # type: ignore[arg-type]


def test_supported_link_mutation_entries_use_service_guards() -> None:
    app_sources = {
        path: path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "app").rglob("*.py")
    }
    auth_router_source = app_sources[PROJECT_ROOT / "app" / "auth" / "router.py"]
    update_source = app_sources[
        PROJECT_ROOT / "app" / "telegram" / "update_processing.py"
    ]

    assert auth_router_source.count('@router.post("/telegram/unlink"') == 1
    assert auth_router_source.count('@router.post("/telegram/relink-token"') == 1
    assert "unlink_telegram(db, user, now)" in auth_router_source
    assert "consume_start_token(" in update_source
    assert (
        sum(
            source.count("unlink_verified_private_chat")
            for source in app_sources.values()
        )
        == 3
    )
    assert (
        sum(
            source.count("relink_verified_private_chat")
            for source in app_sources.values()
        )
        == 3
    )


_NOW = datetime(2026, 8, 2, 12, 30, tzinfo=UTC)
_OTP_HMAC_KEY = SecretStr("m11-synthetic-registration-otp-hmac-key")
_RATE_HMAC_KEY = "m11-synthetic-rate-limit-key-at-least-32-characters"
_LOGIN_DIGEST = OtpBrowserBindingDigest("b" * 64)


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=_RATE_HMAC_KEY,
        otp_hmac_key=_OTP_HMAC_KEY,
        telegram_link_rate_limit_user_attempts=10,
        telegram_link_rate_limit_phone_attempts=10,
        telegram_link_rate_limit_ip_attempts=10,
    )


def _activate_otp(
    session: Session,
    *,
    challenge: OtpChallenge,
    purpose: OtpPurpose,
    code: str,
) -> OtpDispatch:
    dispatch = create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=SEED_NOW,
    )
    activate_challenge(
        session,
        challenge=challenge,
        code_mac=compute_otp_code_mac(
            otp_hmac_key=_OTP_HMAC_KEY,
            challenge_id=challenge.id,
            user_id=challenge.user_id,
            purpose=purpose,
            code=OtpCode(code),
        ),
        activated_at=SEED_NOW + timedelta(seconds=1),
        expires_at=_NOW + timedelta(minutes=3),
    )
    return dispatch


def _seed_outstanding_challenges(
    session: Session,
    *,
    snapshot,
) -> tuple[OtpChallenge, OtpChallenge]:
    registration = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=SEED_NOW,
    )
    _activate_otp(
        session,
        challenge=registration,
        purpose=OtpPurpose.REGISTRATION,
        code="004271",
    )
    login = create_pending_challenge(
        session,
        browser_binding_digest=_LOGIN_DIGEST,
        now=SEED_NOW,
        purpose=OtpPurpose.LOGIN,
        user_id=snapshot.user_id,
        telegram_link_id=snapshot.telegram_link_id,
        telegram_linked_at=snapshot.telegram_linked_at,
    )
    _activate_otp(
        session,
        challenge=login,
        purpose=OtpPurpose.LOGIN,
        code="006315",
    )
    return registration, login


def _mark_customer_active(session: Session, *, customer_id: UUID) -> None:
    customer = session.get(Customer, customer_id)
    assert customer is not None
    customer.onboarding_status = CustomerLifecycleStatus.ACTIVE.value
    customer.activated_at = _NOW
    customer.updated_at = _NOW
    session.flush()


def _verify_command(
    *, user_id: UUID, current_session_id: UUID
) -> VerifyRegistrationOtp:
    return VerifyRegistrationOtp(
        actor=CustomerActivationActor(user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=current_session_id,
            browser_binding_digest=REGISTRATION_DIGEST,
        ),
        candidate_code="004271",
        now=_NOW,
    )


@pytest.mark.integration
@pytest.mark.parametrize("customer_state", ("missing", "draft"))
def test_no_customer_and_draft_unlink_keep_inherited_database_behavior(
    m2_test_database: Engine,
    customer_state: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        if customer_state == "draft":
            snapshot = seed_registration_snapshot(
                session,
                phone="+998900001419",
            )
            user = session.get(User, snapshot.user_id)
            assert user is not None
            customer_id = snapshot.customer_id
            link_id = snapshot.telegram_link_id
        else:
            user = User(
                phone="+998900001418",
                is_active=True,
                created_at=SEED_NOW,
                updated_at=SEED_NOW,
            )
            session.add(user)
            session.flush()
            link = TelegramLink(
                user_id=user.id,
                telegram_chat_id=9_980_001_418,
                linked_at=SEED_NOW,
                updated_at=SEED_NOW,
            )
            session.add(link)
            session.flush()
            customer_id = None
            link_id = link.id

        result = unlink(session, user, _NOW)
        assert result.outcome is TelegramLinkOutcome.UNLINKED

    with Session(m2_test_database) as session:
        stored_link = session.get(TelegramLink, link_id)
        customer = (
            session.get(Customer, customer_id) if customer_id is not None else None
        )
        link_event_count = session.scalar(
            select(func.count()).select_from(TelegramLinkEvent)
        )
        challenge_count = session.scalar(select(func.count()).select_from(OtpChallenge))

    assert stored_link is not None
    assert stored_link.telegram_chat_id is None
    assert stored_link.unlinked_at == _NOW
    assert link_event_count == 1
    assert challenge_count == 0
    if customer_state == "draft":
        assert customer is not None
        assert customer.onboarding_status == CustomerLifecycleStatus.DRAFT.value
        assert customer.activated_at is None
    else:
        assert customer is None


@pytest.mark.integration
def test_active_customer_ordinary_unlink_is_zero_write_denied(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001380",
        )
        registration, login = _seed_outstanding_challenges(
            session,
            snapshot=snapshot,
        )
        _mark_customer_active(session, customer_id=snapshot.customer_id)
        user = session.get(User, snapshot.user_id)
        assert user is not None
        issued = issue_relink_token(
            session,
            _settings(m2_test_database),
            user,
            ResolvedClientIp("203.0.113.180"),
            _NOW,
            token_generator=lambda _size: "m11-active-unlink-guard-token",
        )
        session.flush()
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        assert link is not None
        before = (
            link.telegram_chat_id,
            link.linked_at,
            link.unlinked_at,
            registration.status,
            login.status,
            issued.token.consumed_at,
            issued.token.invalidated_at,
            session.scalar(select(func.count()).select_from(TelegramLinkEvent)),
            session.scalar(select(func.count()).select_from(OtpChallengeEvent)),
        )

        with pytest.raises(TelegramLinkTokenIssueError) as caught:
            unlink(session, user, _NOW + timedelta(seconds=1))
        session.flush()
        session.refresh(link)
        session.refresh(registration)
        session.refresh(login)
        session.refresh(issued.token)
        after = (
            link.telegram_chat_id,
            link.linked_at,
            link.unlinked_at,
            registration.status,
            login.status,
            issued.token.consumed_at,
            issued.token.invalidated_at,
            session.scalar(select(func.count()).select_from(TelegramLinkEvent)),
            session.scalar(select(func.count()).select_from(OtpChallengeEvent)),
        )

    assert caught.value.error_code is ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER
    assert before == after


@pytest.mark.integration
def test_active_customer_protected_relink_is_atomic_and_invalidates_both_purposes(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001381",
        )
        registration, login = _seed_outstanding_challenges(
            session,
            snapshot=snapshot,
        )
        _mark_customer_active(session, customer_id=snapshot.customer_id)
        user = session.get(User, snapshot.user_id)
        assert user is not None
        issued = issue_relink_token(
            session,
            _settings(m2_test_database),
            user,
            ResolvedClientIp("203.0.113.181"),
            _NOW,
            token_generator=lambda _size: "m11-protected-relink-token",
        )
        session.flush()
        registration_id = registration.id
        login_id = login.id
        token_id = issued.token.id
        old_linked_at = snapshot.telegram_linked_at
        raw_token = issued.raw_token

    relinked_at = _NOW + timedelta(seconds=1)
    with Session(m2_test_database) as session, session.begin():
        result = consume_start_token(
            session,
            raw_token,
            VerifiedPrivateTelegramChatIdentity(9_980_001_381),
            relinked_at,
        )
        assert result.outcome is TelegramLinkOutcome.RELINKED

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        registration = session.get(OtpChallenge, registration_id)
        login = session.get(OtpChallenge, login_id)
        token = session.get(TelegramLinkToken, token_id)
        challenge_events = tuple(
            session.scalars(
                select(OtpChallengeEvent)
                .where(OtpChallengeEvent.challenge_id.in_((registration_id, login_id)))
                .order_by(OtpChallengeEvent.challenge_id)
            )
        )
        link_events = tuple(session.scalars(select(TelegramLinkEvent)))
        assert customer is not None
        assert link is not None
        assert registration is not None
        assert login is not None
        assert token is not None

    assert (customer.onboarding_status, customer.activated_at) == ("active", _NOW)
    assert link.unlinked_at is None
    assert link.linked_at == relinked_at
    assert link.linked_at != old_linked_at
    assert registration.status == OtpChallengeStatus.INVALIDATED.value
    assert login.status == OtpChallengeStatus.INVALIDATED.value
    assert token.consumed_at == relinked_at
    assert token.invalidated_at is None
    assert tuple(event.action for event in challenge_events) == (
        OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
        OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
    )
    assert tuple(event.action for event in link_events) == ("relinked",)


@pytest.mark.integration
def test_protected_active_relink_collision_preserves_old_generation_and_otp(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001382",
        )
        registration, login = _seed_outstanding_challenges(
            session,
            snapshot=snapshot,
        )
        _mark_customer_active(session, customer_id=snapshot.customer_id)
        user = session.get(User, snapshot.user_id)
        assert user is not None
        issued = issue_relink_token(
            session,
            _settings(m2_test_database),
            user,
            ResolvedClientIp("203.0.113.182"),
            _NOW,
            token_generator=lambda _size: "m11-relink-collision-token",
        )
        collision_owner = User(
            phone="+998900001482",
            is_active=True,
            created_at=SEED_NOW,
            updated_at=SEED_NOW,
        )
        session.add(collision_owner)
        session.flush()
        collision_chat_id = 9_980_001_482
        session.add(
            TelegramLink(
                user_id=collision_owner.id,
                telegram_chat_id=collision_chat_id,
                linked_at=SEED_NOW,
                updated_at=SEED_NOW,
            )
        )
        session.flush()
        registration_id = registration.id
        login_id = login.id
        token_id = issued.token.id
        raw_token = issued.raw_token
        old_link_state = (
            snapshot.telegram_link_id,
            snapshot.telegram_linked_at,
        )

    with Session(m2_test_database) as session, session.begin():
        with pytest.raises(TelegramChatAlreadyLinkedError):
            consume_start_token(
                session,
                raw_token,
                VerifiedPrivateTelegramChatIdentity(collision_chat_id),
                _NOW + timedelta(seconds=1),
            )

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        registration = session.get(OtpChallenge, registration_id)
        login = session.get(OtpChallenge, login_id)
        token = session.get(TelegramLinkToken, token_id)
        event_count = session.scalar(
            select(func.count()).select_from(TelegramLinkEvent)
        )
        challenge_event_count = session.scalar(
            select(func.count()).select_from(OtpChallengeEvent)
        )
        assert customer is not None
        assert link is not None
        assert registration is not None
        assert login is not None
        assert token is not None

    assert (link.id, link.linked_at) == old_link_state
    assert link.unlinked_at is None
    assert registration.status == OtpChallengeStatus.ACTIVE.value
    assert login.status == OtpChallengeStatus.ACTIVE.value
    assert token.consumed_at is None
    assert token.invalidated_at is None
    assert customer.onboarding_status == CustomerLifecycleStatus.ACTIVE.value
    assert event_count == 0
    assert challenge_event_count == 0


@pytest.mark.integration
def test_activation_first_then_unlink_is_denied_and_keeps_active_link(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001383",
        )
        registration = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=SEED_NOW,
        )
        _activate_otp(
            session,
            challenge=registration,
            purpose=OtpPurpose.REGISTRATION,
            code="004271",
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        current_session_id = current.session.id

    activation_locked = Event()
    unlink_attempted = Event()
    original_append = activation_service_module.append_audit_event

    def hold_activation(*args: object, **kwargs: object) -> None:
        activation_locked.set()
        assert unlink_attempted.wait(timeout=5)
        original_append(*args, **kwargs)

    monkeypatch.setattr(
        activation_service_module, "append_audit_event", hold_activation
    )

    def activate() -> str:
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, PreparedCustomerActivation)
            return RegistrationOtpVerificationOutcome.ACTIVATED.value

    def attempt_unlink() -> ErrorCode:
        assert activation_locked.wait(timeout=5)
        unlink_attempted.set()
        with Session(m2_test_database) as session, session.begin():
            user = session.get(User, snapshot.user_id)
            assert user is not None
            with pytest.raises(TelegramLinkTokenIssueError) as caught:
                unlink(session, user, _NOW + timedelta(seconds=1))
            return caught.value.error_code

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        activation_future = executor.submit(activate)
        unlink_future = executor.submit(attempt_unlink)
        completed, pending = wait((activation_future, unlink_future), timeout=10)
        assert not pending
        assert len(completed) == 2
        assert activation_future.result() == "ACTIVATED"
        assert unlink_future.result() is ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        assert customer is not None
        assert link is not None

    assert customer.onboarding_status == CustomerLifecycleStatus.ACTIVE.value
    assert customer.activated_at == _NOW
    assert link.telegram_chat_id is not None
    assert link.unlinked_at is None


@pytest.mark.integration
def test_unlink_first_then_verify_never_activates_unlinked_customer(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001384",
        )
        registration = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=SEED_NOW,
        )
        _activate_otp(
            session,
            challenge=registration,
            purpose=OtpPurpose.REGISTRATION,
            code="004271",
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        registration_id = registration.id
        current_session_id = current.session.id

    unlink_mutated = Event()
    verify_attempted = Event()
    original_append = telegram_service_module.append_telegram_link_event

    def hold_unlink(*args: object, **kwargs: object):
        result = original_append(*args, **kwargs)
        unlink_mutated.set()
        assert verify_attempted.wait(timeout=5)
        return result

    monkeypatch.setattr(
        telegram_service_module,
        "append_telegram_link_event",
        hold_unlink,
    )

    def perform_unlink() -> str:
        with Session(m2_test_database) as session, session.begin():
            user = session.get(User, snapshot.user_id)
            assert user is not None
            result = unlink(session, user, _NOW)
            return result.outcome.value

    def verify() -> RegistrationOtpVerificationOutcome:
        assert unlink_mutated.wait(timeout=5)
        verify_attempted.set()
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            return result.outcome

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        unlink_future = executor.submit(perform_unlink)
        verify_future = executor.submit(verify)
        completed, pending = wait((unlink_future, verify_future), timeout=10)
        assert not pending
        assert len(completed) == 2
        assert unlink_future.result() == TelegramLinkOutcome.UNLINKED.value
        assert verify_future.result() is RegistrationOtpVerificationOutcome.OTP_INVALID
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        registration = session.get(OtpChallenge, registration_id)
        current_session = session.get(AuthSession, current_session_id)
        assert customer is not None
        assert link is not None
        assert registration is not None
        assert current_session is not None

    assert (customer.onboarding_status, customer.activated_at) == ("draft", None)
    assert link.telegram_chat_id is None
    assert link.unlinked_at == _NOW
    assert registration.status == OtpChallengeStatus.INVALIDATED.value
    assert current_session.revoked_at is None


@pytest.mark.integration
def test_relink_first_invalidates_old_registration_challenge_before_verify(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001385",
        )
        registration = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=SEED_NOW,
        )
        _activate_otp(
            session,
            challenge=registration,
            purpose=OtpPurpose.REGISTRATION,
            code="004271",
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        user = session.get(User, snapshot.user_id)
        assert user is not None
        issued = issue_relink_token(
            session,
            _settings(m2_test_database),
            user,
            ResolvedClientIp("203.0.113.185"),
            _NOW,
            token_generator=lambda _size: "m11-relink-verify-race-token",
        )
        session.flush()
        registration_id = registration.id
        current_session_id = current.session.id
        raw_token = issued.raw_token

    relink_mutated = Event()
    verify_attempted = Event()
    original_append = telegram_service_module.append_telegram_link_event

    def hold_relink(*args: object, **kwargs: object):
        result = original_append(*args, **kwargs)
        relink_mutated.set()
        assert verify_attempted.wait(timeout=5)
        return result

    monkeypatch.setattr(
        telegram_service_module,
        "append_telegram_link_event",
        hold_relink,
    )

    def relink() -> str:
        with Session(m2_test_database) as session, session.begin():
            result = consume_start_token(
                session,
                raw_token,
                VerifiedPrivateTelegramChatIdentity(9_980_001_385),
                _NOW + timedelta(seconds=1),
            )
            return result.outcome.value

    def verify() -> RegistrationOtpVerificationOutcome:
        assert relink_mutated.wait(timeout=5)
        verify_attempted.set()
        with Session(m2_test_database) as session, session.begin():
            result = verify_and_activate_registration_customer(
                session,
                command=_verify_command(
                    user_id=snapshot.user_id,
                    current_session_id=current_session_id,
                ),
                settings=_settings(m2_test_database),
                identity_crypto_config=synthetic_identity_crypto_config(),
            )
            assert isinstance(result, RegistrationOtpVerificationResult)
            return result.outcome

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        relink_future = executor.submit(relink)
        verify_future = executor.submit(verify)
        completed, pending = wait((relink_future, verify_future), timeout=10)
        assert not pending
        assert len(completed) == 2
        assert relink_future.result() == TelegramLinkOutcome.RELINKED.value
        assert verify_future.result() is RegistrationOtpVerificationOutcome.OTP_INVALID
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        registration = session.get(OtpChallenge, registration_id)
        current_session = session.get(AuthSession, current_session_id)
        dispatch_statuses = tuple(
            session.scalars(
                select(OtpDispatch.status).where(
                    OtpDispatch.challenge_id == registration_id
                )
            )
        )
        assert customer is not None
        assert link is not None
        assert registration is not None
        assert current_session is not None

    assert (customer.onboarding_status, customer.activated_at) == ("draft", None)
    assert link.telegram_chat_id is not None
    assert link.unlinked_at is None
    assert link.linked_at == _NOW + timedelta(seconds=1)
    assert registration.status == OtpChallengeStatus.INVALIDATED.value
    assert dispatch_statuses == (OtpDispatchStatus.CANCELLED.value,)
    assert current_session.revoked_at is None


@pytest.mark.parametrize(
    "scenario",
    ("activation-first", "unlink-first", "relink-first"),
)
def test_activation_unlink_and_relink_barriers_never_leave_active_unlinked(
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with monkeypatch.context() as scoped:
        if scenario == "activation-first":
            test_activation_first_then_unlink_is_denied_and_keeps_active_link(
                scoped,
                m2_test_database,
            )
        elif scenario == "unlink-first":
            test_unlink_first_then_verify_never_activates_unlinked_customer(
                scoped,
                m2_test_database,
            )
        else:
            test_relink_first_invalidates_old_registration_challenge_before_verify(
                scoped,
                m2_test_database,
            )
