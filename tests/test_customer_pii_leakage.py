import re
from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-only-customer-pii-leakage-hmac-key"
TEST_PASSWORD = "NotARealSecret123"
TEST_PHONE = "+998901234603"
PII_NAME_MARKERS = {
    "phone",
    "name",
    "fio",
    "fish",
    "jshshir",
    "pinfl",
    "passport",
    "document",
    "telegram",
    "offer",
    "shop",
}
INTERNAL_FIELD_NAMES = {
    "password_hash",
    "token_hash",
    "csrf_secret",
    "session_token",
}
FALSE_STATUS_PATTERNS = (
    r"\bactive\b",
    r"\bactivated\b",
    r"\bverified\b",
    r"\btasdiqlangan\s+(?:customer|mijoz)\b",
    r"\bfaol\s+(?:customer|mijoz)\b",
)
SQL_LEAK_PATTERNS = (
    r"\bselect\b[\s\S]*?\bfrom\s+(?:customers|users|sessions)\b",
    r"\binsert\s+into\s+(?:customers|users|sessions)\b",
    r"\bupdate\s+(?:customers|users|sessions)\b",
    r"\bdelete\s+from\s+(?:customers|users|sessions)\b",
)
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
MASKED_PHONE_PATTERN = re.compile(r"\+998[0-9*]{9}")


class CustomerHtmlInspection(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.named_attributes: list[str] = []
        self.inputs: list[dict[str, str | None]] = []
        self.text_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if name := attributes.get("name"):
            self.named_attributes.append(name)
        if tag == "input":
            self.inputs.append(attributes)

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)

    @property
    def visible_text(self) -> str:
        return " ".join(self.text_parts)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_client(
    engine: Engine,
    now: datetime,
) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application), settings


def commit_user(db_session: Session) -> User:
    result = create_user(db_session, TEST_PHONE, TEST_PASSWORD)
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-customer-pii-leakage",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def set_client_session_cookie(
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


def inspect_html(html: str) -> CustomerHtmlInspection:
    inspection = CustomerHtmlInspection()
    inspection.feed(html)
    inspection.close()
    return inspection


def csrf_form_value(inspection: CustomerHtmlInspection) -> str:
    matching_inputs = [
        input_attributes
        for input_attributes in inspection.inputs
        if input_attributes.get("name") == "csrf_token"
    ]
    assert len(matching_inputs) == 1
    value = matching_inputs[0].get("value")
    assert value is not None
    return value


def assert_safe_customer_html(
    html: str,
    *,
    user: User,
    created: CreatedSession,
    customer: Customer | None,
    allowed_name_attributes: set[str],
    expected_masked_phone: str | None,
) -> CustomerHtmlInspection:
    inspection = inspect_html(html)
    decoded_html = unescape(html)
    normalized_html = decoded_html.casefold()
    visible_text = inspection.visible_text

    forbidden_values = {
        user.phone,
        user.password_hash,
        created.raw_token.as_cookie_value(),
        created.session.token_hash,
        created.session.csrf_secret,
        str(user.id),
        str(created.session.id),
    }
    if customer is not None:
        forbidden_values.add(str(customer.id))

    for forbidden_value in forbidden_values:
        assert forbidden_value is not None
        assert forbidden_value not in decoded_html

    assert UUID_PATTERN.search(decoded_html) is None
    input_names = {
        input_attributes["name"]
        for input_attributes in inspection.inputs
        if input_attributes.get("name") is not None
    }
    assert input_names == allowed_name_attributes
    for attribute_name in inspection.named_attributes:
        normalized_name = attribute_name.casefold().replace("-", "_")
        assert all(marker not in normalized_name for marker in PII_NAME_MARKERS)

    for internal_field_name in INTERNAL_FIELD_NAMES:
        assert internal_field_name not in normalized_html
    for orm_object in (user, created.session, customer):
        if orm_object is not None:
            assert repr(orm_object) not in decoded_html
    for orm_marker in (
        "app.auth.models.user object at",
        "app.auth.models.session object at",
        "app.customer.models.customer object at",
        "sqlalchemy.",
    ):
        assert orm_marker not in normalized_html
    for sql_pattern in SQL_LEAK_PATTERNS:
        assert re.search(sql_pattern, decoded_html, re.IGNORECASE) is None
    for false_status_pattern in FALSE_STATUS_PATTERNS:
        assert re.search(false_status_pattern, visible_text, re.IGNORECASE) is None

    displayed_phones = MASKED_PHONE_PATTERN.findall(decoded_html)
    if expected_masked_phone is None:
        assert displayed_phones == []
    else:
        assert displayed_phones == [expected_masked_phone]

    return inspection


def test_customer_onboarding_and_profile_html_are_pii_and_secret_free(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 45, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    not_started = client.get("/customer/onboarding")

    assert not_started.status_code == 200
    not_started_inspection = assert_safe_customer_html(
        not_started.text,
        user=user,
        created=created,
        customer=None,
        allowed_name_attributes={"csrf_token"},
        expected_masked_phone=None,
    )
    csrf_token = csrf_form_value(not_started_inspection)
    assert csrf_token == get_csrf_token(created.session).as_form_value()

    start_response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert start_response.status_code == 303
    assert start_response.headers["location"] == "/customer/profile"
    customer = db_session.scalar(
        select(Customer).where(Customer.user_id == user.id),
    )
    assert customer is not None

    existing_draft = client.get("/customer/onboarding")
    profile = client.get("/customer/profile")

    assert existing_draft.status_code == 200
    assert profile.status_code == 200
    assert_safe_customer_html(
        existing_draft.text,
        user=user,
        created=created,
        customer=customer,
        allowed_name_attributes=set(),
        expected_masked_phone=None,
    )
    assert_safe_customer_html(
        profile.text,
        user=user,
        created=created,
        customer=customer,
        allowed_name_attributes=set(),
        expected_masked_phone=mask_phone_for_display(user.phone),
    )
