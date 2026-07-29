import ast
import re
from collections import defaultdict
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.auth.router as auth_router_module
from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import hash_session_token
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.contracts import OtpChallengeStatus
from app.otp.dispatch_service import (
    PreparedOtpDispatch,
    prepare_next_otp_dispatch,
    record_otp_delivery_result,
)
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.provider import OtpDeliverySendResult, OtpDeliverySendStatus
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.telegram.models import TelegramLink

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-containment"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-containment-at-least-32"


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        anonymous_session_ttl_minutes=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=TEST_OTP_HMAC_KEY,
    )


def make_client(
    engine: Engine,
    now_provider: Callable[[], datetime] = lambda: NOW,
) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = now_provider
    return TestClient(application, client=("203.0.113.120", 50_000)), settings


def mutable_now() -> tuple[dict[str, datetime], Callable[[], datetime]]:
    state = {"now": NOW}
    return state, lambda: state["now"]


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def get_request_csrf(client: TestClient) -> str:
    response = client.get("/auth/otp")
    assert response.status_code == 200
    return extract_hidden_csrf_token(response.text)


def get_verify_csrf(client: TestClient) -> str:
    response = client.get("/auth/otp/verify")
    assert response.status_code == 200
    return extract_hidden_csrf_token(response.text)


def post_request(
    client: TestClient,
    *,
    csrf_token: str,
    phone: str,
):
    return client.post(
        "/auth/otp/request",
        data={"csrf_token": csrf_token, "phone": phone},
        follow_redirects=False,
    )


def post_verify(
    client: TestClient,
    *,
    csrf_token: str,
    code: str,
    extra_data: dict[str, str] | None = None,
):
    data = {"csrf_token": csrf_token, "code": code}
    if extra_data is not None:
        data.update(extra_data)
    return client.post("/auth/otp/verify", data=data, follow_redirects=False)


def post_new_code(
    client: TestClient,
    *,
    csrf_token: str,
    extra_data: dict[str, str] | None = None,
):
    data = {"csrf_token": csrf_token}
    if extra_data is not None:
        data.update(extra_data)
    return client.post("/auth/otp/new-code", data=data, follow_redirects=False)


def add_user_with_link(
    session: Session,
    *,
    phone: str,
) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    chat_offset = session.scalar(select(func.count()).select_from(TelegramLink)) or 0
    session.add(
        TelegramLink(
            user_id=user.id,
            telegram_chat_id=9_989_000_000 + chat_offset + 1,
            linked_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    return user


def attach_shop_state(session: Session, user: User, state: str) -> None:
    if state == "no_shop":
        return
    shop = Shop(
        name=f"OTP {state} shop",
        phone=f"+99890{len(state):07d}",
        status=ShopStatus.SUSPENDED.value
        if state == "suspended_shop"
        else ShopStatus.ACTIVE.value,
    )
    session.add(shop)
    session.flush()
    session.add(
        ShopStaff(
            shop_id=shop.id,
            user_id=user.id,
            role={
                "active_owner": ShopRole.OWNER.value,
                "suspended_shop": ShopRole.MANAGER.value,
                "revoked_staff": ShopRole.CASHIER.value,
            }[state],
            is_active=state != "revoked_staff",
            revoked_at=None if state != "revoked_staff" else NOW,
        )
    )
    session.flush()


def fetch_session_by_cookie(session: Session, raw_cookie: str) -> AuthSession:
    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(raw_cookie)
        )
    )
    assert auth_session is not None
    return auth_session


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def outstanding_challenges(session: Session) -> list[OtpChallenge]:
    return list(
        session.scalars(
            select(OtpChallenge).where(
                OtpChallenge.status.in_(
                    [
                        OtpChallengeStatus.PENDING_DISPATCH.value,
                        OtpChallengeStatus.ACTIVE.value,
                    ]
                )
            )
        ).all()
    )


def run_fake_dispatch(
    session: Session,
    settings: Settings,
    *,
    now: datetime,
    code_value: int,
) -> PreparedOtpDispatch:
    prepared = prepare_next_otp_dispatch(
        session,
        otp_hmac_key=settings.require_otp_hmac_key(),
        now=now,
        ttl_seconds=settings.otp_login_ttl_seconds,
        claim_stale_seconds=settings.otp_dispatch_claim_stale_seconds,
        code_generator=lambda _upper: code_value,
    )
    assert prepared is not None
    session.commit()
    assert record_otp_delivery_result(
        session,
        dispatch_id=prepared.dispatch_id,
        result=OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT),
        now=now + timedelta(seconds=1),
    )
    session.commit()
    return prepared


def test_public_otp_route_surface_has_no_identifier_or_status_routes(
    m2_test_database: Engine,
) -> None:
    application = create_app(settings=make_settings(m2_test_database))
    client = TestClient(application, client=("203.0.113.120", 50_000))
    route_methods: dict[str, set[str]] = defaultdict(set)
    for route in auth_router_module.router.routes:
        path = getattr(route, "path_format", getattr(route, "path", ""))
        if not path.startswith("/auth/otp"):
            continue
        route_methods[path].update(
            getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        )
        assert "{" not in path
        assert "challenge" not in path
        assert "dispatch" not in path
        assert "status" not in path

    assert dict(route_methods) == {
        "/auth/otp": {"GET"},
        "/auth/otp/request": {"POST"},
        "/auth/otp/verify": {"GET", "POST"},
        "/auth/otp/new-code": {"POST"},
    }
    for path in (
        f"/auth/otp/{uuid4()}",
        f"/auth/otp/verify/{uuid4()}",
        "/auth/otp/status",
        "/auth/otp/dispatch/status",
    ):
        assert client.get(path).status_code == 404
        assert client.post(path, data={}).status_code == 404


