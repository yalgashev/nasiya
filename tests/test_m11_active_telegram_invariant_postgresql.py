import ast
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
from app.audit.models import AuditLog
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
    RegistrationOtpPendingDelivery,
    RegistrationOtpPrerequisiteFailed,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    RegistrationPrerequisiteError,
    RegistrationReadinessComponent,
    RegistrationReadinessComponentStatus,
    RegistrationReadinessSnapshot,
    RegistrationReadinessState,
    VerifyRegistrationOtp,
    decide_ordinary_telegram_unlink,
)
from app.customer_activation.service import (
    get_registration_readiness,
    issue_registration_otp,
    verify_and_activate_registration_customer,
)
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
    OtpPublicOutcome,
    OtpPurpose,
    map_internal_outcome_to_public,
)
from app.otp.crypto import OtpBrowserBindingDigest, compute_otp_code_mac
from app.otp.issuance import issue_login_otp_in_transaction
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import (
    activate_challenge,
    create_pending_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
)
from app.otp.web_presentation import OtpWebLanguage
from app.settings import Settings
from app.shop.models import Shop
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramChatAlreadyLinkedError,
    TelegramLinkOutcome,
    TelegramLinkTokenIssueError,
    bind_start_token_for_contact,
    consume_start_token,
    unlink,
)
from tests.m11_seed import (
    NOW as SEED_NOW,
)
from tests.m11_seed import (
    REGISTRATION_DIGEST,
    synthetic_identity_crypto_config,
)
from tests.m11_seed import (
    seed_registration_snapshot as _seed_registration_snapshot,
)
from tests.telegram_issue_helpers import (
    issue_relink_token_in_one_test_transaction as issue_relink_token,
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
    telegram_repository_source = app_sources[
        PROJECT_ROOT / "app" / "telegram" / "repository.py"
    ]
    telegram_service_source = app_sources[
        PROJECT_ROOT / "app" / "telegram" / "service.py"
    ]
    assert (
        telegram_repository_source.count("def unlink_verified_private_chat(")
        == telegram_repository_source.count(
            "def unlink_verified_private_chat_from_prelocked_state("
        )
        == 1
    )
    assert (
        telegram_repository_source.count("def link_unverified_private_chat(")
        == telegram_repository_source.count(
            "def link_unverified_private_chat_from_prelocked_state("
        )
        == 1
    )
    assert (
        telegram_repository_source.count("def relink_unverified_private_chat(")
        == telegram_repository_source.count(
            "def relink_unverified_private_chat_from_prelocked_state("
        )
        == 1
    )
    assert "def link_verified_private_chat(" not in telegram_repository_source
    assert "def link_verified_private_chat_from_prelocked_state(" not in (
        telegram_repository_source
    )
    assert "def relink_verified_private_chat(" not in telegram_repository_source
    assert "def relink_verified_private_chat_from_prelocked_state(" not in (
        telegram_repository_source
    )
    assert "unlink_verified_private_chat(" not in telegram_service_source
    assert "link_unverified_private_chat(" not in telegram_service_source
    assert "relink_unverified_private_chat(" not in telegram_service_source
    assert (
        telegram_service_source.count(
            "unlink_verified_private_chat_from_prelocked_state"
        )
        == 2
    )
    assert (
        telegram_service_source.count(
            "relink_phone_verified_private_chat_from_prelocked_state"
        )
        == 2
    )


def test_every_otp_link_policy_call_supplies_server_derived_owner() -> None:
    policy_calls: list[ast.Call] = []
    for relative_path in (
        "otp/issuance.py",
        "otp/verification.py",
        "otp/dispatch_service.py",
        "customer_activation/service.py",
    ):
        source = (PROJECT_ROOT / "app" / relative_path).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "is_otp_eligible_telegram_link"
            ):
                policy_calls.append(node)

    assert len(policy_calls) == 7
    for call in policy_calls:
        owner_keywords = [
            keyword for keyword in call.keywords if keyword.arg == "expected_user_id"
        ]
        assert len(owner_keywords) == 1
        assert isinstance(owner_keywords[0].value, ast.Attribute)
        assert owner_keywords[0].value.attr == "id"


