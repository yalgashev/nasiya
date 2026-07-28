from hashlib import sha256
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_TEMPLATE = PROJECT_ROOT / "app/templates/auth/telegram.html"
BASE_TEMPLATE = PROJECT_ROOT / "app/templates/base.html"
APP_CSS = PROJECT_ROOT / "app/static/css/app.css"
HTMX_ASSET = PROJECT_ROOT / "app/static/vendor/htmx-2.0.4.min.js"
HTMX_LICENSE = PROJECT_ROOT / "app/static/vendor/HTMX-LICENSE.txt"
TELEGRAM_ACCOUNT_JS = PROJECT_ROOT / "app/static/js/telegram-account.js"


def test_telegram_template_has_local_script_and_no_inline_or_unsafe_rendering() -> None:
    template = TELEGRAM_TEMPLATE.read_text(encoding="utf-8")

    assert "static', path='vendor/htmx-2.0.4.min.js'" in template
    assert "static', path='js/telegram-account.js'" in template
    assert (
        '"historyEnabled":false,"historyCacheSize":0,'
        '"allowEval":false,"allowScriptTags":false'
    ) in template
    assert (
        '"responseHandling":[{"code":"204","swap":false},'
        '{"code":"[23]..","swap":true},'
        '{"code":"[4]..","swap":true,"error":true},'
        '{"code":"[5]..","swap":false,"error":true}]'
    ) in template
    assert "<script src=" in template
    assert "<script>" not in template
    assert "|safe" not in template
    assert "localStorage" not in template
    assert "sessionStorage" not in template
    assert "hx-push-url" not in template
    assert 'hx-disabled-elt="find button"' in template
    assert "data-clear-password-after-request" in template
    assert 'aria-live="polite"' in template


def test_base_template_uses_bounded_page_language() -> None:
    template = BASE_TEMPLATE.read_text(encoding="utf-8")

    assert "<html lang=\"{{ page_language|default('uz') }}\">" in template


def test_telegram_mobile_styles_cover_320_to_430_and_touch_targets() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 320px)" in css
    assert ".telegram-page" in css
    assert "min-height: 44px" in css
    assert ".telegram-page button:disabled" in css
    assert "focus-visible" in css


def test_vendored_htmx_asset_and_license_are_pinned() -> None:
    asset_digest = sha256(HTMX_ASSET.read_bytes()).hexdigest()
    license_text = HTMX_LICENSE.read_text(encoding="utf-8")

    assert asset_digest == (
        "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"
    )
    assert "Zero-Clause BSD" in license_text


def test_account_script_gates_external_link_and_clears_password_memory() -> None:
    source = TELEGRAM_ACCOUNT_JS.read_text(encoding="utf-8")

    assert "htmx:afterRequest" in source
    assert 'input[type="password"]' in source
    assert 'input.value = ""' in source
    assert "data-telegram-external-link" in source
    assert '"pointerdown"' in source
    assert '"keydown"' in source
    assert "event.isTrusted" in source
    assert "event.preventDefault()" in source
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "history.",
        "document.cookie",
        "fetch(",
        "XMLHttpRequest",
        "window.open",
        "location.href",
        "window.location",
        ".click(",
    ):
        assert forbidden not in source