def test_verify_and_new_code_ignore_client_supplied_target_identifiers(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, _settings = make_client(m2_test_database)
    csrf = get_verify_csrf(client)
    injected = {
        "challenge_id": str(uuid4()),
        "dispatch_id": str(uuid4()),
        "user_id": str(uuid4()),
        "phone": "+998900009777",
    }

    verify_response = post_verify(
        client,
        csrf_token=csrf,
        code="000000",
        extra_data=injected,
    )
    new_code_response = post_new_code(
        client,
        csrf_token=csrf,
        extra_data=injected,
    )

    assert verify_response.status_code == 303
    assert verify_response.headers["location"] == "/auth/otp/verify?error=invalid"
    assert new_code_response.status_code == 303
    assert new_code_response.headers["location"] == "/auth/otp/verify"
    rendered_public_result = (
        f"{verify_response.headers['location']} {new_code_response.headers['location']}"
    )
    for value in injected.values():
        assert value not in rendered_public_result
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0


def test_rapid_duplicate_issue_keeps_browser_session_usable_for_latest_code(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    user = add_user_with_link(db_session, phone="+998900009778")
    db_session.commit()
    csrf = get_request_csrf(client)
    first = post_request(client, csrf_token=csrf, phone="+998900009778")
    second = post_request(client, csrf_token=csrf, phone="+998900009778")
    assert first.status_code == 303
    assert second.status_code == 303
    db_session.expire_all()
    outstanding = outstanding_challenges(db_session)
    assert len(outstanding) == 1
    assert outstanding[0].status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert count_table(db_session, OtpChallenge) == 2
    run_fake_dispatch(
        db_session,
        settings,
        now=NOW + timedelta(seconds=2),
        code_value=778899,
    )
    now_state["now"] = NOW + timedelta(seconds=4)
    verify_csrf = get_verify_csrf(client)

    success = post_verify(client, csrf_token=verify_csrf, code="778899")

    assert success.status_code == 303
    assert success.headers["location"] == "/auth/account"
    db_session.expire_all()
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    auth_session = fetch_session_by_cookie(db_session, raw_cookie)
    assert auth_session.user_id == user.id
    assert auth_session.active_shop_id is None


def test_otp_success_login_ignores_account_shop_role_state_matrix(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    for index, shop_state in enumerate(
        ("active_owner", "suspended_shop", "revoked_staff", "no_shop"),
        start=1,
    ):
        now_state, now_provider = mutable_now()
        client, settings = make_client(m2_test_database, now_provider)
        phone = f"+99890000978{index}"
        user = add_user_with_link(db_session, phone=phone)
        attach_shop_state(db_session, user, shop_state)
        db_session.commit()
        request_csrf = get_request_csrf(client)
        request_response = post_request(client, csrf_token=request_csrf, phone=phone)
        assert request_response.status_code == 303
        run_fake_dispatch(
            db_session,
            settings,
            now=NOW + timedelta(seconds=2),
            code_value=880000 + index,
        )
        now_state["now"] = NOW + timedelta(seconds=4)
        verify_csrf = get_verify_csrf(client)

        success = post_verify(
            client,
            csrf_token=verify_csrf,
            code=str(880000 + index),
        )

        assert success.status_code == 303
        assert success.headers["location"] == "/auth/account"
        account = client.get("/auth/account")
        assert account.status_code == 200
        db_session.expire_all()
        raw_cookie = client.cookies.get(settings.session_cookie_name)
        assert raw_cookie is not None
        auth_session = fetch_session_by_cookie(db_session, raw_cookie)
        assert auth_session.user_id == user.id
        assert auth_session.active_shop_id is None


def test_otp_routes_and_package_do_not_import_shop_resolvers_or_generic_queues() -> (
    None
):
    route_functions = (
        auth_router_module.otp_request_page,
        auth_router_module.request_login_otp_route,
        auth_router_module.otp_verify_page,
        auth_router_module.request_new_login_otp_route,
        auth_router_module.verify_login_otp_route,
    )
    for route_function in route_functions:
        source = inspect_source(route_function)
        for forbidden in (
            "require_shop",
            "resolve_current_shop",
            "current_shop",
            "shopstaff",
            "app.shop",
            "outbox",
            "notification",
            "celery",
            "redis",
        ):
            assert forbidden not in source

    imported_modules = {
        module
        for path in [PROJECT_ROOT / "app" / "auth" / "router.py"]
        + sorted((PROJECT_ROOT / "app" / "otp").glob("*.py"))
        for module in parse_imported_modules(path)
    }
    for module in imported_modules:
        module_parts = set(module.casefold().split("."))
        assert "outbox" not in module_parts
        assert "notification" not in module_parts
        assert "notifications" not in module_parts
        assert "job" not in module_parts
        assert "jobs" not in module_parts
        assert "celery" not in module_parts
        assert "redis" not in module_parts
        assert "dramatiq" not in module_parts


def inspect_source(obj) -> str:
    import inspect

    return inspect.getsource(obj).casefold()


def parse_imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules
