from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferAcceptance, OfferText, OfferVersion
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    upsert_offer_draft_text,
)
from app.settings import Settings

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-offer-csrf-sweep"


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


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


def _complete_draft(session: Session, *, actor, reference: str):
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
            title=f"{reference} {language.value} title",
            body=f"{reference} {language.value} body",
            now=NOW,
        ).succeeded
    return draft


def _session_credentials(
    session: Session,
    *,
    user: User,
    settings: Settings,
    label: str,
) -> tuple[str, str]:
    created = create_authenticated_session(
        session,
        user.id,
        f"pytest-offer-csrf-{label}",
        NOW,
        settings=settings,
    )
    return (
        created.raw_token.as_cookie_value(),
        get_csrf_token(created.session).as_form_value(),
    )


def _client(
    engine: Engine,
    settings: Settings,
    raw_cookie: str,
) -> TestClient:
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


def _seed(engine: Engine):
    settings = _settings(engine)
    with Session(engine) as session, session.begin():
        admin_a = _user(
            session,
            phone="+998900001006",
            is_platform_admin=True,
        )
        admin_b = _user(
            session,
            phone="+998900001007",
            is_platform_admin=True,
        )
        account_a = _user(
            session,
            phone="+998900001008",
            is_platform_admin=False,
        )
        account_b = _user(
            session,
            phone="+998900001009",
            is_platform_admin=False,
        )
        actor = require_platform_admin_actor(admin_a)
        current = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-1006",
        )
        assert make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=current.id,
            expected_current_version_id=None,
            now=NOW,
        ).succeeded
        current_ru = next(
            text
            for text in session.scalars(
                select(OfferText).where(OfferText.offer_version_id == current.id)
            )
            if text.language == OfferLanguage.RU.value
        )
        candidate = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-1007",
        )
        approval_draft = _complete_draft(
            session,
            actor=actor,
            reference="LEGAL-2026-1008",
        )
        editable = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        assert upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=editable.id,
            language=OfferLanguage.UZ_LATN,
            title="ORIGINAL CSRF TITLE",
            body="ORIGINAL CSRF BODY",
            now=NOW,
        ).succeeded
        admin_a_cookie, admin_a_csrf = _session_credentials(
            session,
            user=admin_a,
            settings=settings,
            label="admin-a",
        )
        _admin_b_cookie, admin_b_csrf = _session_credentials(
            session,
            user=admin_b,
            settings=settings,
            label="admin-b",
        )
        account_a_cookie, account_a_csrf = _session_credentials(
            session,
            user=account_a,
            settings=settings,
            label="account-a",
        )
        _account_b_cookie, account_b_csrf = _session_credentials(
            session,
            user=account_b,
            settings=settings,
            label="account-b",
        )
        identifiers = {
            "current": current.id,
            "current_ru_text": current_ru.id,
            "candidate": candidate.id,
            "approval_draft": approval_draft.id,
            "editable": editable.id,
        }
    return {
        "settings": settings,
        "admin_client": _client(engine, settings, admin_a_cookie),
        "admin_csrf": admin_a_csrf,
        "admin_cross_csrf": admin_b_csrf,
        "account_client": _client(engine, settings, account_a_cookie),
        "account_csrf": account_a_csrf,
        "account_cross_csrf": account_b_csrf,
        "ids": identifiers,
    }


def _state(engine: Engine) -> tuple[object, ...]:
    with Session(engine) as session:
        versions = tuple(
            session.execute(
                select(
                    OfferVersion.id,
                    OfferVersion.status,
                    OfferVersion.version_number,
                ).order_by(OfferVersion.id)
            )
        )
        texts = tuple(
            session.execute(
                select(
                    OfferText.id,
                    OfferText.title,
                    OfferText.body,
                    OfferText.content_hash,
                ).order_by(OfferText.id)
            )
        )
        return (
            versions,
            texts,
            session.scalar(select(func.count()).select_from(OfferAcceptance)),
            session.scalar(select(func.count()).select_from(AuditLog)),
        )


def _request(seed, mutation: str) -> tuple[TestClient, str, dict[str, str]]:
    ids = seed["ids"]
    if mutation == "create":
        return (
            seed["admin_client"],
            "/admin/offers",
            {"purpose": OfferPurpose.REGISTRATION.value},
        )
    if mutation == "edit":
        return (
            seed["admin_client"],
            (f"/admin/offers/{ids['editable']}/texts/{OfferLanguage.UZ_LATN.value}"),
            {"title": "FORGED CSRF TITLE", "body": "FORGED CSRF BODY"},
        )
    if mutation == "approve":
        return (
            seed["admin_client"],
            f"/admin/offers/{ids['approval_draft']}/approve",
            {
                "legal_review_authority": "External Legal",
                "legal_reviewed_at": "2026-08-01T11:00",
                "legal_review_reference": "LEGAL-2026-CSRF",
            },
        )
    if mutation == "current":
        return (
            seed["admin_client"],
            f"/admin/offers/{ids['candidate']}/make-current",
            {"expected_current_version_id": str(ids["current"])},
        )
    return (
        seed["account_client"],
        "/auth/registration-offer/accept",
        {
            "language": OfferLanguage.RU.value,
            "displayed_offer_text_id": str(ids["current_ru_text"]),
        },
    )


@pytest.mark.parametrize(
    ("mutation", "csrf_case"),
    [
        (mutation, csrf_case)
        for mutation in ("create", "edit", "approve", "current", "accept")
        for csrf_case in ("missing", "wrong", "cross-session")
    ],
)
def test_every_offer_mutation_rejects_invalid_csrf_without_state_change(
    m2_test_database: Engine,
    mutation: str,
    csrf_case: str,
) -> None:
    seed = _seed(m2_test_database)
    client, path, data = _request(seed, mutation)
    if csrf_case == "wrong":
        data["csrf_token"] = "wrong-csrf-token"
    elif csrf_case == "cross-session":
        data["csrf_token"] = (
            seed["account_cross_csrf"]
            if mutation == "accept"
            else seed["admin_cross_csrf"]
        )
    before = _state(m2_test_database)

    response = client.post(path, data=data, follow_redirects=False)

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert response.headers["cache-control"] == "no-store"
    assert _state(m2_test_database) == before


def test_offer_templates_do_not_use_htmx_mutations() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8").casefold()
        for path in Path("app/templates/offers").glob("*.html")
    )

    assert "hx-" not in sources
