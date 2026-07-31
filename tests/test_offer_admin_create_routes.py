import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.enums import OfferPurpose, OfferStatus
from app.offers.models import OfferVersion
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 5, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-offer-admin-create"


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def _client(engine: Engine) -> tuple[TestClient, Settings]:
    settings = _settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    return TestClient(application), settings


def _authenticate(
    client: TestClient,
    settings: Settings,
    session: Session,
    *,
    phone: str,
    is_platform_admin: bool,
) -> None:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=is_platform_admin,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    created = create_authenticated_session(
        session,
        user.id,
        "pytest-offer-admin-create",
        NOW,
        settings=settings,
    )
    session.commit()
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _count_versions(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(OfferVersion)) or 0


def test_admin_create_draft_uses_csrf_service_and_prg_with_server_version(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        _authenticate(
            client,
            settings,
            session,
            phone="+998900000982",
            is_platform_admin=True,
        )
    form = client.get("/admin/offers/new")
    csrf_token = _csrf_from(form.text)

    response = client.post(
        "/admin/offers",
        data={
            "csrf_token": csrf_token,
            "purpose": OfferPurpose.REGISTRATION.value,
            "version_number": "999",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert re.fullmatch(
        r"/admin/offers/[0-9a-f-]+\?notice=draft-created",
        response.headers["location"],
    )
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "Ro‘yxatdan o‘tish · v1" in detail.text
    assert "Yangi offer qoralamasi yaratildi." in detail.text
    with Session(m2_test_database) as session:
        version = session.scalar(select(OfferVersion))
        assert version is not None
        assert version.purpose == OfferPurpose.REGISTRATION.value
        assert version.version_number == 1
        assert version.status == OfferStatus.DRAFT.value


def test_create_draft_rejects_missing_csrf_without_mutation(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        _authenticate(
            client,
            settings,
            session,
            phone="+998900000983",
            is_platform_admin=True,
        )

    response = client.post(
        "/admin/offers",
        data={"purpose": OfferPurpose.REGISTRATION.value},
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert _count_versions(m2_test_database) == 0


def test_non_admin_cannot_open_or_submit_create_draft(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        _authenticate(
            client,
            settings,
            session,
            phone="+998900000984",
            is_platform_admin=False,
        )

    page = client.get("/admin/offers/new")
    submitted = client.post(
        "/admin/offers",
        data={
            "csrf_token": "attacker",
            "purpose": OfferPurpose.REGISTRATION.value,
        },
    )

    assert page.status_code == 403
    assert submitted.status_code == 403
    assert _count_versions(m2_test_database) == 0


def test_invalid_purpose_prg_uses_safe_localized_error(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        _authenticate(
            client,
            settings,
            session,
            phone="+998900000985",
            is_platform_admin=True,
        )
    form = client.get(
        "/admin/offers/new",
        headers={"Accept-Language": "ru"},
    )

    response = client.post(
        "/admin/offers",
        data={
            "csrf_token": _csrf_from(form.text),
            "purpose": "GENERIC_CMS",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == ("/admin/offers/new?error=validation-error")
    error_page = client.get(
        response.headers["location"],
        headers={"Accept-Language": "ru"},
    )
    assert "Проверьте введённое значение." in error_page.text
    assert "GENERIC_CMS" not in error_page.text
    assert _count_versions(m2_test_database) == 0


@pytest.mark.parametrize(
    ("accept_language", "expected_shell"),
    [
        (
            "uz-Latn",
            (
                "Yangi offer qoralamasi",
                "Offer navigatsiyasi",
                "← Offer versiyalariga qaytish",
                "Versiya raqami server tomonidan avtomatik belgilanadi.",
                "Offer maqsadi",
                "Ro‘yxatdan o‘tish",
                "Qarz qabul qilish",
                "Qoralama yaratish",
            ),
        ),
        (
            "ru",
            (
                "Новый черновик оферты",
                "Навигация оферт",
                "← Вернуться к версиям оферты",
                "Номер версии назначается сервером автоматически.",
                "Назначение оферты",
                "Регистрация",
                "Принятие долга",
                "Создать черновик",
            ),
        ),
    ],
)
def test_admin_create_page_renders_full_localized_shell(
    m2_test_database: Engine,
    accept_language: str,
    expected_shell: tuple[str, ...],
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        _authenticate(
            client,
            settings,
            session,
            phone="+998900000986",
            is_platform_admin=True,
        )

    response = client.get(
        "/admin/offers/new",
        headers={"Accept-Language": accept_language},
    )

    assert response.status_code == 200
    assert f'<html lang="{"ru" if accept_language == "ru" else "uz"}">' in (
        response.text
    )
    for expected in expected_shell:
        assert expected in response.text
