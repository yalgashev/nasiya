from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer_activation.router as activation_router_module
import tests.test_m11_sensitive_data_leakage as leakage_tests
from app.audit.models import AuditLog
from app.auth.deps import CurrentSessionContext, CurrentSessionStatus
from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.auth.sessions import RawSessionToken, create_authenticated_session
from app.customer.models import Customer
from app.customer_activation.contracts import (
    ActivationCsrfSecret,
    ActivationSafeDeviceMetadata,
    ActivationSessionRotation,
    ActivationSessionSecrets,
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    CustomerAlreadyActive,
    PreparedCustomerActivation,
    RegistrationOtpCooldown,
    RegistrationOtpPendingDelivery,
    RegistrationOtpPrerequisiteFailed,
    RegistrationOtpRateLimited,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
    RegistrationPrerequisiteError,
    RequestNewRegistrationOtpCode,
    RequestRegistrationOtp,
    VerifyRegistrationOtp,
)
from app.customer_activation.router import (
    activation_page,
    request_new_registration_otp_code,
    request_registration_otp,
    verify_registration_otp,
)
from app.customer_activation.service import (
    AuthenticatedActivationContext,
    derive_authenticated_activation_context,
)
from app.customer_identity.models import CustomerIdentity
from app.main import create_app
from app.otp.code import OtpCode
from app.otp.contracts import OtpChallengeStatus, OtpPurpose
from app.otp.crypto import OtpBrowserBindingDigest, compute_otp_code_mac
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import (
    activate_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
)
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink
from tests.m11_seed import (
    NOW as SEED_NOW,
)
from tests.m11_seed import seed_registration_snapshot

_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
_DIGEST = "b" * 64
_IP = "203.0.113.31"
_NOW = datetime(2026, 8, 2, 10, 15, tzinfo=UTC)
_PHONE = "+998900001330"
_CSRF_SECRET = "synthetic-csrf-secret"
_OTP_KEY = SecretStr("synthetic-otp-hmac-key-at-least-32-characters")


def _actor() -> CustomerActivationActor:
    return CustomerActivationActor(user_id=_USER_ID)


def _browser() -> CustomerActivationBrowserContext:
    return CustomerActivationBrowserContext(
        current_session_id=_SESSION_ID,
        browser_binding_digest=OtpBrowserBindingDigest(_DIGEST),
    )


