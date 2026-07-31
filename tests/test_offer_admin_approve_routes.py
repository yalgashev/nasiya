from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

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
    create_offer_draft_version,
    upsert_offer_draft_text,
)
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 7, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-offer-admin-approve"


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
    complete: bool,
    is_platform_admin: bool = True,
) -> tuple[UUID, str]:
    with Session(engine) as session, session.begin():
        user = User(
            phone="+998900000987",
            password_hash=None,
            is_active=True,
            is_platform_admin=is_platform_admin,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(user)
        session.flush()
        if is_platform_admin:
            actor = require_platform_admin_actor(user)
            draft = create_offer_draft_version(
                session,
                actor=actor,
                purpose=OfferPurpose.REGISTRATION,
                now=NOW,
            )
            languages = tuple(OfferLanguage) if complete else (OfferLanguage.UZ_LATN,)
            for language in languages:
                result = upsert_offer_draft_text(
                    session,
                    actor=actor,
                    offer_version_id=draft.id,
                    language=language,
                    title=f"{language.value} title",
                    body=f"{language.value} body",
                    now=NOW,
                )
                assert result.succeeded
            offer_version_id = draft.id
        else:
            offer_version_id = user.id
        created = create_authenticated_session(
            session,
            user.id,
            "pytest-offer-admin-approve",
            NOW,
            settings=settings,
        )
        csrf_token = get_csrf_token(created.session).as_form_value()
        raw_cookie = created.raw_token.as_cookie_value()
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )
    return offer_version_id, csrf_token


def test_complete_draft_approve_is_explicit_evidence_prg(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    draft_id, csrf_token = _seed(
        m2_test_database,
        client,
        settings,
        complete=True,
    )
    detail = client.get(f"/admin/offers/{draft_id}")

    assert detail.status_code == 200
    assert f'action="/admin/offers/{draft_id}/approve"' in detail.text
    for field_name in (
        "legal_review_authority",
        "legal_reviewed_at",
        "legal_review_reference",
    ):
        assert f'name="{field_name}"' in detail.text
    assert "To‘liq" in detail.text

    response = client.post(
        f"/admin/offers/{draft_id}/approve",
        data={
            "csrf_token": csrf_token,
            "legal_review_authority": "External Legal",
            "legal_reviewed_at": "2026-08-01T06:00",
            "legal_review_reference": "LEGAL-2026-987",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/admin/offers/{draft_id}?notice=offer-approved"
    )
    page = client.get(response.headers["location"])
    assert "Offer tasdiqlandi." in page.text
    assert f"/admin/offers/{draft_id}/approve" not in page.text
    with Session(m2_test_database) as session:
        version = session.get(OfferVersion, draft_id)
        assert version is not None
        assert version.status == OfferStatus.APPROVED.value
        assert version.legal_review_authority == "External Legal"
        assert version.legal_reviewed_at == NOW - timedelta(hours=1)
        assert version.legal_review_reference == "LEGAL-2026-987"


def test_incomplete_draft_approval_is_denied_without_transition(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    draft_id, csrf_token = _seed(
        m2_test_database,
        client,
        settings,
        complete=False,
    )

    response = client.post(
        f"/admin/offers/{draft_id}/approve",
        data={
            "csrf_token": csrf_token,
            "legal_review_authority": "External Legal",
            "legal_reviewed_at": "2026-08-01T06:00",
            "legal_review_reference": "LEGAL-2026-987",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["x-error-code"] == ErrorCode.OFFER_INCOMPLETE.value
    assert response.headers["location"] == (
        f"/admin/offers/{draft_id}?error=offer-incomplete"
    )
    with Session(m2_test_database) as session:
        version = session.get(OfferVersion, draft_id)
        assert version is not None
        assert version.status == OfferStatus.DRAFT.value


@pytest.mark.parametrize(
    ("reviewed_at", "expected_code", "expected_slug"),
    [
        (
            "",
            ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED,
            "legal-review-evidence-required",
        ),
        ("not-a-time", ErrorCode.VALIDATION_ERROR, "validation-error"),
        (
            "2026-08-01T08:00",
            ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED,
            "legal-review-evidence-required",
        ),
    ],
)
def test_missing_invalid_or_future_review_evidence_is_denied(
    m2_test_database: Engine,
    reviewed_at: str,
    expected_code: ErrorCode,
    expected_slug: str,
) -> None:
    client, settings = _client(m2_test_database)
    draft_id, csrf_token = _seed(
        m2_test_database,
        client,
        settings,
        complete=True,
    )

    response = client.post(
        f"/admin/offers/{draft_id}/approve",
        data={
            "csrf_token": csrf_token,
            "legal_review_authority": ("" if not reviewed_at else "External Legal"),
            "legal_reviewed_at": reviewed_at,
            "legal_review_reference": ("" if not reviewed_at else "LEGAL-2026-987"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["x-error-code"] == expected_code.value
    assert response.headers["location"] == (
        f"/admin/offers/{draft_id}?error={expected_slug}"
    )
    with Session(m2_test_database) as session:
        version = session.get(OfferVersion, draft_id)
        assert version is not None
        assert version.status == OfferStatus.DRAFT.value


def test_non_admin_cannot_submit_approval(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    target_id, csrf_token = _seed(
        m2_test_database,
        client,
        settings,
        complete=False,
        is_platform_admin=False,
    )

    response = client.post(
        f"/admin/offers/{target_id}/approve",
        data={
            "csrf_token": csrf_token,
            "legal_review_authority": "Synthetic",
            "legal_reviewed_at": "2026-08-01T06:00",
            "legal_review_reference": "SYNTHETIC-1",
        },
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
