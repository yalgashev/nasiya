import base64
import json
import re
from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_document.models import CustomerDocument
from app.customer_identity.models import CustomerIdentity
from app.main import create_app
from app.settings import Settings
from app.storage.models import ObjectFile, ObjectFileStatus
from tests.storage_fake import FakeObjectStorageService, FakeStorageOperation

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
RAW_FIRST_NAME = "Dilshod"
RAW_LAST_NAME = "Yoqubov"
RAW_MIDDLE_NAME = "Komil o'g'li"
RAW_JSHSHIR = "12345678901234"
RAW_DOCUMENT_NUMBER = "AA 1234567"


class SessionCheckingStorage(FakeObjectStorageService):
    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    def put_object(self, **kwargs):
        _assert_no_checked_out_connection(self._engine)
        return super().put_object(**kwargs)

    def head_object(self, **kwargs):
        _assert_no_checked_out_connection(self._engine)
        return super().head_object(**kwargs)

    def create_presigned_get_url(self, **kwargs):
        _assert_no_checked_out_connection(self._engine)
        return super().create_presigned_get_url(**kwargs)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = Session(m2_test_database)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _encoded(marker: int) -> str:
    return base64.b64encode(bytes([marker]) * 32).decode("ascii")


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key="m10-web-rate-limit-key-not-sensitive",
        customer_identity_active_key_id="identity-v1",
        customer_identity_encryption_keys=json.dumps(
            {"identity-v1": _encoded(1)},
            separators=(",", ":"),
        ),
        customer_identity_blind_index_key=_encoded(2),
        object_storage_endpoint_url="https://m10-web-storage.invalid",
        object_storage_region="region-1",
        object_storage_bucket="m10-private-web",
        object_storage_access_key="m10-web-access",
        object_storage_secret_key="m10-web-secret",
        object_storage_use_ssl=True,
    )


def _client(
    engine: Engine,
    storage: FakeObjectStorageService,
) -> tuple[TestClient, Settings]:
    settings = _settings(engine)
    application = create_app(
        settings=settings,
        customer_document_storage_service=storage,
    )
    application.dependency_overrides[get_current_time] = lambda: NOW
    return TestClient(application, client=("203.0.113.220", 50_000)), settings


def _seed_authenticated_draft(
    session: Session,
    *,
    settings: Settings,
    phone: str,
) -> tuple[User, Customer, CreatedSession]:
    created_user = create_user(session, phone, "Password123")
    assert created_user.user is not None
    user = created_user.user
    session.flush()
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()
    created_session = create_authenticated_session(
        session,
        user.id,
        "pytest-m10-web",
        NOW,
        settings=settings,
    )
    session.commit()
    return user, customer, created_session


def _set_cookie(
    client: TestClient,
    settings: Settings,
    created: CreatedSession,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )


def _hidden(html: str, name: str) -> str:
    matched = re.search(
        rf'name="{re.escape(name)}"\s+value="(?P<value>[^"]*)"',
        html,
    )
    assert matched is not None
    return matched.group("value")


def _png_bytes() -> bytes:
    output = BytesIO()
    with Image.new("RGBA", (6, 4), (10, 30, 50, 128)) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _identity_form(csrf_token: str, *, revision: str = "0") -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "expected_revision": revision,
        "first_name": f"  {RAW_FIRST_NAME}  ",
        "last_name": RAW_LAST_NAME,
        "middle_name": RAW_MIDDLE_NAME,
        "jshshir": RAW_JSHSHIR,
        "document_type": "PASSPORT",
        "document_number": RAW_DOCUMENT_NUMBER,
    }


