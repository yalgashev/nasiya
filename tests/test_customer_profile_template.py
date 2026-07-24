from html import unescape
from pathlib import Path
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.auth.phone import mask_phone_for_display
from app.customer.view_model import CustomerDraftView

TEMPLATES_DIR = Path("app/templates")
PROFILE_TEMPLATE_PATH = Path("app/templates/customer/profile.html")
FORBIDDEN_PROFILE_SCOPE_TEXT = (
    "active",
    "verified",
    "tasdiqlangan",
    "f.i.sh",
    "jshshir",
    "passport",
    "pasport",
    "document",
    "hujjat",
    "telegram",
    "otp",
    "offer",
    "shop",
    "debt",
    "qarz",
    "progress",
    "customer_id",
    "user_id",
)


def render_profile(customer_state: CustomerDraftView) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(
            enabled_extensions=("html", "xml"),
            default=True,
        ),
    )
    environment.globals["url_for"] = static_url_for
    template = environment.get_template("customer/profile.html")
    return template.render(customer_state=customer_state)


def static_url_for(name: str, **params: str) -> str:
    assert name == "static"
    return f"/static/{params['path']}"


def test_customer_profile_template_renders_safe_draft_profile() -> None:
    raw_phone = "+998901234567"
    customer_id = uuid4()
    user_id = uuid4()
    customer_state = CustomerDraftView(
        masked_phone=mask_phone_for_display(raw_phone),
        onboarding_status_display="draft",
    )

    rendered = render_profile(customer_state)
    visible_html = unescape(rendered)

    assert "Mijoz profili" in visible_html
    assert "Draft ma'lumoti" in visible_html
    assert f"Telefon: {customer_state.masked_phone}" in visible_html
    assert "Holat: draft" in visible_html
    assert 'href="/customer/onboarding"' in rendered
    assert 'href="/auth/account"' in rendered
    assert raw_phone not in visible_html
    assert str(customer_id) not in visible_html
    assert str(user_id) not in visible_html
    assert "<form" not in rendered.casefold()
    assert "<input" not in rendered.casefold()
    assert "%" not in visible_html
    assert_forbidden_profile_scope_absent(rendered)
    assert_no_inline_script_or_style(rendered)


def test_customer_profile_template_autoescapes_view_fields() -> None:
    customer_state = CustomerDraftView(
        masked_phone='masked-"phone"-<tag>',
        onboarding_status_display="draft",
    )

    rendered = render_profile(customer_state)

    assert customer_state.masked_phone not in rendered
    assert "masked-&#34;phone&#34;-&lt;tag&gt;" in rendered
    assert "|safe" not in PROFILE_TEMPLATE_PATH.read_text(encoding="utf-8")


def assert_forbidden_profile_scope_absent(html: str) -> None:
    normalized_html = unescape(html).casefold()
    for forbidden_text in FORBIDDEN_PROFILE_SCOPE_TEXT:
        assert forbidden_text.casefold() not in normalized_html


def assert_no_inline_script_or_style(html: str) -> None:
    normalized_html = html.casefold()
    assert "<script" not in normalized_html
    assert "<style" not in normalized_html
    assert " style=" not in normalized_html
    assert " onclick=" not in normalized_html
    assert " onsubmit=" not in normalized_html
