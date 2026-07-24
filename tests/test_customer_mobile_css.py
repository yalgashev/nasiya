from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.customer.view_model import CustomerDraftView

CSS_PATH = Path("app/static/css/app.css")
TEMPLATES_DIR = Path("app/templates")


def render_template(template_name: str, **context: object) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(
            enabled_extensions=("html", "xml"),
            default=True,
        ),
    )
    environment.globals["url_for"] = static_url_for
    return environment.get_template(template_name).render(**context)


def static_url_for(name: str, **params: str) -> str:
    assert name == "static"
    return f"/static/{params['path']}"


def test_customer_css_has_mobile_first_touch_and_wrap_rules() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")

    assert ".customer-page" in css
    assert "max-width: 100%;" in css
    assert "overflow-wrap: anywhere;" in css
    assert ".customer-actions" in css
    assert ".customer-actions a" in css
    assert "min-height: 44px;" in css
    assert ".customer-page a:focus-visible" in css
    assert ".customer-page button:focus-visible" in css
    assert ".customer-status" in css
    assert "@media (max-width: 430px)" in css
    assert "width: min(100% - 24px, 640px);" in css
    assert "100vw" not in css
    assert "overflow-x: scroll" not in css
    assert "overflow-x: auto" not in css
    assert "@import" not in css
    assert "@font-face" not in css
    assert "animation:" not in css
    assert "@keyframes" not in css


def test_customer_templates_use_mobile_classes_and_text_status() -> None:
    onboarding_not_started = render_template(
        "customer/onboarding.html",
        customer_state=None,
        csrf_token="csrf-token",
    )
    onboarding_existing = render_template(
        "customer/onboarding.html",
        customer_state=CustomerDraftView(
            masked_phone="+998*******67",
            onboarding_status_display="draft",
        ),
        csrf_token="csrf-token",
    )
    profile = render_template(
        "customer/profile.html",
        customer_state=CustomerDraftView(
            masked_phone="+998*******67",
            onboarding_status_display="draft",
        ),
    )

    for rendered in (onboarding_not_started, onboarding_existing, profile):
        assert '<main class="customer-page">' in rendered
        assert "<script" not in rendered.casefold()
        assert "<style" not in rendered.casefold()
        assert " style=" not in rendered.casefold()
        assert "customer_id" not in rendered
        assert "user_id" not in rendered

    assert 'class="customer-actions"' in onboarding_not_started
    assert 'class="customer-actions"' in profile
    assert 'class="customer-status"' in onboarding_existing
    assert 'class="customer-status"' in profile
    assert "Status:" in onboarding_existing
    assert "Qoralama" in onboarding_existing
    assert "Holat:" in profile
    assert "draft" in profile


def test_m1_and_m2_templates_still_render_with_existing_css_link() -> None:
    home = render_template("home.html")
    account = render_template(
        "auth/account.html",
        masked_phone="+998*******67",
        csrf_token="csrf-token",
    )

    assert 'href="/static/css/app.css"' in home
    assert 'href="/static/css/app.css"' in account
    assert "Nasiya" in home
    assert "Hisob" in account
    assert 'href="/auth/sessions"' in account
    assert 'href="/customer/onboarding"' in account
    assert '<form method="post" action="/auth/logout">' in account
