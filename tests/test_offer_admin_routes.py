from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.service import (
    create_offer_draft_version,
    upsert_offer_draft_text,
)
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)
TITLE_CANARY = "<Admin exact title>"
BODY_CANARY = "<script>Admin exact legal body</script>"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-offer-admin-routes"


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


def _user(
    session: Session,
    *,
    phone: str,
    is_platform_admin: bool,
) -> User:
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
    return user


def _authenticate(
    client: TestClient,
    settings: Settings,
    session: Session,
    *,
    user: User,
) -> None:
    created = create_authenticated_session(
        session,
        user.id,
        "pytest-offer-admin-route",
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


def _seed_admin_offer(session: Session) -> tuple[User, object]:
    admin = _user(
        session,
        phone="+998900000979",
        is_platform_admin=True,
    )
    actor = require_platform_admin_actor(admin)
    draft = create_offer_draft_version(
        session,
        actor=actor,
        purpose=OfferPurpose.REGISTRATION,
        now=NOW,
    )
    saved = upsert_offer_draft_text(
        session,
        actor=actor,
        offer_version_id=draft.id,
        language=OfferLanguage.UZ_LATN,
        title=TITLE_CANARY,
        body=BODY_CANARY,
        now=NOW,
    )
    assert saved.succeeded
    session.commit()
    return admin, draft


def test_anonymous_admin_offer_list_redirects_to_login(
    m2_test_database: Engine,
) -> None:
    client, _settings_value = _client(m2_test_database)

    response = client.get("/admin/offers", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_non_admin_cannot_read_offer_list_or_detail(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        _admin, draft = _seed_admin_offer(session)
        account = _user(
            session,
            phone="+998900000980",
            is_platform_admin=False,
        )
        _authenticate(client, settings, session, user=account)

    for path in ("/admin/offers", f"/admin/offers/{draft.id}"):
        response = client.get(path)
        assert response.status_code == 403
        assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
        assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert TITLE_CANARY not in response.text
        assert BODY_CANARY not in response.text


def test_platform_admin_reads_metadata_list_and_authorized_escaped_detail(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        admin, draft = _seed_admin_offer(session)
        _authenticate(client, settings, session, user=admin)

    listing = client.get("/admin/offers")
    detail = client.get(f"/admin/offers/{draft.id}")

    assert listing.status_code == 200
    assert listing.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert listing.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert "Ro‘yxatdan o‘tish · v1" in listing.text
    assert TITLE_CANARY not in listing.text
    assert BODY_CANARY not in listing.text
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert detail.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert "&lt;Admin exact title&gt;" in detail.text
    assert "&lt;script&gt;Admin exact legal body&lt;/script&gt;" in detail.text
    assert TITLE_CANARY not in detail.text
    assert BODY_CANARY not in detail.text
    assert "<script>Admin exact legal body</script>" not in detail.text


@pytest.mark.parametrize(
    ("accept_language", "list_shell", "detail_shell"),
    [
        (
            "uz-Latn",
            (
                "Offer versiyalari",
                "Platform darajasidagi legal offer versiyalari.",
                "Yangi qoralama yaratish",
                "Versiyalar",
                "Ro‘yxatdan o‘tish · v1",
                "Holat",
                "Qoralama",
                "Legal tillar",
                "O‘zbekcha (lotin)",
                "To‘liqlik",
                "To‘liq emas",
            ),
            (
                "Offer navigatsiyasi",
                "← Offer versiyalariga qaytish",
                "Legal matnlar",
                "Sarlavha",
                "Legal matn",
                "Matnni saqlash",
                "Offerni tasdiqlash",
                "Yetishmayotgan tillar",
                "Tekshiruvchi / vakolatli tomon",
                "Ko‘rib chiqilgan vaqt (UTC)",
                "Tekshiruv identifikatori",
            ),
        ),
        (
            "ru",
            (
                "Версии оферты",
                "Версии юридической оферты уровня платформы.",
                "Создать новый черновик",
                "Версии",
                "Регистрация · v1",
                "Статус",
                "Черновик",
                "Языки юридического текста",
                "Узбекский (латиница)",
                "Полнота",
                "Неполная",
            ),
            (
                "Навигация оферт",
                "← Вернуться к версиям оферты",
                "Юридические тексты",
                "Заголовок",
                "Юридический текст",
                "Сохранить текст",
                "Утверждение оферты",
                "Отсутствующие языки",
                "Проверяющий / организация",
                "Время проверки (UTC)",
                "Ссылка на проверку",
                "Утвердить оферту",
            ),
        ),
    ],
)
def test_admin_list_and_draft_detail_render_full_localized_shell(
    m2_test_database: Engine,
    accept_language: str,
    list_shell: tuple[str, ...],
    detail_shell: tuple[str, ...],
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        admin, draft = _seed_admin_offer(session)
        _authenticate(client, settings, session, user=admin)

    headers = {"Accept-Language": accept_language}
    listing = client.get("/admin/offers", headers=headers)
    detail = client.get(f"/admin/offers/{draft.id}", headers=headers)

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert f'<html lang="{"ru" if accept_language == "ru" else "uz"}">' in (
        listing.text
    )
    assert 'href="/admin/offers/new"' in listing.text
    for expected in list_shell:
        assert expected in listing.text
    for expected in detail_shell:
        assert expected in detail.text


def test_platform_admin_unknown_offer_detail_is_not_found(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        admin = _user(
            session,
            phone="+998900000981",
            is_platform_admin=True,
        )
        unknown_id = admin.id
        _authenticate(client, settings, session, user=admin)

    response = client.get(f"/admin/offers/{unknown_id}")

    assert response.status_code == 404
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
