import re
from pathlib import Path

OFFER_TEMPLATE_DIR = Path("app/templates/offers")
BASE_TEMPLATE = Path("app/templates/base.html")
STYLESHEET = Path("app/static/css/app.css")


def _offer_templates() -> tuple[Path, ...]:
    return tuple(sorted(OFFER_TEMPLATE_DIR.glob("*.html")))


def test_offer_templates_use_viewport_autoescape_and_no_inline_code() -> None:
    base = BASE_TEMPLATE.read_text(encoding="utf-8")
    assert 'name="viewport"' in base
    assert "width=device-width" in base

    for template in _offer_templates():
        source = template.read_text(encoding="utf-8")
        normalized = source.casefold()
        assert "|safe" not in normalized
        assert "<script" not in normalized
        assert "<style" not in normalized
        assert " style=" not in normalized
        assert " onclick=" not in normalized
        assert " onsubmit=" not in normalized


def test_offer_visible_form_controls_have_explicit_labels() -> None:
    sources = "\n".join(
        template.read_text(encoding="utf-8") for template in _offer_templates()
    )
    control_ids = re.findall(
        r"<(?:input|select|textarea)\b"
        r"(?=[^>]*\bid=\"([^\"]+)\")"
        r"(?![^>]*\btype=\"hidden\")[^>]*>",
        sources,
    )

    assert control_ids
    for control_id in control_ids:
        assert f'<label for="{control_id}">' in sources


def test_offer_css_has_narrow_viewport_touch_focus_and_long_text_contracts() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")

    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 320px)" in css
    assert ".offer-admin-page,\n    .registration-offer-page" in css
    assert "width: min(100% - 16px, 640px);" in css
    assert ".offer-language-nav a" in css
    assert "min-inline-size: 0;" in css
    assert "min-height: 44px;" in css
    assert ".offer-admin-page textarea:focus-visible" in css
    assert "outline: 3px solid #f59e0b;" in css
    assert ".offer-legal-body" in css
    assert "max-width: 70ch;" in css
    assert "white-space: pre-wrap;" in css
    assert "overflow-wrap: anywhere;" in css
