from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer_activation.router as activation_router_module
from app.audit.models import AuditLog
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.sessions import (
    CreatedSession,
    create_authenticated_session,
    revoke_session,
    rotate_session,
)
from app.customer.models import Customer
from app.customer_activation.contracts import (
    CustomerAlreadyActive,
    RegistrationOtpPendingDelivery,
    RegistrationOtpVerificationOutcome,
    RegistrationOtpVerificationResult,
)
from app.main import create_app
from app.otp.code import OtpCode
from app.otp.contracts import OtpPurpose
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.provider import (
    OtpDeliverySendStatus,
    TelegramOtpProvider,
    TelegramOtpTarget,
)
from app.settings import Settings
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.telegram.bot_api import TelegramApiError, TelegramApiErrorCode
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity

_NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)
_OTP_KEY = SecretStr("synthetic-activation-csrf-otp-key-at-least-32-characters")
_POST_PATHS = (
    "/customer/activation/otp/request",
    "/customer/activation/otp/verify",
    "/customer/activation/otp/new-code",
)
_CSRF_CASES = ("missing", "wrong", "cross-session", "rotated", "revoked", "expired")


def _encoded_key(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key="m11-csrf-rate-key-at-least-32-characters",
        otp_hmac_key=_OTP_KEY,
        customer_identity_active_key_id="identity-v1",
        customer_identity_encryption_keys=json.dumps(
            {"identity-v1": _encoded_key(bytes(range(32)))},
            separators=(",", ":"),
        ),
        customer_identity_blind_index_key=_encoded_key(bytes(reversed(range(32)))),
    )


def _created_session(
    session: Session,
    *,
    settings: Settings,
    phone: str,
) -> tuple[User, CreatedSession]:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    session.add(user)
    session.flush()
    created = create_authenticated_session(
        session,
        user.id,
        "synthetic-csrf-browser",
        _NOW,
        settings=settings,
    )
    session.flush()
    return user, created


def _set_cookie(
    client: TestClient,
    settings: Settings,
    created: CreatedSession,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )


def _csrf_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def _mutation_counts(session: Session) -> tuple[int, ...]:
    return tuple(
        session.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            Customer,
            OtpChallenge,
            OtpDispatch,
            AuditLog,
            AuthRateLimit,
        )
    )


def _post_data(path: str, csrf_token: str | None) -> dict[str, str]:
    data = {"code": "004271"} if path.endswith("/verify") else {}
    if csrf_token is not None:
        data["csrf_token"] = csrf_token
    return data