_NOW = datetime(2026, 8, 2, 12, 30, tzinfo=UTC)
_OTP_HMAC_KEY = SecretStr("m11-synthetic-registration-otp-hmac-key")
_RATE_HMAC_KEY = "m11-synthetic-rate-limit-key-at-least-32-characters"
_LOGIN_DIGEST = OtpBrowserBindingDigest("b" * 64)


def seed_registration_snapshot(
    session: Session,
    *,
    phone: str,
) -> RegistrationReadinessSnapshot:
    snapshot = _seed_registration_snapshot(session, phone=phone)
    link = session.get(TelegramLink, snapshot.telegram_link_id)
    assert link is not None
    link.phone_verified_at = link.linked_at
    session.flush()
    return snapshot


def _activation_context(
    *,
    snapshot: RegistrationReadinessSnapshot,
    phone: str,
    current_session_id: UUID | None = None,
) -> activation_service_module.AuthenticatedActivationContext:
    return activation_service_module.AuthenticatedActivationContext(
        actor=CustomerActivationActor(snapshot.user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=current_session_id or snapshot.user_id,
            browser_binding_digest=REGISTRATION_DIGEST,
        ),
        trusted_client_ip=ResolvedClientIp("203.0.113.190"),
        _canonical_account_phone=phone,
    )


def _consume_matching_contact(
    session: Session,
    *,
    raw_token,
    chat_id: int,
    phone: str,
    now: datetime,
):
    chat_identity = VerifiedPrivateTelegramChatIdentity(chat_id)
    sender_identity = TelegramUserIdentity(chat_id + 1_000_000_000)
    binding_key = SecretStr(_RATE_HMAC_KEY)
    bind_start_token_for_contact(
        session,
        raw_token,
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=binding_key,
        now=now,
    )
    return consume_start_token(
        session,
        chat_identity,
        sender_identity,
        sender_identity,
        SensitiveTelegramContactPhone(phone),
        rate_limit_hmac_key=binding_key,
        now=now,
    )


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
def test_legacy_unverified_link_cannot_issue_login_otp(
    m2_test_database: Engine,
) -> None:
    phone = "+998900001390"
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(session, phone=phone)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        assert link is not None
        link.phone_verified_at = None
        link_id = link.id

    with Session(m2_test_database) as session, session.begin():
        result = issue_login_otp_in_transaction(
            session,
            _settings(m2_test_database),
            phone_input=phone,
            browser_binding_digest=_LOGIN_DIGEST,
            locale="uz-Latn",
            now=_NOW,
        )

    with Session(m2_test_database) as session:
        link = session.get(TelegramLink, link_id)
        otp_counts = tuple(
            session.scalar(select(func.count()).select_from(model)) or 0
            for model in (OtpChallenge, OtpDispatch, OtpChallengeEvent)
        )

    assert result.outcome is OtpInternalOutcome.TELEGRAM_PHONE_NOT_VERIFIED
    assert map_internal_outcome_to_public(result.outcome) is (
        OtpPublicOutcome.GENERIC_ACCEPTED
    )
    assert otp_counts == (0, 0, 0)
    assert link is not None
    assert link.telegram_chat_id is not None
    assert link.unlinked_at is None
    assert link.phone_verified_at is None


