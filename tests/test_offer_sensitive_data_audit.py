import logging
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.service as offer_service
from app.audit.contracts import AuditEvent
from app.audit.models import AuditLog
from app.audit.repository import append_audit_event
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferAcceptance, OfferText
from app.offers.read_models import get_offer_version_detail_for_admin
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    resolve_current_offer,
    upsert_offer_draft_text,
)
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-offer-sensitive-data"
CANARIES = {
    "title": "M9_SECRET_LEGAL_TITLE_CANARY",
    "body": "M9_SECRET_LEGAL_BODY_CANARY",
    "user_agent": "M9_RAW_UA_CANARY   /1.0",
    "phone": "+998901234598",
    "token": "M9_TOKEN_CANARY",
    "url": "https://m9-secret.invalid/legal-document",
    "secret": "M9_INTERNAL_SECRET_CANARY",
    "session_id": "M9_SESSION_ID_CANARY",
}


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def _client(application, settings: Settings, raw_cookie: str) -> TestClient:
    client = TestClient(application)
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )
    return client


def _hidden(html: str, field_name: str) -> str:
    match = re.search(rf'name="{field_name}" value="([^"]*)"', html)
    assert match is not None
    return match.group(1)


def test_offer_logs_errors_audit_and_repr_omit_sensitive_canaries(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    contaminated = dict(CANARIES)

    def append_contaminated(session: Session, event: AuditEvent) -> None:
        append_audit_event(
            session,
            replace(
                event,
                candidate_metadata={
                    **event.candidate_metadata,
                    **contaminated,
                },
            ),
        )

    monkeypatch.setattr(
        offer_service,
        "append_audit_event",
        append_contaminated,
    )
    settings = _settings(m2_test_database)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    with Session(m2_test_database) as session, session.begin():
        admin = User(
            phone=CANARIES["phone"],
            password_hash=None,
            is_active=True,
            is_platform_admin=True,
            created_at=NOW,
            updated_at=NOW,
        )
        account = User(
            phone="+998901234599",
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
            saved = upsert_offer_draft_text(
                session,
                actor=actor,
                offer_version_id=draft.id,
                language=language,
                title=f"{CANARIES['title']} {language.value}",
                body=f"{CANARIES['body']} {language.value}",
                now=NOW,
            )
            assert saved.succeeded
        approved = approve_offer_version(
            session,
            actor=actor,
            offer_version_id=draft.id,
            legal_review_authority="External Legal",
            legal_reviewed_at=NOW - timedelta(hours=1),
            legal_review_reference="LEGAL-2026-1050",
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
            "pytest-offer-sensitive-admin",
            NOW,
            settings=settings,
        )
        account_session = create_authenticated_session(
            session,
            account.id,
            "pytest-offer-sensitive-account",
            NOW,
            settings=settings,
        )
        admin_cookie = admin_session.raw_token.as_cookie_value()
        account_cookie = account_session.raw_token.as_cookie_value()
        admin_csrf = get_csrf_token(admin_session.session).as_form_value()
        offer_id = approved.version.id
    contaminated.update(
        {
            "cookie": admin_cookie,
            "csrf": admin_csrf,
        }
    )
    admin_client = _client(application, settings, admin_cookie)
    account_client = _client(application, settings, account_cookie)
    caplog.set_level(logging.INFO)

    denied = admin_client.post(
        f"/admin/offers/{offer_id}/texts/{OfferLanguage.UZ_LATN.value}",
        data={
            "csrf_token": admin_csrf,
            "title": CANARIES["title"],
            "body": CANARIES["body"],
            "token": CANARIES["token"],
        },
        follow_redirects=False,
    )
    account_page = account_client.get(
        f"/auth/registration-offer?language={OfferLanguage.UZ_LATN.value}"
    )
    malformed = account_client.post(
        "/auth/registration-offer/accept",
        data={
            "csrf_token": _hidden(account_page.text, "csrf_token"),
            "language": CANARIES["url"],
            "displayed_offer_text_id": CANARIES["secret"],
        },
        follow_redirects=False,
    )
    accepted = account_client.post(
        "/auth/registration-offer/accept",
        data={
            "csrf_token": _hidden(account_page.text, "csrf_token"),
            "language": _hidden(account_page.text, "language"),
            "displayed_offer_text_id": _hidden(
                account_page.text,
                "displayed_offer_text_id",
            ),
        },
        headers={"User-Agent": CANARIES["user_agent"]},
        follow_redirects=False,
    )

    assert denied.status_code == 303
    assert denied.headers["x-error-code"] == ErrorCode.OFFER_NOT_DRAFT.value
    assert malformed.status_code == 303
    assert malformed.headers["x-error-code"] == ErrorCode.VALIDATION_ERROR.value
    assert accepted.status_code == 303
    non_view_output = repr(
        (
            denied.text,
            dict(denied.headers),
            malformed.text,
            dict(malformed.headers),
            accepted.text,
            dict(accepted.headers),
        )
    )
    assert all(value not in non_view_output for value in contaminated.values())

    with Session(m2_test_database) as session:
        resolved = resolve_current_offer(
            session,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_LATN,
        )
        detail = get_offer_version_detail_for_admin(
            session,
            actor=actor,
            offer_version_id=offer_id,
        )
        text = session.scalar(select(OfferText))
        acceptance = session.scalar(select(OfferAcceptance))
        audits = tuple(session.scalars(select(AuditLog)))
        assert resolved.offer is not None
        assert detail is not None
        assert text is not None
        assert acceptance is not None
        assert CANARIES["body"] in resolved.offer.text.variant.body
        assert CANARIES["body"] in detail.texts[0].body
        assert acceptance.user_agent == "M9_RAW_UA_CANARY /1.0"
        redacted_output = repr(
            (
                resolved,
                detail,
                text,
                acceptance,
                audits,
                tuple(audit.payload for audit in audits),
            )
        )
    assert all(value not in redacted_output for value in contaminated.values())
    assert all(value not in caplog.text for value in contaminated.values())
