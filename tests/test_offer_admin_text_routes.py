from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferText
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    upsert_offer_draft_text,
)
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 6, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-offer-admin-text"


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


def _seed(
    engine: Engine,
    client: TestClient,
    settings: Settings,
    *,
    approve: bool,
) -> tuple[UUID, str]:
    with Session(engine) as session, session.begin():
        admin = User(
            phone="+998900000986",
            password_hash=None,
            is_active=True,
            is_platform_admin=True,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(admin)
        session.flush()
        actor = require_platform_admin_actor(admin)
        draft = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        languages = tuple(OfferLanguage) if approve else (OfferLanguage.UZ_LATN,)
        for language in languages:
            saved = upsert_offer_draft_text(
                session,
                actor=actor,
                offer_version_id=draft.id,
                language=language,
                title=f"<{language.value} title>",
                body=f"<script>{language.value} body</script>",
                now=NOW,
            )
            assert saved.succeeded
        if approve:
            approved = approve_offer_version(
                session,
                actor=actor,
                offer_version_id=draft.id,
                legal_review_authority="External Legal",
                legal_reviewed_at=NOW - timedelta(hours=1),
                legal_review_reference="LEGAL-2026-986",
                now=NOW,
            )
            assert approved.succeeded
        created_session = create_authenticated_session(
            session,
            admin.id,
            "pytest-offer-admin-text",
            NOW,
            settings=settings,
        )
        csrf_token = get_csrf_token(created_session.session).as_form_value()
        raw_cookie = created_session.raw_token.as_cookie_value()
        draft_id = draft.id
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )
    return draft_id, csrf_token


def test_draft_detail_has_three_labeled_forms_and_upsert_uses_prg(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    draft_id, csrf_token = _seed(
        m2_test_database,
        client,
        settings,
        approve=False,
    )

    detail = client.get(f"/admin/offers/{draft_id}")

    assert detail.status_code == 200
    for language in OfferLanguage:
        assert (
            f'action="/admin/offers/{draft_id}/texts/{language.value}"' in detail.text
        )
        assert f'id="offer-title-{language.value}"' in detail.text
        assert f'id="offer-body-{language.value}"' in detail.text
    assert "&lt;UZ_LATN title&gt;" in detail.text
    assert "<script>UZ_LATN body</script>" not in detail.text

    updated = client.post(
        f"/admin/offers/{draft_id}/texts/{OfferLanguage.RU.value}",
        data={
            "csrf_token": csrf_token,
            "title": "RU exact title\r\nsecond line",
            "body": "RU exact body\rsecond line",
        },
        follow_redirects=False,
    )

    assert updated.status_code == 303
    assert updated.headers["location"] == (
        f"/admin/offers/{draft_id}?notice=text-updated"
    )
    page = client.get(updated.headers["location"])
    assert "Legal matn saqlandi." in page.text
    with Session(m2_test_database) as session:
        persisted = session.scalar(
            select(OfferText).where(
                OfferText.offer_version_id == draft_id,
                OfferText.language == OfferLanguage.RU.value,
            )
        )
        assert persisted is not None
        assert persisted.title == "RU exact title\nsecond line"
        assert persisted.body == "RU exact body\nsecond line"


def test_approved_detail_hides_edit_forms_and_post_is_server_denied(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    approved_id, csrf_token = _seed(
        m2_test_database,
        client,
        settings,
        approve=True,
    )

    detail = client.get(f"/admin/offers/{approved_id}")
    denied = client.post(
        f"/admin/offers/{approved_id}/texts/{OfferLanguage.RU.value}",
        data={
            "csrf_token": csrf_token,
            "title": "MUTATED TITLE",
            "body": "MUTATED BODY",
        },
        follow_redirects=False,
    )

    assert detail.status_code == 200
    assert f"/admin/offers/{approved_id}/texts/" not in detail.text
    assert denied.status_code == 303
    assert denied.headers["x-error-code"] == ErrorCode.OFFER_NOT_DRAFT.value
    assert denied.headers["location"] == (
        f"/admin/offers/{approved_id}?error=offer-not-draft"
    )
    error_page = client.get(denied.headers["location"])
    assert "Faqat qoralama offerni tahrirlash mumkin." in error_page.text
    assert "MUTATED TITLE" not in error_page.text
    assert "MUTATED BODY" not in error_page.text
    with Session(m2_test_database) as session:
        persisted = session.scalar(
            select(OfferText).where(
                OfferText.offer_version_id == approved_id,
                OfferText.language == OfferLanguage.RU.value,
            )
        )
        assert persisted is not None
        assert persisted.title == "<RU title>"
        assert persisted.body == "<script>RU body</script>"


def test_text_upsert_missing_csrf_is_zero_write(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    draft_id, _csrf_token = _seed(
        m2_test_database,
        client,
        settings,
        approve=False,
    )

    denied = client.post(
        f"/admin/offers/{draft_id}/texts/{OfferLanguage.UZ_LATN.value}",
        data={"title": "MUTATED", "body": "MUTATED"},
    )

    assert denied.status_code == 403
    assert denied.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    with Session(m2_test_database) as session:
        persisted = session.scalar(
            select(OfferText).where(
                OfferText.offer_version_id == draft_id,
                OfferText.language == OfferLanguage.UZ_LATN.value,
            )
        )
        assert persisted is not None
        assert persisted.title == "<UZ_LATN title>"
