from __future__ import annotations

import re
from pathlib import Path

from app.debt.presentation import DebtWebLanguage
from app.debt.web_presentation import COPY as DEBT_COPY
from app.payment.presentation import PAYMENT_WEB_COPY
from app.security_headers import CONTENT_SECURITY_POLICY

PAYMENT_TEMPLATE_DIR = Path("app/templates/payment")
DEBT_TEMPLATE_DIR = Path("app/templates/debt")
M15_TEMPLATES = (
    *(
        PAYMENT_TEMPLATE_DIR / name
        for name in (
            "shop_list.html",
            "shop_new.html",
            "shop_receipt.html",
            "customer_list.html",
            "customer_receipt.html",
        )
    ),
    *(
        DEBT_TEMPLATE_DIR / name
        for name in (
            "shop_list.html",
            "shop_detail.html",
            "customer_list.html",
            "customer_detail.html",
        )
    ),
)


def test_m15_overdue_copy_and_refresh_action_are_complete_in_both_locales() -> None:
    expected = {
        DebtWebLanguage.UZ_LATN: {
            "status_overdue": "Muddati o'tgan",
            "original_basis": "Asl summa bo'yicha hisob",
            "current_balance": "Hozirgi qoldiq",
            "refresh": "Holatni yangilash",
        },
        DebtWebLanguage.RU: {
            "status_overdue": "Срок оплаты истёк",
            "original_basis": "Расчёт по первоначальной сумме",
            "current_balance": "Текущий остаток",
            "refresh": "Обновить данные",
        },
    }

    for language, values in expected.items():
        for key, value in values.items():
            assert PAYMENT_WEB_COPY[language][key] == value
            assert DEBT_COPY[language][key] == value
        assert PAYMENT_WEB_COPY[language]["historical_balance"].strip()
        assert PAYMENT_WEB_COPY[language]["late_terms"].strip()
        assert DEBT_COPY[language]["clawback"].strip()

    for template in M15_TEMPLATES:
        source = template.read_text(encoding="utf-8")
        assert 'href="{{ request.url.path }}"' in source
        assert "copy.refresh" in source
        assert 'aria-label="{{ copy.navigation }}"' in source


def test_m15_templates_rely_on_autoescape_and_have_no_browser_authority() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in M15_TEMPLATES)
    folded = combined.casefold()

    assert "|safe" not in folded
    assert "autoescape false" not in folded
    assert "<script" not in folded
    assert "<style" not in folded
    assert not re.search(r"\son[a-z]+\s*=", folded)
    for forbidden in (
        "localstorage",
        "sessionstorage",
        "cachestorage",
        "caches.",
        "indexeddb",
        "serviceworker",
        "new date(",
        "date.now(",
        "math.",
        "parseint(",
        "parsefloat(",
        "data-balance",
        "data-amount",
    ):
        assert forbidden not in folded


def test_m15_semantics_mobile_focus_and_csp_are_static_contracts() -> None:
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    receipts = "\n".join(
        (PAYMENT_TEMPLATE_DIR / name).read_text(encoding="utf-8")
        for name in ("shop_receipt.html", "customer_receipt.html")
    )

    assert (
        '<meta name="viewport" content="width=device-width, initial-scale=1">' in base
    )
    assert "<script" not in base.casefold()
    assert '<time datetime="{{ receipt.created_at.isoformat() }}">' in receipts
    assert "<dl>" in receipts
    assert 'aria-label="{{ copy.history }}"' in (
        PAYMENT_TEMPLATE_DIR / "shop_list.html"
    ).read_text(encoding="utf-8")
    assert 'aria-label="{{ copy.debts }}"' in (
        DEBT_TEMPLATE_DIR / "customer_list.html"
    ).read_text(encoding="utf-8")
    assert ".debt-page nav a" in css
    assert ".debt-card h2 a" in css
    assert ".payment-page nav a" in css
    assert "min-width: 44px" in css
    assert "min-height: 44px" in css
    assert "overflow-wrap: anywhere" in css
    assert "min-inline-size: 0" in css
    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 320px)" in css
    assert "input:focus-visible" in css
    assert "button:focus-visible" in css
    assert "a:focus-visible" in css

    assert "default-src 'self'" in CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY
    assert "https:" not in CONTENT_SECURITY_POLICY
    assert "http:" not in CONTENT_SECURITY_POLICY
