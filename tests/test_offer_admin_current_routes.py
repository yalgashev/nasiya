from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferVersion
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    upsert_offer_draft_text,
)
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-offer-admin-current"
_TIMEOUT = 20


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def _client(engine: Engine, settings: Settings, raw_cookie: str) -> TestClient:
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    client = TestClient(application)
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )
    return client


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


def _seed(engine: Engine) -> tuple[Settings, str, str, UUID, UUID, UUID]:
    settings = _settings(engine)
    with Session(engine) as session, session.begin():
        admin = User(
            phone="+998900000988",
            password_hash=None,
            is_active=True,
            is_platform_admin=True,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(admin)
        session.flush()
        actor = require_platform_admin_actor(admin)
        current = _approved(session, actor=actor, reference="LEGAL-2026-988")
        assert make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=current.id,
            expected_current_version_id=None,
            now=NOW,
        ).succeeded
        first_candidate = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-989",
        )
        second_candidate = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-990",
        )
        created = create_authenticated_session(
            session,
            admin.id,
            "pytest-offer-admin-current",
            NOW,
            settings=settings,
        )
        csrf = get_csrf_token(created.session).as_form_value()
        raw_cookie = created.raw_token.as_cookie_value()
    return (
        settings,
        raw_cookie,
        csrf,
        current.id,
        first_candidate.id,
        second_candidate.id,
    )


def _current_audit_count(engine: Engine) -> int:
    with Session(engine) as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.OFFER_VERSION_MADE_CURRENT.value
                )
            )
            or 0
        )


def test_approved_detail_shows_safe_expected_current_and_switches_with_prg(
    m2_test_database: Engine,
) -> None:
    settings, raw_cookie, csrf, current_id, candidate_id, _other_id = _seed(
        m2_test_database
    )
    client = _client(m2_test_database, settings, raw_cookie)
    page = client.get(f"/admin/offers/{candidate_id}")

    assert page.status_code == 200
    assert f'action="/admin/offers/{candidate_id}/make-current"' in page.text
    assert f'value="{current_id}"' in page.text
    assert "REGISTRATION · v1" in page.text
    response = client.post(
        f"/admin/offers/{candidate_id}/make-current",
        data={
            "csrf_token": csrf,
            "expected_current_version_id": str(current_id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/admin/offers/{candidate_id}?notice=offer-made-current"
    )
    with Session(m2_test_database) as session:
        target = session.get(OfferVersion, candidate_id)
        previous = session.get(OfferVersion, current_id)
        assert target is not None
        assert previous is not None
        assert target.status == OfferStatus.CURRENT.value
        assert previous.status == OfferStatus.APPROVED.value


def test_already_current_post_is_noop_without_duplicate_audit(
    m2_test_database: Engine,
) -> None:
    settings, raw_cookie, csrf, current_id, _candidate_id, _other_id = _seed(
        m2_test_database
    )
    client = _client(m2_test_database, settings, raw_cookie)
    before = _current_audit_count(m2_test_database)

    response = client.post(
        f"/admin/offers/{current_id}/make-current",
        data={
            "csrf_token": csrf,
            "expected_current_version_id": str(current_id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/admin/offers/{current_id}?notice=offer-already-current"
    )
    assert _current_audit_count(m2_test_database) == before


def test_concurrent_web_switch_has_one_winner_and_one_changed_prg(
    m2_test_database: Engine,
) -> None:
    settings, raw_cookie, csrf, current_id, first_id, second_id = _seed(
        m2_test_database
    )
    barrier = Barrier(2)
    clients = {
        target_id: _client(m2_test_database, settings, raw_cookie)
        for target_id in (first_id, second_id)
    }
    for client in clients.values():
        assert client.get("/health").status_code == 200

    def switch(target_id: UUID) -> tuple[int, str | None, str]:
        barrier.wait(timeout=10)
        response = clients[target_id].post(
            f"/admin/offers/{target_id}/make-current",
            data={
                "csrf_token": csrf,
                "expected_current_version_id": str(current_id),
            },
            follow_redirects=False,
        )
        return (
            response.status_code,
            response.headers.get("x-error-code"),
            response.headers["location"],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(switch, target_id) for target_id in (first_id, second_id)
        ]
        outcomes = [future.result(timeout=_TIMEOUT) for future in futures]

    assert [status for status, _error, _location in outcomes] == [303, 303]
    assert sorted(error or "" for _status, error, _location in outcomes) == [
        "",
        ErrorCode.OFFER_CHANGED.value,
    ]
    assert (
        sum(
            "notice=offer-made-current" in location
            for _status, _error, location in outcomes
        )
        == 1
    )
    assert (
        sum("error=offer-changed" in location for _status, _error, location in outcomes)
        == 1
    )
    with Session(m2_test_database) as session:
        current_rows = tuple(
            session.scalars(
                select(OfferVersion).where(
                    OfferVersion.status == OfferStatus.CURRENT.value
                )
            )
        )
        assert len(current_rows) == 1
        assert current_rows[0].id in {first_id, second_id}


def test_make_current_missing_csrf_does_not_switch(
    m2_test_database: Engine,
) -> None:
    settings, raw_cookie, _csrf, current_id, candidate_id, _other_id = _seed(
        m2_test_database
    )
    client = _client(m2_test_database, settings, raw_cookie)

    response = client.post(
        f"/admin/offers/{candidate_id}/make-current",
        data={"expected_current_version_id": str(current_id)},
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    with Session(m2_test_database) as session:
        target = session.get(OfferVersion, candidate_id)
        current = session.get(OfferVersion, current_id)
        assert target is not None
        assert current is not None
        assert target.status == OfferStatus.APPROVED.value
        assert current.status == OfferStatus.CURRENT.value