def _current_context(
    *,
    status: CurrentSessionStatus = CurrentSessionStatus.AUTHENTICATED,
    user_active: bool = True,
    revoked: bool = False,
    expires_at: datetime | None = None,
) -> CurrentSessionContext:
    user = User(
        id=_USER_ID,
        phone=_PHONE,
        password_hash=None,
        is_active=user_active,
        is_platform_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    session = AuthSession(
        id=_SESSION_ID,
        user_id=_USER_ID,
        active_shop_id=None,
        token_hash="e" * 64,
        csrf_secret=_CSRF_SECRET,
        user_agent="synthetic-browser",
        created_at=_NOW,
        last_seen_at=_NOW,
        expires_at=expires_at or _NOW + timedelta(days=1),
        revoked_at=_NOW if revoked else None,
    )
    return CurrentSessionContext(
        status=status,
        session_id=_SESSION_ID,
        user_id=_USER_ID,
        _session=session,
        _user=user,
    )


@pytest.mark.parametrize(
    "command_type",
    [RequestRegistrationOtp, RequestNewRegistrationOtpCode],
)
def test_issue_and_new_code_commands_have_only_server_context(command_type) -> None:
    command = command_type(
        actor=_actor(),
        browser=_browser(),
        trusted_client_ip=ResolvedClientIp(_IP),
        now=_NOW,
    )

    assert tuple(field.name for field in fields(command)) == (
        "actor",
        "browser",
        "trusted_client_ip",
        "now",
    )
    assert command.now == _NOW
    forbidden = {
        "phone",
        "purpose",
        "customer_id",
        "challenge_id",
        "dispatch_id",
        "telegram_link_id",
        "acceptance_id",
        "identity_id",
        "document_id",
        "object_file_id",
        "code",
        "raw_code",
    }
    assert forbidden.isdisjoint(field.name for field in fields(command))


def test_issue_and_new_code_commands_require_typed_aware_context() -> None:
    base = {
        "actor": _actor(),
        "browser": _browser(),
        "trusted_client_ip": ResolvedClientIp(_IP),
        "now": _NOW,
    }
    invalid = (
        {"actor": _USER_ID},
        {"browser": _SESSION_ID},
        {"trusted_client_ip": _IP},
        {"now": datetime(2026, 8, 2, 10, 15)},
    )

    for override in invalid:
        with pytest.raises((TypeError, ValueError)):
            RequestRegistrationOtp(**{**base, **override})  # type: ignore[arg-type]


def test_command_and_context_repr_redact_identity_session_digest_ip_and_time() -> None:
    command = RequestNewRegistrationOtpCode(
        actor=_actor(),
        browser=_browser(),
        trusted_client_ip=ResolvedClientIp(_IP),
        now=_NOW,
    )
    rendered = " ".join((repr(command), repr(command.actor), repr(command.browser)))

    for forbidden in (
        str(_USER_ID),
        str(_SESSION_ID),
        _DIGEST,
        _IP,
        _NOW.isoformat(),
    ):
        assert forbidden not in rendered
    assert "redacted" in rendered


def test_request_results_are_exact_safe_identifier_free_variants() -> None:
    results = (
        RegistrationOtpPendingDelivery(),
        CustomerAlreadyActive(),
        RegistrationOtpPrerequisiteFailed(
            RegistrationPrerequisiteError.CUSTOMER_IDENTITY_UNAVAILABLE
        ),
        RegistrationOtpCooldown(),
        RegistrationOtpRateLimited(),
    )

    assert [type(result).__name__ for result in results] == [
        "RegistrationOtpPendingDelivery",
        "CustomerAlreadyActive",
        "RegistrationOtpPrerequisiteFailed",
        "RegistrationOtpCooldown",
        "RegistrationOtpRateLimited",
    ]
    forbidden_fields = {
        "challenge_id",
        "dispatch_id",
        "provider_status",
        "delivery_status",
        "telegram_link_id",
        "code",
    }
    for result in results:
        assert forbidden_fields.isdisjoint(field.name for field in fields(result))


def test_new_code_contract_cannot_carry_or_resend_an_old_code() -> None:
    command_fields = {field.name for field in fields(RequestNewRegistrationOtpCode)}

    assert {"code", "raw_code", "old_code", "challenge_id"}.isdisjoint(command_fields)
    with pytest.raises(TypeError):
        RequestNewRegistrationOtpCode(
            actor=_actor(),
            browser=_browser(),
            trusted_client_ip=ResolvedClientIp(_IP),
            now=_NOW,
            raw_code="123456",  # type: ignore[call-arg]
        )


def test_activation_context_is_server_derived_detached_and_redacted() -> None:
    context = derive_authenticated_activation_context(
        current_context=_current_context(),
        trusted_client_ip=ResolvedClientIp(_IP),
        otp_hmac_key=_OTP_KEY,
        now=_NOW,
    )

    assert isinstance(context, AuthenticatedActivationContext)
    assert context.actor == CustomerActivationActor(_USER_ID)
    assert context.browser.current_session_id == _SESSION_ID
    assert context.canonical_account_phone_for_rate_limit() == _PHONE
    rendered = repr(context)
    for forbidden in (
        str(_USER_ID),
        str(_SESSION_ID),
        _PHONE,
        _IP,
        _CSRF_SECRET,
        context.browser.browser_binding_digest.as_stored_value(),
    ):
        assert forbidden not in rendered
    assert rendered.count("<redacted>") == 4


@pytest.mark.parametrize(
    "context",
    (
        _current_context(status=CurrentSessionStatus.ANONYMOUS),
        _current_context(user_active=False),
        _current_context(revoked=True),
        _current_context(expires_at=_NOW),
    ),
)
def test_activation_context_rejects_inactive_revoked_or_expired_auth(
    context: CurrentSessionContext,
) -> None:
    assert (
        derive_authenticated_activation_context(
            current_context=context,
            trusted_client_ip=ResolvedClientIp(_IP),
            otp_hmac_key=_OTP_KEY,
            now=_NOW,
        )
        is None
    )


def test_activation_context_accepts_no_client_authority_arguments() -> None:
    parameters = signature(derive_authenticated_activation_context).parameters
    assert {
        "phone",
        "purpose",
        "user_id",
        "customer_id",
        "session_id",
        "challenge_id",
    }.isdisjoint(parameters)
    with pytest.raises(TypeError):
        derive_authenticated_activation_context(
            current_context=_current_context(),
            trusted_client_ip=ResolvedClientIp(_IP),
            otp_hmac_key=_OTP_KEY,
            now=_NOW,
            customer_id=UUID("33333333-3333-4333-8333-333333333333"),
        )


def test_activation_router_has_exact_four_fixed_routes_once() -> None:
    application = create_app(
        Settings(
            _env_file=None,
            app_environment="testing",
            debug=False,
            database_url="postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test",
            session_cookie_secure=False,
            rate_limit_hmac_key="m11-web-route-inventory-key-at-least-32-characters",
        )
    )
    inventory = [
        (route.path, frozenset(route.methods or ()))
        for route in _iter_api_routes(application.routes)
        if isinstance(route, APIRoute) and route.path.startswith("/customer/activation")
    ]

    assert inventory == [
        ("/customer/activation", frozenset({"GET"})),
        ("/customer/activation/otp/request", frozenset({"POST"})),
        ("/customer/activation/otp/verify", frozenset({"POST"})),
        ("/customer/activation/otp/new-code", frozenset({"POST"})),
    ]


@pytest.mark.parametrize(
    "route_function",
    (
        activation_page,
        request_registration_otp,
        verify_registration_otp,
        request_new_registration_otp_code,
    ),
)
def test_activation_route_signatures_have_no_client_authority(
    route_function,
) -> None:
    parameters = signature(route_function).parameters
    forbidden = {
        "phone",
        "purpose",
        "user_id",
        "customer_id",
        "challenge_id",
        "dispatch_id",
        "telegram_link_id",
        "offer_id",
        "acceptance_id",
        "identity_id",
        "document_id",
        "object_file_id",
        "session_id",
    }

    assert forbidden.isdisjoint(parameters)
    assert {"request", "settings", "context"}.issubset(parameters)


def _encoded_identity_key(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _web_settings(engine: Engine, *, secure_cookie: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=secure_cookie,
        rate_limit_hmac_key="m11-web-readiness-rate-key-at-least-32-characters",
        otp_hmac_key=_OTP_KEY,
        customer_identity_active_key_id="identity-v1",
        customer_identity_encryption_keys=json.dumps(
            {"identity-v1": _encoded_identity_key(bytes(range(32)))},
            separators=(",", ":"),
        ),
        customer_identity_blind_index_key=_encoded_identity_key(
            bytes(reversed(range(32)))
        ),
    )


def _activation_request(
    settings: Settings,
    *,
    method: str = "GET",
    query_string: bytes = b"",
    accept_language: bytes = b"uz-Latn",
) -> Request:
    application = create_app(settings)
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": "/customer/activation",
            "raw_path": b"/customer/activation",
            "query_string": query_string,
            "headers": [(b"accept-language", accept_language)],
            "client": ("203.0.113.131", 443),
            "server": ("testserver", 80),
            "root_path": "",
            "app": application,
            "router": application.router,
        }
    )


