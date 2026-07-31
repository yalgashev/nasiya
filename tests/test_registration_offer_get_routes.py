from datetime import UTC, datetime, timedelta

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
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    upsert_offer_draft_text,
)
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-registration-offer-get"


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


def _authenticate_account(
    engine: Engine,
    client: TestClient,
    settings: Settings,
    *,
    seed_current: bool,
) -> None:
    with Session(engine) as session, session.begin():
        admin = User(
            phone="+998900000991",
            password_hash=None,
            is_active=True,
            is_platform_admin=True,
            created_at=NOW,
            updated_at=NOW,
        )
        account = User(
            phone="+998900000992",
            password_hash=None,
            is_active=True,
            is_platform_admin=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add_all((admin, account))
        session.flush()
        if seed_current:
            actor = require_platform_admin_actor(admin)
            draft = create_offer_draft_version(
                session,
                actor=actor,
                purpose=OfferPurpose.REGISTRATION,
                now=NOW,
            )
            for language in OfferLanguage:
                saved = upsert_offer_draft_text(
                    session,
                    actor=actor,
                    offer_version_id=draft.id,
                    language=language,
                    title=f"<{language.value} exact title>",
                    body=f"<script>{language.value} exact body</script>",
                    now=NOW,
                )
                assert saved.succeeded
            approved = approve_offer_version(
                session,
                actor=actor,
                offer_version_id=draft.id,
                legal_review_authority="External Legal",
                legal_reviewed_at=NOW - timedelta(hours=1),
                legal_review_reference="LEGAL-2026-991",
                now=NOW,
            )
            assert approved.version is not None
            assert make_offer_version_current(
                session,
                actor=actor,
                offer_version_id=approved.version.id,
                expected_current_version_id=None,
                now=NOW,
            ).succeeded
        created = create_authenticated_session(
            session,
            account.id,
            "pytest-registration-offer-get",
            NOW,
            settings=settings,
        )
        raw_cookie = created.raw_token.as_cookie_value()
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )


def test_authenticated_default_is_uz_latn_and_legal_text_is_autoescaped(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    _authenticate_account(
        m2_test_database,
        client,
        settings,
        seed_current=True,
    )

    response = client.get(
        "/auth/registration-offer",
        headers={"Accept-Language": "ru"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert 'lang="uz-Latn"' in response.text
    assert "&lt;UZ_LATN exact title&gt;" in response.text
    assert "&lt;script&gt;UZ_LATN exact body&lt;/script&gt;" in response.text
    assert "<script>UZ_LATN exact body</script>" not in response.text
    assert "RU exact body" not in response.text
    for language in OfferLanguage:
        assert (
            f'href="/auth/registration-offer?language={language.value}"'
            in response.text
        )


@pytest.mark.parametrize(
    ("language", "language_tag"),
    [
        (OfferLanguage.UZ_LATN, "uz-Latn"),
        (OfferLanguage.UZ_CYRL, "uz-Cyrl"),
        (OfferLanguage.RU, "ru"),
    ],
)
def test_explicit_legal_language_is_independent_of_ui_locale(
    m2_test_database: Engine,
    language: OfferLanguage,
    language_tag: str,
) -> None:
    client, settings = _client(m2_test_database)
    _authenticate_account(
        m2_test_database,
        client,
        settings,
        seed_current=True,
    )

    response = client.get(
        f"/auth/registration-offer?language={language.value}",
        headers={"Accept-Language": "ru" if language is not OfferLanguage.RU else "uz"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert f'lang="{language_tag}"' in response.text
    assert f"&lt;{language.value} exact title&gt;" in response.text
    assert f"{language.value} exact body" in response.text


@pytest.mark.parametrize(
    ("accept_language", "page_language", "expected_shell"),
    [
        (
            "uz",
            "uz",
            (
                "Ro‘yxatdan o‘tish ofertasi",
                "Legal matn tili",
                "O‘zbekcha (lotin)",
                "O‘zbekcha (kirill)",
                "Ruscha",
                "Ro‘yxatdan o‘tish ofertasini qabul qilish",
            ),
        ),
        (
            "ru",
            "ru",
            (
                "Регистрационная оферта",
                "Язык юридического текста",
                "Узбекский (латиница)",
                "Узбекский (кириллица)",
                "Русский",
                "Принять регистрационную оферту",
            ),
        ),
    ],
)
def test_registration_offer_renders_full_localized_shell(
    m2_test_database: Engine,
    accept_language: str,
    page_language: str,
    expected_shell: tuple[str, ...],
) -> None:
    client, settings = _client(m2_test_database)
    _authenticate_account(
        m2_test_database,
        client,
        settings,
        seed_current=True,
    )

    response = client.get(
        "/auth/registration-offer",
        headers={"Accept-Language": accept_language},
    )

    assert response.status_code == 200
    assert f'<html lang="{page_language}">' in response.text
    assert 'lang="uz-Latn"' in response.text
    for expected in expected_shell:
        assert expected in response.text


def test_no_current_and_invalid_language_fail_closed(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    _authenticate_account(
        m2_test_database,
        client,
        settings,
        seed_current=False,
    )

    unavailable = client.get("/auth/registration-offer")
    invalid = client.get("/auth/registration-offer?language=HTML")

    assert unavailable.status_code == 409
    assert unavailable.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert unavailable.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert unavailable.headers["x-error-code"] == ErrorCode.OFFER_UNAVAILABLE.value
    assert "Joriy registration offer hozir mavjud emas." in unavailable.text
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert invalid.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert invalid.headers["x-error-code"] == ErrorCode.VALIDATION_ERROR.value
    assert "Kiritilgan qiymatni tekshiring." in invalid.text
    assert "HTML" not in invalid.text


def test_anonymous_registration_offer_redirects_to_login(
    m2_test_database: Engine,
) -> None:
    client, _settings_value = _client(m2_test_database)

    response = client.get(
        "/auth/registration-offer",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
