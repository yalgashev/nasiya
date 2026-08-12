from pathlib import Path

from app.security_headers import (
    CONTENT_SECURITY_POLICY,
    M9_NO_STORE_PATH_PREFIXES,
    M12_NO_STORE_PATH_PREFIXES,
    M14_NO_STORE_PATH_PREFIXES,
    M16_NO_STORE_PATH_PREFIXES,
    SENSITIVE_NO_STORE_PATH_PREFIXES,
)


def test_sensitive_no_store_scope_is_exact_and_csp_forbids_inline_code() -> None:
    assert M9_NO_STORE_PATH_PREFIXES == (
        "/admin/offers",
        "/auth/registration-offer",
        "/customer/activation",
        "/customer/identity",
    )
    assert M12_NO_STORE_PATH_PREFIXES == (
        "/shop/customers",
        "/shop/settings/credit",
        "/customer/shops",
    )
    assert M14_NO_STORE_PATH_PREFIXES == (
        "/shop/debts",
        "/shop/payments",
        "/customer/debts",
        "/customer/payments",
    )
    assert M16_NO_STORE_PATH_PREFIXES == ("/shop/risk-band-disclosures",)
    assert SENSITIVE_NO_STORE_PATH_PREFIXES == (
        *M9_NO_STORE_PATH_PREFIXES,
        *M12_NO_STORE_PATH_PREFIXES,
        *M14_NO_STORE_PATH_PREFIXES,
        *M16_NO_STORE_PATH_PREFIXES,
    )
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY
    assert "style-src 'self'" in CONTENT_SECURITY_POLICY
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert "object-src 'none'" in CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY
    assert "form-action 'self'" in CONTENT_SECURITY_POLICY


def test_every_offer_template_relies_on_autoescape_and_external_css() -> None:
    for template in Path("app/templates/offers").glob("*.html"):
        source = template.read_text(encoding="utf-8").casefold()
        assert "|safe" not in source
        assert "<script" not in source
        assert "<style" not in source
        assert " style=" not in source
        assert " onerror=" not in source
        assert " onclick=" not in source