def _activation_form_request(
    settings: Settings,
    *,
    path: str,
    fields: tuple[tuple[str, str], ...],
) -> Request:
    application = create_app(settings)
    body = urlencode(fields).encode("ascii")
    received = False

    async def receive() -> dict[str, object]:
        nonlocal received
        if received:
            return {"type": "http.request", "body": b"", "more_body": False}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [
                (b"accept-language", b"uz-Latn"),
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
            "client": ("203.0.113.131", 443),
            "server": ("testserver", 443),
            "root_path": "",
            "app": application,
            "router": application.router,
        },
        receive=receive,
    )


def _authenticated_context(
    *,
    user: User,
    session: AuthSession,
) -> CurrentSessionContext:
    return CurrentSessionContext(
        status=CurrentSessionStatus.AUTHENTICATED,
        session_id=session.id,
        user_id=user.id,
        _session=session,
        _user=user,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("state", "accept_language"),
    (
        ("ready", b"uz-Latn"),
        ("incomplete", b"uz-Latn"),
        ("active", b"uz-Latn"),
        ("ready", b"ru-RU"),
    ),
)
def test_activation_get_is_pii_free_no_store_and_zero_mutation(
    state: str,
    accept_language: bytes,
    m2_test_database: Engine,
) -> None:
    settings = _web_settings(m2_test_database)
    web_now = SEED_NOW + timedelta(minutes=30)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone={
                "ready": "+998900001386",
                "incomplete": "+998900001387",
                "active": "+998900001388",
            }[state]
            if accept_language == b"uz-Latn"
            else "+998900001391",
        )
        if state == "incomplete":
            identity = session.get(CustomerIdentity, snapshot.customer_id)
            assert identity is not None
            session.delete(identity)
        elif state == "active":
            customer = session.get(Customer, snapshot.customer_id)
            assert customer is not None
            customer.onboarding_status = "active"
            customer.activated_at = web_now
            customer.updated_at = web_now
        created = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-primary-browser",
            SEED_NOW,
            settings=settings,
        )
        user = session.get(User, snapshot.user_id)
        assert user is not None
        session.flush()
        before = tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                Customer,
                OtpChallenge,
                OtpDispatch,
                OtpChallengeEvent,
                AuditLog,
                AuthRateLimit,
                AuthSession,
            )
        )
        session_snapshot = (
            created.session.last_seen_at,
            created.session.revoked_at,
            created.session.csrf_secret,
        )

        response = activation_page(
            _activation_request(settings, accept_language=accept_language),
            db=session,
            settings=settings,
            context=_authenticated_context(user=user, session=created.session),
            now=web_now,
        )
        body = response.body.decode("utf-8")
        after = tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                Customer,
                OtpChallenge,
                OtpDispatch,
                OtpChallengeEvent,
                AuditLog,
                AuthRateLimit,
                AuthSession,
            )
        )
        after_session_snapshot = (
            created.session.last_seen_at,
            created.session.revoked_at,
            created.session.csrf_secret,
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert before == after
    assert session_snapshot == after_session_snapshot
    assert all(
        forbidden not in body
        for forbidden in (
            "+998900001386",
            "+998900001387",
            "+998900001388",
            "+998900001391",
            "Synthetic",
            "Specimen",
            "12345678901234",
            "AB 12345",
            str(snapshot.user_id),
            str(snapshot.customer_id),
            str(snapshot.telegram_link_id),
            str(snapshot.registration_offer_acceptance_id),
            str(snapshot.customer_document_id),
            "Synthetic registration offer body",
            "object_file",
            "provider_status",
            "challenge_id",
            "dispatch_id",
        )
    )
    if state == "ready":
        assert 'action="/customer/activation/otp/request"' in body
        assert 'action="/customer/activation/otp/verify"' in body
        assert 'action="/customer/activation/otp/new-code"' in body
        assert 'name="csrf_token"' in body
        assert 'name="code" type="text"' in body
        assert 'inputmode="numeric"' in body
        assert 'autocomplete="one-time-code"' in body
        assert 'maxlength="6"' in body
        if accept_language == b"ru-RU":
            assert "Активация клиента" in body
            assert "Получить код активации" in body
            assert "Шестизначный код" in body
            assert "Запросить новый код" in body
    elif state == "active":
        assert 'action="/customer/activation/otp/' not in body
        assert "Mijoz faollashtirilgan" in body
        assert 'name="csrf_token"' not in body
    else:
        assert 'action="/customer/activation/otp/' not in body
        assert "Tugallanmagan" in body
        assert 'name="csrf_token"' not in body


@pytest.mark.parametrize(
    ("service_result", "expected_location"),
    (
        (RegistrationOtpPendingDelivery(), "/customer/activation?notice=otp-pending"),
        (CustomerAlreadyActive(), "/customer/activation"),
        (
            RegistrationOtpPrerequisiteFailed(
                RegistrationPrerequisiteError.TELEGRAM_NOT_LINKED
            ),
            "/customer/activation?error=TELEGRAM_NOT_LINKED",
        ),
        (RegistrationOtpCooldown(), "/customer/activation?error=RATE_LIMITED"),
        (RegistrationOtpRateLimited(), "/customer/activation?error=RATE_LIMITED"),
    ),
)
def test_request_post_uses_server_context_and_fixed_safe_prg(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    service_result: object,
    expected_location: str,
) -> None:
    settings = _web_settings(m2_test_database)
    captured: dict[str, object] = {}

    def service_spy(session_factory, **kwargs):
        captured["session_factory"] = session_factory
        captured.update(kwargs)
        return service_result

    monkeypatch.setattr(
        activation_router_module,
        "request_registration_otp_service",
        service_spy,
    )
    request = _activation_request(
        settings,
        method="POST",
        query_string=(
            b"purpose=LOGIN&phone=%2B998900000000&customer_id="
            b"33333333-3333-4333-8333-333333333333"
        ),
    )
    context = _current_context()

    response = request_registration_otp(
        request,
        settings=settings,
        context=context,
        now=_NOW,
        _csrf=None,
    )

    activation_context = captured["context"]
    assert isinstance(activation_context, AuthenticatedActivationContext)
    assert activation_context.actor.user_id == _USER_ID
    assert captured["now"] == _NOW
    assert set(captured) == {
        "session_factory",
        "context",
        "settings",
        "identity_crypto_config",
        "language",
        "now",
    }
    assert response.status_code == 303
    assert response.headers["location"] == expected_location
    assert response.headers["Cache-Control"] == "no-store"
    assert all(
        forbidden not in response.headers["location"]
        for forbidden in (
            "LOGIN",
            "+998900000000",
            "33333333-3333-4333-8333-333333333333",
        )
    )


def test_request_post_has_no_telegram_transport_import_or_call() -> None:
    source = signature(request_registration_otp).parameters
    route_source = getsource(request_registration_otp)

    assert "purpose" not in source
    assert "phone" not in source
    assert "TelegramOtpProvider" not in route_source
    assert "create_telegram_http_client" not in route_source


@pytest.mark.parametrize(
    ("service_result", "expected_location"),
    (
        (RegistrationOtpPendingDelivery(), "/customer/activation?notice=otp-pending"),
        (CustomerAlreadyActive(), "/customer/activation"),
        (
            RegistrationOtpPrerequisiteFailed(
                RegistrationPrerequisiteError.CUSTOMER_DOCUMENT_UNAVAILABLE
            ),
            "/customer/activation?error=CUSTOMER_DOCUMENT_UNAVAILABLE",
        ),
        (RegistrationOtpCooldown(), "/customer/activation?error=RATE_LIMITED"),
        (RegistrationOtpRateLimited(), "/customer/activation?error=RATE_LIMITED"),
    ),
)
def test_new_code_post_uses_server_context_and_non_oracular_safe_prg(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    service_result: object,
    expected_location: str,
) -> None:
    settings = _web_settings(m2_test_database)
    captured: dict[str, object] = {}

    def service_spy(session_factory, **kwargs):
        captured["session_factory"] = session_factory
        captured.update(kwargs)
        return service_result

    monkeypatch.setattr(
        activation_router_module,
        "request_new_registration_otp_service",
        service_spy,
    )
    request = _activation_request(
        settings,
        method="POST",
        query_string=(
            b"purpose=LOGIN&challenge_id="
            b"33333333-3333-4333-8333-333333333333&old_code=004271"
        ),
    )

    response = request_new_registration_otp_code(
        request,
        settings=settings,
        context=_current_context(),
        now=_NOW,
        _csrf=None,
    )

    activation_context = captured["context"]
    assert isinstance(activation_context, AuthenticatedActivationContext)
    assert activation_context.actor.user_id == _USER_ID
    assert captured["now"] == _NOW
    assert set(captured) == {
        "session_factory",
        "context",
        "settings",
        "identity_crypto_config",
        "language",
        "now",
    }
    assert response.status_code == 303
    assert response.headers["location"] == expected_location
    assert response.headers["Cache-Control"] == "no-store"
    assert all(
        forbidden not in response.headers["location"]
        for forbidden in (
            "LOGIN",
            "33333333-3333-4333-8333-333333333333",
            "004271",
            "attempt",
            "provider",
            "second",
        )
    )


def test_duplicate_new_code_clicks_delegate_without_old_code_or_sync_transport(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _web_settings(m2_test_database)
    calls: list[AuthenticatedActivationContext] = []

    def service_spy(session_factory, **kwargs):
        _ = session_factory
        calls.append(kwargs["context"])
        return RegistrationOtpCooldown()

    monkeypatch.setattr(
        activation_router_module,
        "request_new_registration_otp_service",
        service_spy,
    )

    responses = tuple(
        request_new_registration_otp_code(
            _activation_request(settings, method="POST"),
            settings=settings,
            context=_current_context(),
            now=_NOW,
            _csrf=None,
        )
        for _ in range(2)
    )

    assert len(calls) == 2
    assert all(call.actor.user_id == _USER_ID for call in calls)
    assert all(
        response.headers["location"] == "/customer/activation?error=RATE_LIMITED"
        for response in responses
    )
    route_source = getsource(request_new_registration_otp_code)
    assert "TelegramOtpProvider" not in route_source
    assert "create_telegram_http_client" not in route_source
    assert "old_code" not in route_source


def _prepared_activation() -> PreparedCustomerActivation:
    return PreparedCustomerActivation(
        _rotation=ActivationSessionRotation(
            previous_session_id=uuid4(),
            replacement_session_id=uuid4(),
            user_id=uuid4(),
            active_shop_id=None,
            safe_device_metadata=ActivationSafeDeviceMetadata(
                user_agent="synthetic-browser"
            ),
            _replacement_secrets=ActivationSessionSecrets(
                token=RawSessionToken("synthetic-replacement-session-token"),
                csrf_secret=ActivationCsrfSecret("synthetic-replacement-csrf"),
            ),
        )
    )


@pytest.mark.parametrize(
    ("outcome", "expected_location"),
    (
        (
            RegistrationOtpVerificationOutcome.OTP_INVALID,
            "/customer/activation?error=OTP_INVALID",
        ),
        (
            RegistrationOtpVerificationOutcome.CUSTOMER_ACTIVATION_CHANGED,
            "/customer/activation?error=CUSTOMER_ACTIVATION_CHANGED",
        ),
        (
            RegistrationOtpVerificationOutcome.RATE_LIMITED,
            "/customer/activation?error=RATE_LIMITED",
        ),
        (
            RegistrationOtpVerificationOutcome.ALREADY_ACTIVE,
            "/customer/activation",
        ),
    ),
)
def test_verify_post_forwards_raw_string_without_client_authority_and_uses_safe_prg(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    outcome: RegistrationOtpVerificationOutcome,
    expected_location: str,
) -> None:
    settings = _web_settings(m2_test_database)
    captured: dict[str, object] = {}

    def verify_spy(db, **kwargs):
        captured["db"] = db
        captured.update(kwargs)
        return RegistrationOtpVerificationResult(outcome)

    monkeypatch.setattr(
        activation_router_module,
        "verify_and_activate_registration_customer",
        verify_spy,
    )
    request = _activation_form_request(
        settings,
        path="/customer/activation/otp/verify",
        fields=(("csrf_token", "synthetic"), ("code", "004271")),
    )
    database_boundary = object()

    response = asyncio.run(
        verify_registration_otp(
            request,
            db=database_boundary,  # type: ignore[arg-type]
            settings=settings,
            context=_current_context(),
            now=_NOW,
            _csrf=None,
        )
    )

    command = captured["command"]
    assert isinstance(command, VerifyRegistrationOtp)
    assert command.candidate_code == "004271"
    assert command.actor.user_id == _USER_ID
    assert captured["db"] is database_boundary
    assert set(captured) == {
        "db",
        "command",
        "settings",
        "identity_crypto_config",
    }
    assert response.status_code == 303
    assert response.headers["location"] == expected_location
    assert response.headers["Cache-Control"] == "no-store"
    assert "004271" not in response.headers["location"]
    assert "set-cookie" not in response.headers


def test_verify_post_rejects_forged_authority_fields_before_service(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _web_settings(m2_test_database)
    called = False

    def unexpected_service_call(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("verify service must not be called")

    monkeypatch.setattr(
        activation_router_module,
        "verify_and_activate_registration_customer",
        unexpected_service_call,
    )
    request = _activation_form_request(
        settings,
        path="/customer/activation/otp/verify",
        fields=(
            ("csrf_token", "synthetic"),
            ("code", "004271"),
            ("purpose", "LOGIN"),
            ("challenge_id", str(uuid4())),
        ),
    )

    response = asyncio.run(
        verify_registration_otp(
            request,
            db=object(),  # type: ignore[arg-type]
            settings=settings,
            context=_current_context(),
            now=_NOW,
            _csrf=None,
        )
    )

    assert called is False
    assert response.status_code == 303
    assert response.headers["location"] == "/customer/activation?error=OTP_INVALID"
    assert "004271" not in response.headers["location"]


def test_first_activation_cookie_is_secure_http_only_and_replay_has_no_rotation(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _web_settings(m2_test_database, secure_cookie=True)
    monkeypatch.setattr(
        activation_router_module,
        "verify_and_activate_registration_customer",
        lambda *args, **kwargs: _prepared_activation(),
    )
    request = _activation_form_request(
        settings,
        path="/customer/activation/otp/verify",
        fields=(("csrf_token", "synthetic"), ("code", "004271")),
    )

    first = asyncio.run(
        verify_registration_otp(
            request,
            db=object(),  # type: ignore[arg-type]
            settings=settings,
            context=_current_context(),
            now=_NOW,
            _csrf=None,
        )
    )
    cookie = first.headers["set-cookie"]

    assert first.status_code == 303
    assert first.headers["location"] == "/customer/activation"
    assert first.headers["Cache-Control"] == "no-store"
    assert settings.session_cookie_name in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "004271" not in cookie

    monkeypatch.setattr(
        activation_router_module,
        "verify_and_activate_registration_customer",
        lambda *args, **kwargs: RegistrationOtpVerificationResult(
            RegistrationOtpVerificationOutcome.ALREADY_ACTIVE
        ),
    )
    replay = asyncio.run(
        verify_registration_otp(
            _activation_form_request(
                settings,
                path="/customer/activation/otp/verify",
                fields=(("csrf_token", "synthetic"), ("code", "004271")),
            ),
            db=object(),  # type: ignore[arg-type]
            settings=settings,
            context=_current_context(),
            now=_NOW,
            _csrf=None,
        )
    )

    assert replay.headers["location"] == "/customer/activation"
    assert replay.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in replay.headers


def _create_web_registration_candidate(
    session: Session,
    *,
    settings: Settings,
    phone: str,
) -> tuple[UUID, UUID, UUID]:
    snapshot = seed_registration_snapshot(session, phone=phone)
    created = create_authenticated_session(
        session,
        snapshot.user_id,
        "synthetic-browser",
        SEED_NOW,
        settings=settings,
    )
    user = session.get(User, snapshot.user_id)
    assert user is not None
    context = _authenticated_context(user=user, session=created.session)
    activation_context = derive_authenticated_activation_context(
        current_context=context,
        trusted_client_ip=ResolvedClientIp("203.0.113.131"),
        otp_hmac_key=settings.require_otp_hmac_key(),
        now=SEED_NOW + timedelta(minutes=1),
    )
    assert activation_context is not None
    snapshot = replace(
        snapshot,
        browser_binding_digest=activation_context.browser.browser_binding_digest,
    )
    challenge = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=SEED_NOW,
    )
    create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=SEED_NOW,
    )
    activate_challenge(
        session,
        challenge=challenge,
        code_mac=compute_otp_code_mac(
            otp_hmac_key=settings.require_otp_hmac_key(),
            challenge_id=challenge.id,
            user_id=snapshot.user_id,
            purpose=OtpPurpose.REGISTRATION,
            code=OtpCode("004271"),
        ),
        activated_at=SEED_NOW + timedelta(seconds=1),
        expires_at=SEED_NOW + timedelta(minutes=5),
    )
    session.flush()
    return snapshot.user_id, created.session.id, challenge.id


@pytest.mark.integration
def test_wrong_owner_web_new_code_and_verify_are_safe_and_contained(
    m2_test_database: Engine,
) -> None:
    settings = _web_settings(m2_test_database, secure_cookie=True)
    with Session(m2_test_database) as session, session.begin():
        user_id, current_session_id, challenge_id = _create_web_registration_candidate(
            session,
            settings=settings,
            phone="+998900001392",
        )
        challenge = session.get(OtpChallenge, challenge_id)
        current_session = session.get(AuthSession, current_session_id)
        assert challenge is not None
        assert current_session is not None
        link = session.get(TelegramLink, challenge.telegram_link_id)
        dispatch = session.scalar(
            select(OtpDispatch).where(OtpDispatch.challenge_id == challenge_id)
        )
        customer = session.scalar(select(Customer).where(Customer.user_id == user_id))
        assert link is not None
        assert dispatch is not None
        assert customer is not None
        other_user = User(
            phone="+998900001393",
            password_hash=None,
            is_active=True,
            is_platform_admin=False,
            created_at=SEED_NOW,
            updated_at=SEED_NOW,
        )
        session.add(other_user)
        session.flush()
        link.user_id = other_user.id
        link.updated_at = SEED_NOW + timedelta(minutes=1)
        session.flush()
        dispatch_id = dispatch.id
        expected_link = (
            link.user_id,
            link.telegram_chat_id,
            link.linked_at,
            link.phone_verified_at,
            link.unlinked_at,
            link.updated_at,
        )
        expected_session = (
            current_session.user_id,
            current_session.token_hash,
            current_session.csrf_secret,
            current_session.created_at,
            current_session.last_seen_at,
            current_session.expires_at,
            current_session.revoked_at,
        )

    with Session(m2_test_database) as session:
        user = session.get(User, user_id)
        current_session = session.get(AuthSession, current_session_id)
        assert user is not None
        assert current_session is not None
        session.expunge(user)
        session.expunge(current_session)
    context = _authenticated_context(user=user, session=current_session)

    new_code_response = request_new_registration_otp_code(
        _activation_form_request(
            settings,
            path="/customer/activation/otp/new-code",
            fields=(("csrf_token", "synthetic"),),
        ),
        settings=settings,
        context=context,
        now=SEED_NOW + timedelta(minutes=2),
        _csrf=None,
    )

    with Session(m2_test_database) as session, session.begin():
        user = session.get(User, user_id)
        current_session = session.get(AuthSession, current_session_id)
        assert user is not None
        assert current_session is not None
        verify_response = asyncio.run(
            verify_registration_otp(
                _activation_form_request(
                    settings,
                    path="/customer/activation/otp/verify",
                    fields=(("csrf_token", "synthetic"), ("code", "004271")),
                ),
                db=session,
                settings=settings,
                context=_authenticated_context(
                    user=user,
                    session=current_session,
                ),
                now=SEED_NOW + timedelta(minutes=3),
                _csrf=None,
            )
        )

    with Session(m2_test_database) as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        link = session.scalar(select(TelegramLink))
        customer = session.scalar(select(Customer).where(Customer.user_id == user_id))
        current_session = session.get(AuthSession, current_session_id)
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
        rates = tuple(session.scalars(select(AuthRateLimit)))
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))
        session_count = session.scalar(select(func.count()).select_from(AuthSession))
        challenge_count = session.scalar(select(func.count()).select_from(OtpChallenge))
        dispatch_count = session.scalar(select(func.count()).select_from(OtpDispatch))
        assert challenge is not None
        assert dispatch is not None
        assert link is not None
        assert customer is not None
        assert current_session is not None

    assert new_code_response.status_code == 303
    assert new_code_response.headers["location"] == (
        "/customer/activation?error=TELEGRAM_NOT_LINKED"
    )
    assert new_code_response.headers["X-Error-Code"] == "TELEGRAM_NOT_LINKED"
    assert new_code_response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in new_code_response.headers
    assert verify_response.status_code == 303
    assert verify_response.headers["location"] == (
        "/customer/activation?error=CUSTOMER_ACTIVATION_CHANGED"
    )
    assert verify_response.headers["X-Error-Code"] == "CUSTOMER_ACTIVATION_CHANGED"
    assert verify_response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in verify_response.headers
    for response in (new_code_response, verify_response):
        rendered_headers = repr(tuple(response.headers.items()))
        assert "004271" not in rendered_headers
        assert str(user_id) not in rendered_headers
        assert str(challenge_id) not in rendered_headers
    assert (
        challenge.status,
        challenge.failed_attempts,
        challenge.consumed_at,
        challenge.terminal_at,
        challenge.updated_at,
    ) == (
        OtpChallengeStatus.INVALIDATED.value,
        0,
        None,
        SEED_NOW + timedelta(minutes=3),
        SEED_NOW + timedelta(minutes=3),
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
        "CANCELLED",
        None,
        None,
        None,
        SEED_NOW + timedelta(minutes=3),
        None,
        SEED_NOW + timedelta(minutes=3),
    )
    assert [(event.action, event.safe_code) for event in events] == [
        ("INVALIDATED_BY_LINK_CHANGE", None)
    ]
    assert (
        link.user_id,
        link.telegram_chat_id,
        link.linked_at,
        link.phone_verified_at,
        link.unlinked_at,
        link.updated_at,
    ) == expected_link
    assert (customer.onboarding_status, customer.activated_at) == ("draft", None)
    assert (
        current_session.user_id,
        current_session.token_hash,
        current_session.csrf_secret,
        current_session.created_at,
        current_session.last_seen_at,
        current_session.expires_at,
        current_session.revoked_at,
    ) == expected_session
    assert len(rates) == 3
    assert {rate.attempt_count for rate in rates} == {1}
    assert audit_count == 0
    assert session_count == challenge_count == dispatch_count == 1


@pytest.mark.integration
def test_verify_post_first_activation_commits_atomic_state_and_replay_is_cookie_free(
    m2_test_database: Engine,
) -> None:
    settings = _web_settings(m2_test_database, secure_cookie=True)
    with Session(m2_test_database) as session, session.begin():
        user_id, old_session_id, challenge_id = _create_web_registration_candidate(
            session,
            settings=settings,
            phone="+998900001389",
        )

    with Session(m2_test_database) as session, session.begin():
        user = session.get(User, user_id)
        old_session = session.get(AuthSession, old_session_id)
        assert user is not None
        assert old_session is not None
        response = asyncio.run(
            verify_registration_otp(
                _activation_form_request(
                    settings,
                    path="/customer/activation/otp/verify",
                    fields=(("csrf_token", "synthetic"), ("code", "004271")),
                ),
                db=session,
                settings=settings,
                context=_authenticated_context(user=user, session=old_session),
                now=SEED_NOW + timedelta(minutes=2),
                _csrf=None,
            )
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/customer/activation"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]

    with Session(m2_test_database) as session, session.begin():
        customer = session.scalar(select(Customer).where(Customer.user_id == user_id))
        old_session = session.get(AuthSession, old_session_id)
        challenge = session.get(OtpChallenge, challenge_id)
        sessions = list(
            session.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == user_id)
                .order_by(AuthSession.created_at, AuthSession.id)
            )
        )
        assert customer is not None
        assert customer.onboarding_status == "active"
        assert old_session is not None and old_session.revoked_at is not None
        assert challenge is not None
        assert challenge.status == OtpChallengeStatus.CONSUMED.value
        assert len(sessions) == 2
        replacement = next(row for row in sessions if row.id != old_session_id)
        user = session.get(User, user_id)
        assert user is not None

        replay = asyncio.run(
            verify_registration_otp(
                _activation_form_request(
                    settings,
                    path="/customer/activation/otp/verify",
                    fields=(("csrf_token", "synthetic"), ("code", "004271")),
                ),
                db=session,
                settings=settings,
                context=_authenticated_context(user=user, session=replacement),
                now=SEED_NOW + timedelta(minutes=3),
                _csrf=None,
            )
        )

    assert replay.status_code == 303
    assert replay.headers["location"] == "/customer/activation"
    assert "set-cookie" not in replay.headers


