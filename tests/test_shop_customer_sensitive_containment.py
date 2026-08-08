from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHOP_CUSTOMER = PROJECT_ROOT / "app" / "shop_customer"


def test_m12_production_has_no_pii_crypto_or_browser_storage_surface() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SHOP_CUSTOMER.glob("*.py"))
    )
    lowered = source.casefold()
    for forbidden in (
        "decrypt_identity",
        "jshshir",
        "document_number",
        "ciphertext",
        "blind_index",
        "presigned",
        "localstorage",
        "sessionstorage",
        "indexeddb",
    ):
        assert forbidden not in lowered


def test_redirects_are_fixed_markers_and_never_interpolate_form_material() -> None:
    router = (SHOP_CUSTOMER / "router.py").read_text(encoding="utf-8")
    redirect = router[
        router.index("def _redirect(") : router.index("def _redirect_login(")
    ]
    assert "phone" not in redirect
    assert "credit_limit" not in redirect
    assert "max_open_debts" not in redirect
    assert "list_status" not in redirect
    assert "marker = error.value if error is not None else notice" in redirect


def test_authorized_locator_is_confined_to_tenant_scoped_actions() -> None:
    template = (
        PROJECT_ROOT / "app" / "templates" / "shop_customer" / "roster.html"
    ).read_text(encoding="utf-8")
    assert template.count("row.locator") == 2
    assert 'action="/shop/customers/{{ row.locator }}/policy"' in template
    assert 'href="/shop/customers/{{ row.locator }}/debts"' in template
    remaining = template.replace(
        'action="/shop/customers/{{ row.locator }}/policy"', ""
    ).replace('href="/shop/customers/{{ row.locator }}/debts"', "")
    assert "{{ row.locator }}" not in remaining
