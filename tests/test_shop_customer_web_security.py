from dataclasses import fields
from pathlib import Path

from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.presentation import (
    ShopCustomerWebLanguage,
    get_shop_customer_web_copy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = PROJECT_ROOT / "app" / "templates" / "shop_customer"
CSS_PATH = PROJECT_ROOT / "app" / "static" / "css" / "app.css"
ROUTER_PATH = PROJECT_ROOT / "app" / "shop_customer" / "router.py"


def _template_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(TEMPLATE_DIR.glob("*.html"))
    )


def test_every_m12_page_has_complete_uz_latn_and_ru_copy() -> None:
    for language in ShopCustomerWebLanguage:
        copy = get_shop_customer_web_copy(language)
        assert all(
            isinstance(value, str) and value.strip()
            for value in (getattr(copy, field.name) for field in fields(copy))
        )
    ru = get_shop_customer_web_copy(ShopCustomerWebLanguage.RU)
    uz = get_shop_customer_web_copy(ShopCustomerWebLanguage.UZ_LATN)
    assert ru.read_only_notice != uz.read_only_notice
    assert {
        uz.normal_status,
        uz.whitelisted_status,
        uz.blacklisted_status,
    }
    assert tuple(ShopCustomerListStatus) == (
        ShopCustomerListStatus.NORMAL,
        ShopCustomerListStatus.WHITELISTED,
        ShopCustomerListStatus.BLACKLISTED,
    )


def test_templates_are_csp_mobile_and_browser_storage_safe() -> None:
    source = _template_source()
    lowered = source.casefold()
    for forbidden in (
        "<script",
        "style=",
        "onclick=",
        "onchange=",
        "localstorage",
        "sessionstorage",
        "indexeddb",
        "serviceworker",
    ):
        assert forbidden not in lowered
    assert 'name="csrf_token"' in source
    assert 'autocomplete="off"' in source
    assert 'role="alert"' in source
    assert 'role="status"' in source
    assert "<label" in source
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "@media (max-width: 430px)" in css
    assert "min-width: 0" in css
    assert ":focus-visible" in css


def test_router_never_imports_m10_decrypt_or_accepts_target_authority_ids() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "customer_identity" not in source
    assert "customer_document" not in source
    assert "app.storage" not in source
    link_signature = source[
        source.index("def link_shop_customer(") : source.index(
            "@router.get(SHOP_SETTINGS_CREDIT_PATH"
        )
    ]
    for forbidden in (
        "customer_id:",
        "user_id:",
        "shop_id:",
        "telegram_link_id:",
    ):
        assert forbidden not in link_signature
