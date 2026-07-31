from datetime import UTC, datetime, timedelta
from html import escape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
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
from app.security_headers import CONTENT_SECURITY_POLICY
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-offer-xss-rendering"


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def _client(
    application,
    settings: Settings,
    raw_cookie: str,
) -> TestClient:
    client = TestClient(application)
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )
    return client


def _seed(
    engine: Engine,
    settings: Settings,
    *,
    payload: str,
) -> tuple[object, str, str]:
    with Session(engine) as session, session.begin():
        admin = User(
            phone="+998900001010",
            password_hash=None,
            is_active=True,
            is_platform_admin=True,
            created_at=NOW,
            updated_at=NOW,
        )
        account = User(
            phone="+998900001011",
            password_hash=None,
            is_active=True,
            is_platform_admin=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add_all((admin, account))
        session.flush()
        actor = require_platform_admin_actor(admin)
        draft = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        for language in OfferLanguage:
            assert upsert_offer_draft_text(
                session,
                actor=actor,
                offer_version_id=draft.id,
                language=language,
                title=f"{payload} title",
                body=f"{payload} body",
                now=NOW,
            ).succeeded
        approved = approve_offer_version(
            session,
            actor=actor,
            offer_version_id=draft.id,
            legal_review_authority="External Legal",
            legal_reviewed_at=NOW - timedelta(hours=1),
            legal_review_reference="LEGAL-2026-1010",
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
        admin_session = create_authenticated_session(
            session,
            admin.id,
            "pytest-offer-xss-admin",
            NOW,
            settings=settings,
        )
        account_session = create_authenticated_session(
            session,
            account.id,
            "pytest-offer-xss-account",
            NOW,
            settings=settings,
        )
        offer_id = approved.version.id
        admin_cookie = admin_session.raw_token.as_cookie_value()
        account_cookie = account_session.raw_token.as_cookie_value()
    return offer_id, admin_cookie, account_cookie


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(2)>",
        "</textarea><svg/onload=alert(3)>",
        "\u202e<math href=x>\u2066Z",
    ],
    ids=("script", "event-handler", "malformed-html", "unicode-edge"),
)
def test_legal_content_is_literal_escaped_text_on_admin_and_account_pages(
    m2_test_database: Engine,
    payload: str,
) -> None:
    settings = _settings(m2_test_database)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    offer_id, admin_cookie, account_cookie = _seed(
        m2_test_database,
        settings,
        payload=payload,
    )
    admin_client = _client(application, settings, admin_cookie)
    account_client = _client(application, settings, account_cookie)

    listing = admin_client.get("/admin/offers")
    detail = admin_client.get(f"/admin/offers/{offer_id}")
    account_page = account_client.get(
        f"/auth/registration-offer?language={OfferLanguage.UZ_LATN.value}"
    )

    assert listing.status_code == 200
    assert payload not in listing.text
    assert escape(payload) not in listing.text
    for response in (detail, account_page):
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["cache-control"] == "no-store"
        assert payload not in response.text
        assert f"{escape(payload)} title" in response.text
        assert f"{escape(payload)} body" in response.text
        normalized = response.text.casefold()
        for executable_prefix in (
            "<script",
            "<img",
            "<svg",
            "<math",
        ):
            assert executable_prefix not in normalized
