from pathlib import Path

from jinja2 import Environment, select_autoescape

from app.auth.models import Session as AuthSession
from app.auth.template_context import (
    get_csrf_template_context,
    with_csrf_context,
)

CSRF_INPUT_TEMPLATE = (
    '<input type="hidden" name="csrf_token" value="{{ csrf_token }}">'
)


def render_csrf_input(context: dict[str, str | None]) -> str:
    environment = Environment(
        autoescape=select_autoescape(default=True),
    )
    template = environment.from_string(CSRF_INPUT_TEMPLATE)
    return template.render(context)


def test_csrf_template_context_returns_token_for_session() -> None:
    session = AuthSession(csrf_secret="csrf-secret-for-template-test")

    context = get_csrf_template_context(session)

    assert context["csrf_token"]
    assert isinstance(context["csrf_token"], str)


def test_csrf_template_context_returns_none_without_session() -> None:
    assert get_csrf_template_context(None) == {"csrf_token": None}


def test_jinja_hidden_field_renders_csrf_token_with_autoescape() -> None:
    raw_token = 'token-with-"quote"-and-<tag>'
    context = {"csrf_token": raw_token}

    rendered = render_csrf_input(context)

    assert 'type="hidden"' in rendered
    assert 'name="csrf_token"' in rendered
    assert raw_token not in rendered
    assert "&#34;quote&#34;" in rendered
    assert "&lt;tag&gt;" in rendered


def test_csrf_template_does_not_use_safe_filter() -> None:
    assert "|safe" not in CSRF_INPUT_TEMPLATE


def test_with_csrf_context_only_adds_token_to_given_form_context() -> None:
    session = AuthSession(csrf_secret="csrf-secret-for-specific-form")

    context = with_csrf_context({"form_name": "login"}, session)

    assert context["form_name"] == "login"
    assert context["csrf_token"]


def test_base_template_has_no_global_csrf_secret_or_safe_filter() -> None:
    base_template = Path("app/templates/base.html").read_text(encoding="utf-8")

    assert "csrf_secret" not in base_template
    assert "csrf_token" not in base_template
    assert "|safe" not in base_template
