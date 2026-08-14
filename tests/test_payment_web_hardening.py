from __future__ import annotations

import re
from pathlib import Path

PAYMENT_TEMPLATE_DIR = Path("app/templates/payment")
PAYMENT_TEMPLATES = tuple(sorted(PAYMENT_TEMPLATE_DIR.glob("*.html")))
PAYMENT_CSS = Path("app/static/css/app.css")


def test_payment_templates_have_no_client_financial_authority_or_inline_code() -> None:
    assert {path.name for path in PAYMENT_TEMPLATES} == {
        "customer_list.html",
        "customer_receipt.html",
        "shop_list.html",
        "shop_new.html",
        "shop_receipt.html",
        "shop_void.html",
    }
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PAYMENT_TEMPLATES)
    folded = combined.casefold()

    assert "<script" not in folded
    assert "<style" not in folded
    assert not re.search(r"\son[a-z]+\s*=", folded)
    assert "localstorage" not in folded
    assert "sessionstorage" not in folded
    assert "serviceworker" not in folded
    assert "navigator.serviceworker" not in folded
    assert "data-balance" not in folded
    assert "data-amount" not in folded
    assert "recorded_by" not in folded
    assert "customer_id" not in folded
    assert "user_id" not in folded
    assert "key/hash" not in folded


def test_payment_form_is_labelled_server_idempotent_and_error_associated() -> None:
    source = (PAYMENT_TEMPLATE_DIR / "shop_new.html").read_text(encoding="utf-8")

    assert '<form action="/shop/debts/{{ debt_id }}/payments" method="post"' in source
    assert 'name="csrf_token"' in source
    assert 'name="idempotency_key"' in source
    assert 'name="expected_revision"' in source
    assert '<label for="payment-amount">' in source
    assert '<label for="payment-method">' in source
    assert 'inputmode="numeric"' in source
    assert 'pattern="[0-9]+"' in source
    assert 'aria-live="assertive"' in source
    assert 'aria-describedby="payment-balance payment-form-error"' in source
    assert "aria-invalid=" in source
    assert '<button type="submit">' in source


def test_void_form_is_semantic_confirmed_and_has_no_client_authority() -> None:
    source = (PAYMENT_TEMPLATE_DIR / "shop_void.html").read_text(encoding="utf-8")
    folded = source.casefold()

    assert 'action="/shop/payments/{{ payment_id }}/void"' in source
    assert 'autocomplete="off"' in source
    assert 'name="csrf_token"' in source
    assert 'name="idempotency_key"' in source
    assert 'name="expected_revision"' in source
    assert '<label for="payment-void-reason">' in source
    assert '<select id="payment-void-reason" name="reason"' in source
    assert "<fieldset>" in source and "<legend>" in source
    assert 'name="confirmation" value="yes" required' in source
    assert 'role="alert" aria-live="assertive"' in source
    assert 'class="button-danger" type="submit"' in source
    for forbidden in (
        "<script",
        "datetime.now",
        "date.now",
        "amount_uzs",
        "current_shop_id",
        "actor_user_id",
        "request_hash",
        "key_digest",
    ):
        assert forbidden not in folded


def test_payment_mobile_touch_focus_overflow_and_static_asset_budget() -> None:
    css = PAYMENT_CSS.read_text(encoding="utf-8")

    assert PAYMENT_CSS.stat().st_size <= 16 * 1024
    assert ".payment-page nav a" in css
    assert ".payment-page li a" in css
    assert ".destructive-confirmation" in css
    assert "grid-template-columns: 44px minmax(0, 1fr)" in css
    assert "min-width: 44px" in css
    assert "min-height: 44px" in css
    assert "overflow-wrap: anywhere" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 320px)" in css
    assert "input:focus-visible" in css
    assert "a:focus-visible" in css
    assert "outline: 3px solid #f59e0b" in css
    assert "color: #111827" in css
    assert "background: #f8fafc" in css


def test_payment_views_have_empty_states_semantic_times_and_safe_navigation() -> None:
    list_sources = [
        (PAYMENT_TEMPLATE_DIR / name).read_text(encoding="utf-8")
        for name in ("shop_list.html", "customer_list.html")
    ]
    receipt_sources = [
        (PAYMENT_TEMPLATE_DIR / name).read_text(encoding="utf-8")
        for name in ("shop_receipt.html", "customer_receipt.html")
    ]

    for source in list_sources:
        assert "{% else %}<p>{{ copy.empty }}</p>{% endif %}" in source
        assert '<time datetime="{{ payment.created_at.isoformat() }}">' in source
        assert "<nav aria-label=" in source
    for source in receipt_sources:
        assert "copy.historical_balance" in source
        assert "copy.current_balance" in source
        assert "copy.current_status" in source
        assert "<nav aria-label=" in source


def test_shop_history_reason_is_not_available_to_customer_templates() -> None:
    shop_list = (PAYMENT_TEMPLATE_DIR / "shop_list.html").read_text(encoding="utf-8")
    customer_sources = "\n".join(
        (PAYMENT_TEMPLATE_DIR / name).read_text(encoding="utf-8")
        for name in ("customer_list.html", "customer_receipt.html")
    )

    assert "void_state.reason_label" in shop_list
    assert "reason_label" not in customer_sources
    assert "void_reason" not in customer_sources
    assert "recorded_by" not in customer_sources
    assert "actor" not in customer_sources