@pytest.mark.integration
def test_legacy_unverified_link_cannot_issue_registration_otp_or_activate(
    m2_test_database: Engine,
) -> None:
    phone = "+998900001391"
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(session, phone=phone)
        challenge = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=SEED_NOW,
        )
        dispatch = _activate_otp(
            session,
            challenge=challenge,
            purpose=OtpPurpose.REGISTRATION,
            code="004271",
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-legacy-verification-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        assert link is not None
        link.phone_verified_at = None
        challenge_id = challenge.id
        dispatch_id = dispatch.id
        current_session_id = current.session.id
        link_id = link.id

    context = _activation_context(
        snapshot=snapshot,
        phone=phone,
        current_session_id=current_session_id,
    )
    with Session(m2_test_database) as session, session.begin():
        issue_result = issue_registration_otp(
            session,
            context=context,
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=_NOW,
        )

    with Session(m2_test_database) as session, session.begin():
        verify_result = verify_and_activate_registration_customer(
            session,
            command=_verify_command(
                user_id=snapshot.user_id,
                current_session_id=current_session_id,
            ),
            settings=_settings(m2_test_database),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        link = session.get(TelegramLink, link_id)
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        current_session = session.get(AuthSession, current_session_id)
        capability_counts = tuple(
            session.scalar(select(func.count()).select_from(model)) or 0
            for model in (
                OtpChallenge,
                OtpDispatch,
                OtpChallengeEvent,
                AuditLog,
                AuthSession,
            )
        )
        challenge_events = tuple(
            session.scalars(select(OtpChallengeEvent.action)).all()
        )

    assert isinstance(issue_result, RegistrationOtpPrerequisiteFailed)
    assert issue_result.error is (
        RegistrationPrerequisiteError.TELEGRAM_PHONE_NOT_VERIFIED
    )
    assert isinstance(verify_result, RegistrationOtpVerificationResult)
    assert verify_result.outcome is (
        RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED
    )
    assert customer is not None
    assert (customer.onboarding_status, customer.activated_at) == ("draft", None)
    assert link is not None
    assert link.telegram_chat_id is not None
    assert link.unlinked_at is None
    assert link.phone_verified_at is None
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.failed_attempts == 0
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert current_session is not None
    assert current_session.revoked_at is None
    assert capability_counts == (1, 1, 1, 0, 1)
    assert challenge_events == (
        OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
    )


@pytest.mark.integration
def test_phone_verified_link_permits_login_and_registration_otp(
    m2_test_database: Engine,
) -> None:
    phone = "+998900001392"
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(session, phone=phone)
        readiness = get_registration_readiness(
            session,
            context=_activation_context(snapshot=snapshot, phone=phone),
            identity_crypto_config=synthetic_identity_crypto_config(),
        )
        login_result = issue_login_otp_in_transaction(
            session,
            _settings(m2_test_database),
            phone_input=phone,
            browser_binding_digest=_LOGIN_DIGEST,
            locale="uz-Latn",
            now=_NOW,
        )
        registration_result = issue_registration_otp(
            session,
            context=_activation_context(snapshot=snapshot, phone=phone),
            identity_crypto_config=synthetic_identity_crypto_config(),
            language=OtpWebLanguage.UZ_LATN,
            now=_NOW,
        )

    with Session(m2_test_database) as session:
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        challenges = tuple(
            session.scalars(select(OtpChallenge).order_by(OtpChallenge.purpose)).all()
        )
        dispatch_count = (
            session.scalar(select(func.count()).select_from(OtpDispatch)) or 0
        )
        issued_event_count = (
            session.scalar(
                select(func.count())
                .select_from(OtpChallengeEvent)
                .where(OtpChallengeEvent.action == OtpChallengeEventAction.ISSUED.value)
            )
            or 0
        )

    assert readiness.state is RegistrationReadinessState.READY_FOR_OTP
    assert all(
        component.status is RegistrationReadinessComponentStatus.COMPLETE
        for component in readiness.components
    )
    assert {component.component for component in readiness.components} == set(
        RegistrationReadinessComponent
    )
    assert login_result.outcome is OtpInternalOutcome.OTP_PENDING
    assert isinstance(registration_result, RegistrationOtpPendingDelivery)
    assert link is not None
    assert link.phone_verified_at == link.linked_at
    assert tuple(challenge.purpose for challenge in challenges) == (
        OtpPurpose.LOGIN.value,
        OtpPurpose.REGISTRATION.value,
    )
    assert all(
        challenge.telegram_link_id == link.id
        and challenge.telegram_linked_at == link.linked_at
        for challenge in challenges
    )
    assert dispatch_count == 2
    assert issued_event_count == 2


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


def _assert_same_phone_reverify_invalidates_both_purposes(
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
        result = _consume_matching_contact(
            session,
            raw_token=raw_token,
            chat_id=9_980_001_381,
            phone="+998900001381",
            now=relinked_at,
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
    assert link.phone_verified_at == relinked_at
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
def test_active_customer_protected_relink_is_atomic_and_invalidates_both_purposes(
    m2_test_database: Engine,
) -> None:
    _assert_same_phone_reverify_invalidates_both_purposes(m2_test_database)


@pytest.mark.integration
def test_same_phone_reverify_rotates_generation_and_stales_both_purposes(
    m2_test_database: Engine,
) -> None:
    _assert_same_phone_reverify_invalidates_both_purposes(m2_test_database)


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
            _consume_matching_contact(
                session,
                raw_token=raw_token,
                chat_id=collision_chat_id,
                phone="+998900001382",
                now=_NOW + timedelta(seconds=1),
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
def test_activation_verify_and_link_token_issue_barrier_converges_without_deadlock(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001386",
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
            "synthetic-activation-link-issue-browser",
            SEED_NOW,
            settings=_settings(m2_test_database),
        )
        session.flush()
        registration_id = registration.id
        current_session_id = current.session.id
        original_link_state = (
            snapshot.telegram_link_id,
            snapshot.telegram_linked_at,
        )

    activation_write_reached = Event()
    issue_link_state_observed = Event()
    original_append_audit = activation_service_module.append_audit_event
    original_has_active_link = telegram_service_module.has_active_telegram_link

    def hold_activation_write(*args: object, **kwargs: object):
        result = original_append_audit(*args, **kwargs)
        activation_write_reached.set()
        assert issue_link_state_observed.wait(timeout=5)
        return result

    def observe_link_state(*args: object, **kwargs: object) -> bool:
        result = original_has_active_link(*args, **kwargs)
        issue_link_state_observed.set()
        return result

    monkeypatch.setattr(
        activation_service_module,
        "append_audit_event",
        hold_activation_write,
    )
    monkeypatch.setattr(
        telegram_service_module,
        "has_active_telegram_link",
        observe_link_state,
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

    def issue_link_token() -> ErrorCode:
        assert activation_write_reached.wait(timeout=5)
        with Session(m2_test_database) as session, session.begin():
            user = session.get(User, snapshot.user_id)
            assert user is not None
            with pytest.raises(TelegramLinkTokenIssueError) as caught:
                telegram_service_module.issue_link_token_after_rate_limit(
                    session,
                    user,
                    _NOW + timedelta(seconds=1),
                    token_generator=lambda _size: "activation_link_issue_barrier_token",
                )
            return caught.value.error_code

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        activation_future = executor.submit(activate)
        issue_future = executor.submit(issue_link_token)
        completed, pending = wait((activation_future, issue_future), timeout=10)
        assert not pending
        assert len(completed) == 2
        assert activation_future.result() == "ACTIVATED"
        assert issue_future.result() is ErrorCode.TELEGRAM_ALREADY_LINKED
    finally:
        issue_link_state_observed.set()
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        customer = session.get(Customer, snapshot.customer_id)
        link = session.get(TelegramLink, snapshot.telegram_link_id)
        challenge = session.get(OtpChallenge, registration_id)
        old_session = session.get(AuthSession, current_session_id)
        token_count = session.scalar(
            select(func.count()).select_from(TelegramLinkToken)
        )
        link_event_count = session.scalar(
            select(func.count()).select_from(TelegramLinkEvent)
        )
        activation_audit_count = session.scalar(
            select(func.count()).select_from(AuditLog)
        )
        authenticated_session_count = session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == snapshot.user_id)
        )
        assert customer is not None
        assert link is not None
        assert challenge is not None
        assert old_session is not None

    assert (customer.onboarding_status, customer.activated_at) == ("active", _NOW)
    assert (link.id, link.linked_at) == original_link_state
    assert link.phone_verified_at == link.linked_at
    assert link.unlinked_at is None
    assert challenge.status == OtpChallengeStatus.CONSUMED.value
    assert old_session.revoked_at == _NOW
    assert token_count == 0
    assert link_event_count == 0
    assert activation_audit_count == 1
    assert authenticated_session_count == 2


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
            result = _consume_matching_contact(
                session,
                raw_token=raw_token,
                chat_id=9_980_001_385,
                phone="+998900001385",
                now=_NOW + timedelta(seconds=1),
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