@pytest.mark.integration
def test_cookie_preparation_failure_rolls_back_entire_activation(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _web_settings(m2_test_database, secure_cookie=True)
    with Session(m2_test_database) as session, session.begin():
        user_id, old_session_id, challenge_id = _create_web_registration_candidate(
            session,
            settings=settings,
            phone="+998900001390",
        )

    def fail_cookie_preparation(*args, **kwargs) -> None:
        raise RuntimeError("synthetic cookie preparation failure")

    monkeypatch.setattr(
        activation_router_module,
        "set_session_cookie",
        fail_cookie_preparation,
    )
    with pytest.raises(RuntimeError, match="synthetic cookie preparation failure"):
        with Session(m2_test_database) as session, session.begin():
            user = session.get(User, user_id)
            old_session = session.get(AuthSession, old_session_id)
            assert user is not None
            assert old_session is not None
            asyncio.run(
                verify_registration_otp(
                    _activation_form_request(
                        settings,
                        path="/customer/activation/otp/verify",
                        fields=(
                            ("csrf_token", "synthetic"),
                            ("code", "004271"),
                        ),
                    ),
                    db=session,
                    settings=settings,
                    context=_authenticated_context(user=user, session=old_session),
                    now=SEED_NOW + timedelta(minutes=2),
                    _csrf=None,
                )
            )

    with Session(m2_test_database) as session:
        customer = session.scalar(select(Customer).where(Customer.user_id == user_id))
        old_session = session.get(AuthSession, old_session_id)
        challenge = session.get(OtpChallenge, challenge_id)
        session_count = session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == user_id)
        )

    assert customer is not None and customer.onboarding_status == "draft"
    assert old_session is not None and old_session.revoked_at is None
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert session_count == 1


def _iter_api_routes(routes: list[object]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            yield from _iter_api_routes(included_router.routes)


def test_activation_routes_ignore_forged_authority_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    for route_function in (
        activation_page,
        request_registration_otp,
        verify_registration_otp,
        request_new_registration_otp_code,
    ):
        test_activation_route_signatures_have_no_client_authority(route_function)
    test_verify_post_rejects_forged_authority_fields_before_service(
        monkeypatch,
        m2_test_database,
    )


@pytest.mark.parametrize(
    "path",
    (
        "/customer/activation/otp/request",
        "/customer/activation/otp/verify",
        "/customer/activation/otp/new-code",
    ),
)
def test_activation_posts_require_session_bound_csrf_before_any_write(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    leakage_tests.test_activation_post_csrf_matrix_is_prg_no_store_and_zero_domain_mutation(
        path,
        "wrong",
        monkeypatch,
        m2_test_database,
    )