def test_identity_get_post_prg_masking_localization_and_encrypted_persistence(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    storage = SessionCheckingStorage(m2_test_database)
    client, settings = _client(m2_test_database, storage)
    _, customer, created = _seed_authenticated_draft(
        db_session,
        settings=settings,
        phone="+998900001501",
    )
    _set_cookie(client, settings, created)

    initial = client.get("/customer/identity")
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    assert "Shaxsiy ma&#39;lumotlar va hujjat" in initial.text
    csrf_token = _hidden(initial.text, "csrf_token")
    assert csrf_token == get_csrf_token(created.session).as_form_value()
    assert _hidden(initial.text, "expected_revision") == "0"

    saved = client.post(
        "/customer/identity",
        data=_identity_form(csrf_token),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == ("/customer/identity?notice=identity-saved")
    assert saved.headers["cache-control"] == "no-store"
    for plaintext in (
        RAW_FIRST_NAME,
        RAW_LAST_NAME,
        RAW_MIDDLE_NAME,
        RAW_JSHSHIR,
        RAW_DOCUMENT_NUMBER,
    ):
        assert plaintext not in saved.headers["location"]

    page = client.get(saved.headers["location"])
    assert page.status_code == 200
    assert RAW_FIRST_NAME in page.text
    assert RAW_LAST_NAME in page.text
    assert RAW_MIDDLE_NAME in unescape(page.text)
    assert RAW_JSHSHIR not in page.text
    assert RAW_DOCUMENT_NUMBER not in page.text
    assert "**********1234" in page.text
    assert "*****4567" in page.text
    assert re.search(r'name="jshshir"[^>]+value=""', page.text)
    assert re.search(r'name="document_number"[^>]+value=""', page.text)
    assert str(customer.id) not in page.text
    assert "object_file_id" not in page.text
    assert "ciphertext" not in page.text
    assert "nonce" not in page.text

    ru_page = client.get(
        "/customer/identity",
        headers={"Accept-Language": "ru-RU"},
    )
    assert ru_page.status_code == 200
    assert '<html lang="ru">' in ru_page.text
    assert "Персональные данные и документ" in ru_page.text
    assert "Загрузить изображение документа" in ru_page.text

    db_session.expire_all()
    identity = db_session.get(CustomerIdentity, customer.id)
    assert identity is not None
    persisted = bytes(identity.ciphertext)
    for plaintext in (
        RAW_FIRST_NAME,
        RAW_LAST_NAME,
        RAW_MIDDLE_NAME,
        RAW_JSHSHIR,
        RAW_DOCUMENT_NUMBER,
    ):
        assert plaintext.encode("utf-8") not in persisted
    audit_payloads = tuple(db_session.scalars(select(AuditLog.payload)))
    rendered_audit = repr(audit_payloads)
    assert RAW_JSHSHIR not in rendered_audit
    assert RAW_DOCUMENT_NUMBER not in rendered_audit


def test_document_upload_access_and_csrf_denial_are_prg_and_provider_safe(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    storage = SessionCheckingStorage(m2_test_database)
    client, settings = _client(m2_test_database, storage)
    _, _, created = _seed_authenticated_draft(
        db_session,
        settings=settings,
        phone="+998900001502",
    )
    _set_cookie(client, settings, created)
    page = client.get("/customer/identity")
    csrf_token = _hidden(page.text, "csrf_token")
    submission_id = _hidden(page.text, "submission_id")
    expected_current = _hidden(page.text, "expected_current_document_id")

    denied = client.post(
        "/customer/identity/document",
        data={
            "submission_id": submission_id,
            "expected_current_document_id": expected_current,
        },
        files={
            "document_file": ("private-passport-name.png", _png_bytes(), "image/png")
        },
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert denied.headers["location"] == ("/customer/identity?error=CSRF_FAILED")
    assert denied.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert storage.calls == ()
    assert db_session.scalar(select(func.count()).select_from(ObjectFile)) == 0
    db_session.rollback()

    uploaded = client.post(
        "/customer/identity/document",
        data={
            "csrf_token": csrf_token,
            "submission_id": submission_id,
            "expected_current_document_id": expected_current,
        },
        files={
            "document_file": ("private-passport-name.png", _png_bytes(), "image/png")
        },
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    assert uploaded.headers["location"] == (
        "/customer/identity?notice=document-uploaded"
    )
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]

    refreshed = client.get(uploaded.headers["location"])
    assert "Joriy hujjat rasmi mavjud" in refreshed.text
    assert 'href="/customer/identity/document"' in refreshed.text
    assert "private-passport-name.png" not in refreshed.text

    access = client.get(
        "/customer/identity/document",
        follow_redirects=False,
    )
    assert access.status_code == 303
    assert access.headers["location"].startswith("https://")
    assert access.headers["cache-control"] == "no-store"
    assert access.headers["referrer-policy"] == "no-referrer"
    assert storage.calls[-1].operation is FakeStorageOperation.PRESIGN_GET
    assert storage.calls[-1].ttl_seconds == 300

    db_session.expire_all()
    document = db_session.scalar(select(CustomerDocument))
    object_file = db_session.scalar(select(ObjectFile))
    assert document is not None
    assert object_file is not None
    assert object_file.status == ObjectFileStatus.AVAILABLE.value
    assert "private-passport-name.png" not in repr(document)
    assert "private-passport-name.png" not in repr(object_file)
    payloads = tuple(db_session.scalars(select(AuditLog.payload)))
    rendered = repr(payloads)
    assert str(object_file.id) not in rendered
    assert access.headers["location"] not in rendered


def test_identity_and_document_routes_have_no_authority_identifiers() -> None:
    application = create_app(
        settings=Settings(
            _env_file=None,
            app_environment="testing",
            debug=False,
            database_url="postgresql+psycopg://nasiya:test@127.0.0.1/nasiya_test",
            session_cookie_secure=False,
            rate_limit_hmac_key="m10-route-inventory-key-not-sensitive",
        )
    )
    route_paths = {
        path
        for path in application.openapi()["paths"]
        if path.startswith("/customer/identity")
    }
    assert route_paths == {
        "/customer/identity",
        "/customer/identity/document",
    }
    assert all("{" not in path and "}" not in path for path in route_paths)


def test_identity_csrf_errors_prg_without_state_and_account_link_is_localized(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    storage = FakeObjectStorageService()
    client, settings = _client(m2_test_database, storage)
    user, _, created = _seed_authenticated_draft(
        db_session,
        settings=settings,
        phone="+998900001503",
    )
    other_session = create_authenticated_session(
        db_session,
        user.id,
        "pytest-m10-web-other-session",
        NOW,
        settings=settings,
    )
    db_session.commit()
    _set_cookie(client, settings, created)

    for invalid_token in (
        None,
        "invalid-csrf-token",
        get_csrf_token(other_session.session).as_form_value(),
    ):
        form = _identity_form(invalid_token or "")
        if invalid_token is None:
            form.pop("csrf_token")
        response = client.post(
            "/customer/identity",
            data=form,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == ("/customer/identity?error=CSRF_FAILED")
        assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
        assert response.headers["cache-control"] == "no-store"
    assert db_session.scalar(select(func.count()).select_from(CustomerIdentity)) == 0
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 0

    account = client.get("/auth/account", headers={"Accept-Language": "ru"})
    assert account.status_code == 200
    assert 'href="/customer/identity"' in account.text
    assert "Персональные данные" in account.text


def test_document_stale_prg_then_fresh_supersede_preserves_one_current(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    storage = SessionCheckingStorage(m2_test_database)
    client, settings = _client(m2_test_database, storage)
    _, _, created = _seed_authenticated_draft(
        db_session,
        settings=settings,
        phone="+998900001504",
    )
    _set_cookie(client, settings, created)
    first_form_page = client.get("/customer/identity")
    stale_form_page = client.get("/customer/identity")

    def upload_from(page_text: str, filename: str):
        return client.post(
            "/customer/identity/document",
            data={
                "csrf_token": _hidden(page_text, "csrf_token"),
                "submission_id": _hidden(page_text, "submission_id"),
                "expected_current_document_id": _hidden(
                    page_text,
                    "expected_current_document_id",
                ),
            },
            files={"document_file": (filename, _png_bytes(), "image/png")},
            follow_redirects=False,
        )

    first = upload_from(first_form_page.text, "first-private-name.png")
    assert first.headers["location"] == ("/customer/identity?notice=document-uploaded")
    calls_after_first = storage.calls
    stale = upload_from(stale_form_page.text, "stale-private-name.png")
    assert stale.status_code == 303
    assert stale.headers["location"] == (
        "/customer/identity?error=CUSTOMER_DOCUMENT_CHANGED"
    )
    assert storage.calls == calls_after_first

    fresh_form_page = client.get("/customer/identity")
    replacement = upload_from(fresh_form_page.text, "second-private-name.png")
    assert replacement.headers["location"] == (
        "/customer/identity?notice=document-uploaded"
    )
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    with Session(m2_test_database) as verification:
        statuses = tuple(
            verification.scalars(
                select(CustomerDocument.status).order_by(CustomerDocument.attached_at)
            )
        )
        assert sorted(statuses) == sorted(
            [
                "CURRENT",
                "SUPERSEDED",
            ]
        )
        assert (
            verification.scalar(
                select(func.count())
                .select_from(CustomerDocument)
                .where(CustomerDocument.status == "CURRENT")
            )
            == 1
        )
        assert (
            verification.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "customer.document_superseded")
            )
            == 1
        )


def test_identity_xss_autoescape_mobile_file_contract_and_body_guard(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    storage = FakeObjectStorageService()
    client, settings = _client(m2_test_database, storage)
    _, _, created = _seed_authenticated_draft(
        db_session,
        settings=settings,
        phone="+998900001505",
    )
    _set_cookie(client, settings, created)
    page = client.get("/customer/identity")
    malicious_name = '<img src=x onerror="alert(1)">'
    form = _identity_form(_hidden(page.text, "csrf_token"))
    form["first_name"] = malicious_name
    saved = client.post("/customer/identity", data=form, follow_redirects=False)
    assert saved.status_code == 303
    rendered = client.get(saved.headers["location"])
    assert malicious_name not in rendered.text
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in rendered.text
    assert "<script" not in rendered.text.casefold()
    assert "<style" not in rendered.text.casefold()
    assert "style=" not in rendered.text.casefold()
    assert "<img" not in rendered.text.casefold()
    assert 'type="file"' in rendered.text
    assert 'accept="image/*"' in rendered.text
    assert 'capture="environment"' in rendered.text
    assert rendered.headers["content-security-policy"]
    assert rendered.headers["cache-control"] == "no-store"

    oversized = client.post(
        "/customer/identity/document",
        content=b"x",
        headers={
            "Content-Type": "multipart/form-data; boundary=m10",
            "Content-Length": str(settings.object_storage_max_multipart_bytes + 1),
        },
        follow_redirects=False,
    )
    assert oversized.status_code == 413
    assert oversized.headers["x-error-code"] == ErrorCode.FILE_TOO_LARGE.value
    assert oversized.headers["cache-control"] == "no-store"
    assert storage.calls == ()

    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert "max-width: 100%" in css
    assert "min-height: 44px" in css
    assert "overflow-wrap: anywhere" in css


def _assert_no_checked_out_connection(engine: Engine) -> None:
    checked_out = getattr(engine.pool, "checkedout", None)
    if callable(checked_out):
        assert checked_out() == 0
