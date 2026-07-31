import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.main import create_app
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance, OfferText, OfferVersion
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    upsert_offer_draft_text,
)
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 11, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-offer-authz-idor"


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


def _seed_offer_versions(session: Session) -> tuple[User, UUID]:
    admin = _user(
        session,
        phone="+998900001001",
        is_platform_admin=True,
    )
    actor = require_platform_admin_actor(admin)
    current_draft = create_offer_draft_version(
        session,
        actor=actor,
        purpose=OfferPurpose.REGISTRATION,
        now=NOW,
    )
    for language in OfferLanguage:
        saved = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=current_draft.id,
            language=language,
            title=f"{language.value} current title",
            body=f"{language.value} current body",
            now=NOW,
        )
        assert saved.succeeded
    approved = approve_offer_version(
        session,
        actor=actor,
        offer_version_id=current_draft.id,
        legal_review_authority="External Legal",
        legal_reviewed_at=NOW - timedelta(hours=1),
        legal_review_reference="LEGAL-2026-1001",
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
    editable = create_offer_draft_version(
        session,
        actor=actor,
        purpose=OfferPurpose.REGISTRATION,
        now=NOW,
    )
    saved = upsert_offer_draft_text(
        session,
        actor=actor,
        offer_version_id=editable.id,
        language=OfferLanguage.UZ_LATN,
        title="ORIGINAL DRAFT TITLE",
        body="ORIGINAL DRAFT BODY",
        now=NOW,
    )
    assert saved.succeeded
    return admin, editable.id


def _role_user(session: Session, role_kind: str) -> User:
    role_index = {
        "account": "2",
        "seller": "3",
        "owner": "4",
        "platform_admin": "5",
    }[role_kind]
    user = _user(
        session,
        phone=f"+99890000100{role_index}",
        is_platform_admin=role_kind == "platform_admin",
    )
    if role_kind in {"seller", "owner"}:
        shop = Shop(
            name=f"Role Matrix {role_kind}",
            phone=f"+99891000100{role_index}",
            address_text=None,
            status=ShopStatus.ACTIVE.value,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(shop)
        session.flush()
        session.add(
            ShopStaff(
                shop_id=shop.id,
                user_id=user.id,
                role=(
                    ShopRole.CASHIER.value
                    if role_kind == "seller"
                    else ShopRole.OWNER.value
                ),
                is_active=True,
                created_at=NOW,
                updated_at=NOW,
                revoked_at=None,
            )
        )
        session.flush()
    return user


def _authenticate(
    client: TestClient,
    settings: Settings,
    session: Session,
    user: User,
) -> str:
    created = create_authenticated_session(
        session,
        user.id,
        "pytest-offer-role-matrix",
        NOW,
        settings=settings,
    )
    csrf = get_csrf_token(created.session).as_form_value()
    raw_cookie = created.raw_token.as_cookie_value()
    session.commit()
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )
    return csrf


def _hidden(html: str, field_name: str) -> str:
    match = re.search(rf'name="{field_name}" value="([^"]*)"', html)
    assert match is not None
    return match.group(1)


def test_anonymous_cannot_cross_admin_or_account_offer_boundaries(
    m2_test_database: Engine,
) -> None:
    client, _settings_value = _client(m2_test_database)
    paths = (
        ("GET", "/admin/offers", None),
        ("POST", "/admin/offers", {"purpose": "REGISTRATION"}),
        ("GET", "/auth/registration-offer", None),
        ("POST", "/auth/registration-offer/accept", {}),
    )

    for method, path, data in paths:
        response = client.request(
            method,
            path,
            data=data,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login"


@pytest.mark.parametrize("role_kind", ["account", "seller", "owner"])
def test_non_platform_roles_cannot_read_or_mutate_admin_offers(
    m2_test_database: Engine,
    role_kind: str,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session, session.begin():
        _seed_admin, draft_id = _seed_offer_versions(session)
        user = _role_user(session, role_kind)
        user_id = user.id
    with Session(m2_test_database) as session:
        user = session.get(User, user_id)
        assert user is not None
        csrf = _authenticate(client, settings, session, user)

    read_paths = (
        "/admin/offers",
        f"/admin/offers/{draft_id}",
        "/admin/offers/new",
    )
    for path in read_paths:
        assert client.get(path).status_code == 403

    mutation_requests = (
        (
            "/admin/offers",
            {"csrf_token": csrf, "purpose": OfferPurpose.REGISTRATION.value},
        ),
        (
            f"/admin/offers/{draft_id}/texts/{OfferLanguage.UZ_LATN.value}",
            {
                "csrf_token": csrf,
                "title": "FORGED TITLE",
                "body": "FORGED BODY",
            },
        ),
        (
            f"/admin/offers/{draft_id}/approve",
            {
                "csrf_token": csrf,
                "legal_review_authority": "Forged",
                "legal_reviewed_at": "2026-08-01T10:00",
                "legal_review_reference": "FORGED-1",
            },
        ),
        (
            f"/admin/offers/{draft_id}/make-current",
            {"csrf_token": csrf, "expected_current_version_id": ""},
        ),
    )
    for path, data in mutation_requests:
        assert client.post(path, data=data).status_code == 403

    with Session(m2_test_database) as session:
        versions = tuple(session.scalars(select(OfferVersion)))
        draft = session.get(OfferVersion, draft_id)
        text = session.scalar(
            select(OfferText).where(
                OfferText.offer_version_id == draft_id,
                OfferText.language == OfferLanguage.UZ_LATN.value,
            )
        )
        assert len(versions) == 2
        assert draft is not None
        assert draft.status == OfferStatus.DRAFT.value
        assert text is not None
        assert text.title == "ORIGINAL DRAFT TITLE"
        assert text.body == "ORIGINAL DRAFT BODY"


@pytest.mark.parametrize(
    "role_kind",
    ["account", "seller", "owner", "platform_admin"],
)
def test_every_active_authenticated_role_can_read_and_accept_current_registration(
    m2_test_database: Engine,
    role_kind: str,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session, session.begin():
        _seed_admin, _draft_id = _seed_offer_versions(session)
        user = _role_user(session, role_kind)
        user_id = user.id
    with Session(m2_test_database) as session:
        user = session.get(User, user_id)
        assert user is not None
        _authenticate(client, settings, session, user)

    page = client.get(f"/auth/registration-offer?language={OfferLanguage.RU.value}")
    response = client.post(
        "/auth/registration-offer/accept",
        data={
            "csrf_token": _hidden(page.text, "csrf_token"),
            "language": _hidden(page.text, "language"),
            "displayed_offer_text_id": _hidden(
                page.text,
                "displayed_offer_text_id",
            ),
        },
        follow_redirects=False,
    )

    assert page.status_code == 200
    assert response.status_code == 303
    with Session(m2_test_database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OfferAcceptance)
                .where(OfferAcceptance.user_id == user_id)
            )
            == 1
        )


def test_platform_admin_has_admin_read_and_create_authority(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session, session.begin():
        admin, draft_id = _seed_offer_versions(session)
        admin_id = admin.id
    with Session(m2_test_database) as session:
        admin = session.get(User, admin_id)
        assert admin is not None
        csrf = _authenticate(client, settings, session, admin)

    assert client.get("/admin/offers").status_code == 200
    assert client.get(f"/admin/offers/{draft_id}").status_code == 200
    created = client.post(
        "/admin/offers",
        data={
            "csrf_token": csrf,
            "purpose": OfferPurpose.REGISTRATION.value,
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(OfferVersion)) == 3
