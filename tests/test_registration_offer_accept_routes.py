import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferAcceptance
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    upsert_offer_draft_text,
)
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-registration-offer-accept"


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


def _approved(session: Session, *, actor, reference: str):
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
            title=f"{reference} {language.value} title",
            body=f"{reference} {language.value} body",
            now=NOW,
        )
        assert saved.succeeded
    approved = approve_offer_version(
        session,
        actor=actor,
        offer_version_id=draft.id,
        legal_review_authority="External Legal",
        legal_reviewed_at=NOW - timedelta(hours=1),
        legal_review_reference=reference,
        now=NOW,
    )
    assert approved.version is not None
    return approved.version


def _seed(
    engine: Engine,
    client: TestClient,
    settings: Settings,
) -> tuple[UUID, UUID]:
    with Session(engine) as session, session.begin():
        admin = User(
            phone="+998900000993",
            password_hash=None,
            is_active=True,
            is_platform_admin=True,
            created_at=NOW,
            updated_at=NOW,
        )
        account = User(
            phone="+998900000994",
            password_hash=None,
            is_active=True,
            is_platform_admin=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add_all((admin, account))
        session.flush()
        actor = require_platform_admin_actor(admin)
        current = _approved(session, actor=actor, reference="LEGAL-2026-993")
        assert make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=current.id,
            expected_current_version_id=None,
            now=NOW,
        ).succeeded
        created = create_authenticated_session(
            session,
            account.id,
            "pytest-registration-offer-accept",
            NOW,
            settings=settings,
        )
        raw_cookie = created.raw_token.as_cookie_value()
        admin_id = admin.id
        current_id = current.id
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )
    return admin_id, current_id


def _hidden(html: str, field_name: str) -> str:
    match = re.search(
        rf'name="{field_name}" value="([^"]*)"',
        html,
    )
    assert match is not None
    return match.group(1)


def _acceptance_counts(engine: Engine) -> tuple[int, int]:
    with Session(engine) as session:
        acceptance_count = (
            session.scalar(select(func.count()).select_from(OfferAcceptance)) or 0
        )
        audit_count = (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.OFFER_REGISTRATION_ACCEPTED.value
                )
            )
            or 0
        )
        return acceptance_count, audit_count


def test_accept_post_prg_and_replay_keep_one_evidence_and_audit(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    _admin_id, _current_id = _seed(m2_test_database, client, settings)
    page = client.get(f"/auth/registration-offer?language={OfferLanguage.RU.value}")
    form = {
        "csrf_token": _hidden(page.text, "csrf_token"),
        "language": _hidden(page.text, "language"),
        "displayed_offer_text_id": _hidden(
            page.text,
            "displayed_offer_text_id",
        ),
    }

    created = client.post(
        "/auth/registration-offer/accept",
        data=form,
        headers={"User-Agent": "Browser   Test"},
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert created.headers["location"] == (
        "/auth/registration-offer?language=RU&notice=acceptance-recorded"
    )
    result_page = client.get(created.headers["location"])
    assert "Registration offer qabul qilindi." in result_page.text
    assert _acceptance_counts(m2_test_database) == (1, 1)

    replay = client.post(
        "/auth/registration-offer/accept",
        data=form,
        follow_redirects=False,
    )

    assert replay.status_code == 303
    assert replay.headers["location"] == (
        "/auth/registration-offer?language=RU&notice=acceptance-replayed"
    )
    assert _acceptance_counts(m2_test_database) == (1, 1)
    with Session(m2_test_database) as session:
        acceptance = session.scalar(select(OfferAcceptance))
        assert acceptance is not None
        assert acceptance.language == OfferLanguage.RU.value
        assert acceptance.user_agent == "Browser Test"


def test_stale_form_redirects_and_renders_new_current(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    admin_id, old_current_id = _seed(m2_test_database, client, settings)
    old_page = client.get(
        f"/auth/registration-offer?language={OfferLanguage.UZ_LATN.value}"
    )
    stale_form = {
        "csrf_token": _hidden(old_page.text, "csrf_token"),
        "language": _hidden(old_page.text, "language"),
        "displayed_offer_text_id": _hidden(
            old_page.text,
            "displayed_offer_text_id",
        ),
    }
    with Session(m2_test_database) as session, session.begin():
        admin = session.get(User, admin_id)
        assert admin is not None
        actor = require_platform_admin_actor(admin)
        replacement = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-994",
        )
        assert make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=replacement.id,
            expected_current_version_id=old_current_id,
            now=NOW,
        ).succeeded

    response = client.post(
        "/auth/registration-offer/accept",
        data=stale_form,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["x-error-code"] == ErrorCode.OFFER_CHANGED.value
    assert response.headers["location"] == (
        "/auth/registration-offer?language=UZ_LATN&error=offer-changed"
    )
    refreshed = client.get(response.headers["location"])
    assert "Joriy offer o‘zgargan. Sahifani yangilang." in refreshed.text
    assert "LEGAL-2026-994 UZ_LATN title" in refreshed.text
    assert "LEGAL-2026-993 UZ_LATN title" not in refreshed.text
    assert _acceptance_counts(m2_test_database) == (0, 0)


def test_accept_missing_csrf_and_malformed_form_are_zero_write(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    _admin_id, _current_id = _seed(m2_test_database, client, settings)
    page = client.get("/auth/registration-offer")
    text_id = _hidden(page.text, "displayed_offer_text_id")

    no_csrf = client.post(
        "/auth/registration-offer/accept",
        data={
            "language": OfferLanguage.UZ_LATN.value,
            "displayed_offer_text_id": text_id,
        },
    )
    malformed = client.post(
        "/auth/registration-offer/accept",
        data={
            "csrf_token": _hidden(page.text, "csrf_token"),
            "language": "HTML",
            "displayed_offer_text_id": "not-a-uuid",
        },
        follow_redirects=False,
    )

    assert no_csrf.status_code == 403
    assert no_csrf.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert malformed.status_code == 303
    assert malformed.headers["x-error-code"] == ErrorCode.VALIDATION_ERROR.value
    assert _acceptance_counts(m2_test_database) == (0, 0)
