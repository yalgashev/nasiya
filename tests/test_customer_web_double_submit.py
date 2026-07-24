from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Barrier, BrokenBarrierError, Lock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer.router as customer_router
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-customer-double-submit"
TEST_PASSWORD = "Password123"
CONCURRENCY_BARRIER_TIMEOUT_SECONDS = 10
CONCURRENCY_RESULT_TIMEOUT_SECONDS = 20
POSTGRES_STATEMENT_TIMEOUT_MILLISECONDS = 10_000
RAW_DATABASE_ERROR_MARKERS = (
    "IntegrityError",
    "UniqueViolation",
    "ForeignKeyViolation",
    "duplicate key",
    "violates unique constraint",
    "uq_customers_user_id",
    "ck_customers_onboarding_status_draft_only",
    "sqlalchemy",
    "psycopg",
    "Traceback",
)


@dataclass(frozen=True, slots=True)
class PostStartResult:
    status_code: int
    location: str | None
    headers: dict[str, str]
    text: str


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_client(engine: Engine, now: datetime) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application, raise_server_exceptions=False), settings


def make_clients(
    engine: Engine,
    now: datetime,
) -> tuple[TestClient, TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return (
        TestClient(application, raise_server_exceptions=False),
        TestClient(application, raise_server_exceptions=False),
        settings,
    )


def set_client_session_cookie(
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


def commit_user(db_session: Session, phone: str) -> User:
    result = create_user(db_session, phone, TEST_PASSWORD)
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-customer-web-double-submit",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def csrf_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def count_customers(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(Customer)) or 0


def get_customer_by_user_id(db_session: Session, user_id: UUID) -> Customer | None:
    return db_session.scalar(select(Customer).where(Customer.user_id == user_id))


def post_start(client: TestClient, csrf_token: str) -> PostStartResult:
    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    return PostStartResult(
        status_code=response.status_code,
        location=response.headers.get("location"),
        headers=dict(response.headers),
        text=response.text,
    )


def assert_customer_security_headers(result: PostStartResult) -> None:
    assert result.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert result.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert result.headers["x-frame-options"] == "DENY"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_safe_redirect_result(result: PostStartResult) -> None:
    assert result.status_code == 303
    assert result.location == "/customer/profile"
    assert_customer_security_headers(result)
    assert_no_raw_database_error_leak(result.text)


def assert_no_raw_database_error_leak(*texts: str) -> None:
    combined_text = "\n".join(texts)
    for marker in RAW_DATABASE_ERROR_MARKERS:
        assert marker.casefold() not in combined_text.casefold()


def assert_logs_have_no_raw_database_error(caplog: pytest.LogCaptureFixture) -> None:
    log_output = "\n".join(
        f"{record.levelname} {record.name} {record.getMessage()}"
        for record in caplog.records
    )
    assert_no_raw_database_error_leak(log_output)


def _wait_at_barrier(barrier: Barrier, message: str) -> None:
    try:
        barrier.wait()
    except BrokenBarrierError as exc:
        raise AssertionError(message) from exc


@pytest.mark.integration
def test_customer_start_one_client_sequential_double_submit_is_idempotent(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 24, 9, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998900000801")
    created = commit_authenticated_session(db_session, user, now, settings)
    csrf_token = csrf_value(created)
    set_client_session_cookie(client, settings, created)

    first_result = post_start(client, csrf_token)
    second_result = post_start(client, csrf_token)
    profile_response = client.get("/customer/profile", follow_redirects=False)

    assert_safe_redirect_result(first_result)
    assert_safe_redirect_result(second_result)
    assert profile_response.status_code == 200
    assert count_customers(db_session) == 1

    customer = get_customer_by_user_id(db_session, user.id)
    assert customer is not None
    assert customer.user_id == user.id
    assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    assert str(customer.id) not in first_result.text
    assert str(customer.id) not in second_result.text
    assert_no_raw_database_error_leak(profile_response.text)
    assert_logs_have_no_raw_database_error(caplog)


@pytest.mark.integration
def test_customer_start_parallel_same_user_sessions_are_web_idempotent(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 24, 9, 35, tzinfo=UTC)
    first_client, second_client, settings = make_clients(m2_test_database, now)
    user = commit_user(db_session, "+998900000802")
    first_session = commit_authenticated_session(db_session, user, now, settings)
    second_session = commit_authenticated_session(db_session, user, now, settings)
    assert first_session.session.id != second_session.session.id
    assert first_session.session.user_id == user.id
    assert second_session.session.user_id == user.id
    set_client_session_cookie(first_client, settings, first_session)
    set_client_session_cookie(second_client, settings, second_session)

    start_barrier = Barrier(2, timeout=CONCURRENCY_BARRIER_TIMEOUT_SECONDS)
    continuation_checks: list[UUID] = []
    continuation_checks_lock = Lock()
    original_start_customer_draft = customer_router.start_customer_draft

    def synchronized_start_customer_draft(session: Session, user_id: UUID) -> Customer:
        session.execute(
            text(
                "SET LOCAL statement_timeout = "
                f"{POSTGRES_STATEMENT_TIMEOUT_MILLISECONDS}",
            ),
        )
        _wait_at_barrier(
            start_barrier,
            "both web customer start requests did not reach the barrier",
        )

        customer = original_start_customer_draft(session, user_id)
        assert session.scalar(select(User.id).where(User.id == user_id)) == user_id

        with continuation_checks_lock:
            continuation_checks.append(user_id)
        return customer

    monkeypatch.setattr(
        customer_router,
        "start_customer_draft",
        synchronized_start_customer_draft,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(post_start, first_client, csrf_value(first_session)),
            executor.submit(post_start, second_client, csrf_value(second_session)),
        ]
        results = [
            future.result(timeout=CONCURRENCY_RESULT_TIMEOUT_SECONDS)
            for future in futures
        ]

    first_profile = first_client.get("/customer/profile", follow_redirects=False)
    second_profile = second_client.get("/customer/profile", follow_redirects=False)

    for result in results:
        assert_safe_redirect_result(result)

    assert {result.status_code for result in results} == {303}
    assert {result.location for result in results} == {"/customer/profile"}
    assert count_customers(db_session) == 1
    assert len(continuation_checks) == 2
    assert set(continuation_checks) == {user.id}
    assert first_profile.status_code == 200
    assert second_profile.status_code == 200

    customer = get_customer_by_user_id(db_session, user.id)
    assert customer is not None
    assert customer.user_id == user.id
    assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    for result in results:
        assert str(customer.id) not in result.text
    assert_no_raw_database_error_leak(first_profile.text, second_profile.text)
    assert_logs_have_no_raw_database_error(caplog)