@pytest.mark.integration
@pytest.mark.parametrize("path", _POST_PATHS)
@pytest.mark.parametrize("csrf_case", _CSRF_CASES)
def test_activation_post_csrf_matrix_is_prg_no_store_and_zero_domain_mutation(
    path: str,
    csrf_case: str,
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    application = create_app(settings)
    client_now = _NOW
    with Session(m2_test_database) as session, session.begin():
        user, current = _created_session(
            session,
            settings=settings,
            phone="+998900001392",
        )
        submitted_token: str | None
        cookie_session = current
        if csrf_case == "missing":
            submitted_token = None
        elif csrf_case == "wrong":
            submitted_token = "synthetic-invalid-csrf"
        elif csrf_case == "cross-session":
            other = create_authenticated_session(
                session,
                user.id,
                "synthetic-other-browser",
                _NOW,
                settings=settings,
            )
            submitted_token = _csrf_value(other)
        elif csrf_case == "rotated":
            submitted_token = _csrf_value(current)
            cookie_session = rotate_session(
                session,
                current.session,
                user.id,
                "synthetic-rotated-browser",
                _NOW + timedelta(seconds=1),
                settings=settings,
            )
            client_now = _NOW + timedelta(seconds=1)
        elif csrf_case == "revoked":
            submitted_token = _csrf_value(current)
            revoke_session(session, current.session, _NOW + timedelta(seconds=1))
            client_now = _NOW + timedelta(seconds=2)
        else:
            submitted_token = _csrf_value(current)
            current.session.expires_at = _NOW + timedelta(seconds=1)
            client_now = _NOW + timedelta(seconds=2)

    application.dependency_overrides[get_current_time] = lambda: client_now
    monkeypatch.setattr(
        activation_router_module,
        "request_registration_otp_service",
        lambda *args, **kwargs: pytest.fail("CSRF must precede request service"),
    )
    monkeypatch.setattr(
        activation_router_module,
        "request_new_registration_otp_service",
        lambda *args, **kwargs: pytest.fail("CSRF must precede new-code service"),
    )
    monkeypatch.setattr(
        activation_router_module,
        "verify_and_activate_registration_customer",
        lambda *args, **kwargs: pytest.fail("CSRF must precede verify service"),
    )
    with Session(m2_test_database) as session:
        before = _mutation_counts(session)

    with TestClient(application, client=("203.0.113.141", 50_000)) as client:
        _set_cookie(client, settings, cookie_session)
        response = client.post(
            path,
            data=_post_data(path, submitted_token),
            headers={"accept": "text/html"},
            follow_redirects=False,
        )

    with Session(m2_test_database) as session:
        after = _mutation_counts(session)
    application.state.database_engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == ("/customer/activation?error=CSRF_FAILED")
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert response.headers["cache-control"] == "no-store"
    assert before == after
    assert "004271" not in response.headers["location"]
    assert "set-cookie" not in response.headers


@pytest.mark.integration
@pytest.mark.parametrize("path", _POST_PATHS)
def test_activation_mutation_prg_refresh_does_not_repeat_service_capability(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    application = create_app(settings)
    application.dependency_overrides[get_current_time] = lambda: _NOW
    with Session(m2_test_database) as session, session.begin():
        _user, created = _created_session(
            session,
            settings=settings,
            phone="+998900001393",
        )
        csrf_token = _csrf_value(created)
    calls = 0

    def request_result(*args, **kwargs):
        nonlocal calls
        calls += 1
        return RegistrationOtpPendingDelivery()

    def verify_result(*args, **kwargs):
        nonlocal calls
        calls += 1
        return RegistrationOtpVerificationResult(
            RegistrationOtpVerificationOutcome.OTP_INVALID
        )

    monkeypatch.setattr(
        activation_router_module,
        "request_registration_otp_service",
        request_result,
    )
    monkeypatch.setattr(
        activation_router_module,
        "request_new_registration_otp_service",
        request_result,
    )
    monkeypatch.setattr(
        activation_router_module,
        "verify_and_activate_registration_customer",
        verify_result,
    )

    with TestClient(application, client=("203.0.113.142", 50_000)) as client:
        _set_cookie(client, settings, created)
        mutation = client.post(
            path,
            data=_post_data(path, csrf_token),
            follow_redirects=False,
        )
        refreshed = client.get(mutation.headers["location"], follow_redirects=False)

    application.state.database_engine.dispose()

    assert mutation.status_code == 303
    assert mutation.headers["cache-control"] == "no-store"
    assert refreshed.status_code == 200
    assert refreshed.headers["cache-control"] == "no-store"
    assert calls == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "path",
    (
        "/customer/activation/otp/request",
        "/customer/activation/otp/new-code",
    ),
)
@pytest.mark.parametrize("privilege", ("ordinary", "shop-owner", "platform-admin"))
def test_privileged_or_ordinary_actor_cannot_select_a_foreign_customer(
    path: str,
    privilege: str,
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    application = create_app(settings)
    application.dependency_overrides[get_current_time] = lambda: _NOW
    with Session(m2_test_database) as session, session.begin():
        actor, created = _created_session(
            session,
            settings=settings,
            phone="+998900001394",
        )
        foreign_user = User(
            phone="+998900001395",
            password_hash=None,
            is_active=True,
            is_platform_admin=False,
            created_at=_NOW,
            updated_at=_NOW,
        )
        session.add(foreign_user)
        session.flush()
        if privilege == "platform-admin":
            actor.is_platform_admin = True
        elif privilege == "shop-owner":
            shop = Shop(
                name="Synthetic scope shop",
                phone="+998900001396",
                address_text=None,
                status="active",
                created_at=_NOW,
                updated_at=_NOW,
            )
            session.add(shop)
            session.flush()
            session.add(
                ShopStaff(
                    shop_id=shop.id,
                    user_id=actor.id,
                    role=ShopRole.OWNER.value,
                    is_active=True,
                    created_at=_NOW,
                    updated_at=_NOW,
                    revoked_at=None,
                )
            )
        csrf_token = _csrf_value(created)
        actor_id = actor.id
        foreign_user_id = foreign_user.id
    captured_actor_ids = []

    def own_actor_service(session_factory, **kwargs):
        _ = session_factory
        captured_actor_ids.append(kwargs["context"].actor.user_id)
        return CustomerAlreadyActive()

    monkeypatch.setattr(
        activation_router_module,
        "request_registration_otp_service",
        own_actor_service,
    )
    monkeypatch.setattr(
        activation_router_module,
        "request_new_registration_otp_service",
        own_actor_service,
    )
    forged = {
        "csrf_token": csrf_token,
        "purpose": "LOGIN",
        "user_id": str(foreign_user_id),
        "customer_id": "33333333-3333-4333-8333-333333333333",
        "challenge_id": "44444444-4444-4444-8444-444444444444",
        "telegram_link_id": "55555555-5555-4555-8555-555555555555",
        "acceptance_id": "66666666-6666-4666-8666-666666666666",
        "identity_id": "77777777-7777-4777-8777-777777777777",
        "document_id": "88888888-8888-4888-8888-888888888888",
        "object_file_id": "99999999-9999-4999-8999-999999999999",
        "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    }

    with TestClient(application, client=("203.0.113.143", 50_000)) as client:
        _set_cookie(client, settings, created)
        response = client.post(path, data=forged, follow_redirects=False)

    application.state.database_engine.dispose()

    assert captured_actor_ids == [actor_id]
    assert foreign_user_id not in captured_actor_ids
    assert response.status_code == 303
    assert response.headers["location"] == "/customer/activation"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    rendered_headers = " ".join(
        f"{key}: {value}" for key, value in response.headers.items()
    )
    assert all(value not in rendered_headers for value in forged.values())


@pytest.mark.integration
def test_anonymous_activation_attempts_never_reach_domain_service(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    application = create_app(settings)
    monkeypatch.setattr(
        activation_router_module,
        "request_registration_otp_service",
        lambda *args, **kwargs: pytest.fail("anonymous actor reached service"),
    )

    with TestClient(application, client=("203.0.113.144", 50_000)) as client:
        page = client.get(
            "/customer/activation",
            params={"customer_id": "33333333-3333-4333-8333-333333333333"},
            follow_redirects=False,
        )
        mutation = client.post(
            "/customer/activation/otp/request",
            data={
                "csrf_token": "synthetic-invalid-csrf",
                "customer_id": "33333333-3333-4333-8333-333333333333",
            },
            follow_redirects=False,
        )

    application.state.database_engine.dispose()

    assert page.status_code == 303
    assert page.headers["location"] == "/auth/login"
    assert page.headers["cache-control"] == "no-store"
    assert mutation.status_code == 303
    assert mutation.headers["location"] == ("/customer/activation?error=CSRF_FAILED")
    assert mutation.headers["cache-control"] == "no-store"


@pytest.mark.integration
def test_registration_forbidden_values_never_reach_db_audit_log_error_html_url_or_repr(
    caplog: pytest.LogCaptureFixture,
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    application = create_app(settings)
    application.dependency_overrides[get_current_time] = lambda: _NOW
    with Session(m2_test_database) as session, session.begin():
        user, created = _created_session(
            session,
            settings=settings,
            phone="+998900001397",
        )
        forbidden_values = (
            user.phone,
            str(user.id),
            created.raw_token.as_cookie_value(),
            _csrf_value(created),
            "004271",
            "f" * 64,
            "12345678901234",
            "AB 12345",
            "203.0.113.199",
            "https://storage.invalid/private-object",
            "<script>alert(1)</script>",
        )
    query = {f"forged_{index}": value for index, value in enumerate(forbidden_values)}
    query["error"] = "<script>alert(2)</script>"

    with TestClient(application, client=("203.0.113.145", 50_000)) as client:
        _set_cookie(client, settings, created)
        response = client.get(
            "/customer/activation",
            params=query,
            follow_redirects=False,
        )

    application.state.database_engine.dispose()
    with Session(m2_test_database) as session:
        persistence_repr = repr(
            (
                tuple(session.scalars(select(User))),
                tuple(session.scalars(select(AuthRateLimit))),
                tuple(session.scalars(select(OtpChallenge))),
                tuple(session.scalars(select(OtpDispatch))),
                tuple(session.scalars(select(AuditLog))),
            )
        )
    rendered = " ".join(
        (
            response.text,
            " ".join(f"{key}: {value}" for key, value in response.headers.items()),
            " ".join(record.getMessage() for record in caplog.records),
            persistence_repr,
        )
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    assert all(value not in rendered for value in forbidden_values)
    assert "<script" not in response.text.casefold()


class _FailingTelegramTransport:
    async def send_message(
        self,
        *,
        chat_id: VerifiedPrivateTelegramChatIdentity,
        text: str,
        timeout_seconds: int,
    ) -> None:
        _ = (chat_id, text, timeout_seconds)
        raise TelegramApiError(TelegramApiErrorCode.TRANSIENT_NETWORK)


def test_web_never_calls_telegram_and_dispatcher_never_leaks_transport_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    customer_activation_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("app/customer_activation").glob("*.py"))
    )
    for forbidden_transport_call in (
        "TelegramOtpProvider",
        "TelegramBotApiClient",
        ".send_otp(",
        ".send_message(",
    ):
        assert forbidden_transport_call not in customer_activation_source

    raw_code = "271828"
    raw_chat = "99890001489"
    provider = TelegramOtpProvider(
        bot_api_client=_FailingTelegramTransport(),  # type: ignore[arg-type]
        send_timeout_seconds=5,
    )
    result = asyncio.run(
        provider.send_otp(
            target=TelegramOtpTarget(
                chat_identity=VerifiedPrivateTelegramChatIdentity(int(raw_chat))
            ),
            code=OtpCode(raw_code),
            locale="uz-Latn",
            ttl_seconds=180,
            purpose=OtpPurpose.REGISTRATION,
        )
    )
    rendered = " ".join((repr(result), caplog.text))

    assert result.status is OtpDeliverySendStatus.UNKNOWN
    assert raw_code not in rendered
    assert raw_chat not in rendered
