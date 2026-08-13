from pathlib import Path

from app.debt.admin_write_off_presentation import ADMIN_WRITE_OFF_COPY
from app.debt.contracts import WriteOffReason
from app.debt.enums import DebtStatus
from app.debt.presentation import DebtWebLanguage
from app.payment.presentation import get_payment_web_copy

ADMIN_TEMPLATES = (
    Path("app/templates/debt/admin_write_off_candidates.html"),
    Path("app/templates/debt/admin_write_off_detail.html"),
)
RECOVERY_TEMPLATES = tuple(
    Path("app/templates/payment") / name
    for name in (
        "shop_list.html",
        "shop_new.html",
        "shop_receipt.html",
        "customer_list.html",
        "customer_receipt.html",
    )
)


def test_uz_and_ru_copy_close_five_reasons_two_statuses_and_generic_errors() -> None:
    for language in DebtWebLanguage:
        copy = ADMIN_WRITE_OFF_COPY[language]
        assert tuple(copy.reason_labels) == tuple(WriteOffReason)
        assert len(set(copy.reason_labels.values())) == 5
        assert all(label.strip() for label in copy.reason_labels.values())
        assert copy.status_labels[DebtStatus.WRITTEN_OFF].strip()
        assert copy.status_labels[DebtStatus.WRITTEN_OFF_SETTLED].strip()
        assert copy.error_heading.endswith(":")
        assert copy.generic_error.strip()

        payment_copy = get_payment_web_copy(language)
        assert payment_copy["status_written_off"].strip()
        assert payment_copy["status_written_off_settled"].strip()
        assert payment_copy["recovery_terms"].strip()
        assert payment_copy["customer_payment_unavailable"].strip()


def test_admin_form_and_result_are_semantic_keyboard_native_and_textual() -> None:
    detail = ADMIN_TEMPLATES[1].read_text(encoding="utf-8")
    assert '<label for="reason">' in detail
    assert '<select id="reason" name="reason" required>' in detail
    assert 'id="confirmed" name="confirmed" type="checkbox"' in detail
    assert '<label for="confirmed">' in detail
    assert 'class="button-danger" type="submit"' in detail
    assert '<section aria-labelledby="write-off-result-heading">' in detail
    assert 'id="write-off-result-heading"' in detail
    assert "copy.status_labels" in detail
    assert "copy.reason_labels[completed.reason]" in detail
    assert 'autocomplete="off"' in detail


def test_m17_pages_are_mobile_bounded_focusable_and_not_color_only() -> None:
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*ADMIN_TEMPLATES, *RECOVERY_TEMPLATES)
    )
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in base
    assert "width: min(100% - 32px, 640px);" in css
    assert ".write-off-admin-page" in css and "overflow-wrap: anywhere;" in css
    assert ".write-off-action-link" in css and "min-height: 44px;" in css
    assert "input:focus-visible" in css
    assert "button:focus-visible" in css
    assert "select:focus-visible" in css
    assert "a:focus-visible" in css
    assert "min-width: 320px" not in css
    assert "min-width: 430px" not in css
    assert "copy.status_labels" in templates or "status_label" in templates
    assert 'role="status"' in templates


def test_m17_templates_have_no_active_content_or_browser_authority() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*ADMIN_TEMPLATES, *RECOVERY_TEMPLATES)
    ).casefold()
    for forbidden in (
        "<script",
        "<style",
        "javascript:",
        "onclick=",
        "onchange=",
        "localstorage",
        "sessionstorage",
        "cachestorage",
        "date.now",
        "new date(",
        "fetch(",
        "xmlhttprequest",
    ):
        assert forbidden not in sources


def test_shop_customer_surfaces_have_no_admin_reason_or_rating_channel() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            *RECOVERY_TEMPLATES,
            Path("app/templates/debt/shop_list.html"),
            Path("app/templates/debt/shop_detail.html"),
            Path("app/templates/debt/customer_list.html"),
            Path("app/templates/debt/customer_detail.html"),
        )
    ).casefold()
    for forbidden in (
        "written_off_reason",
        "written_off_actor",
        "collection_exhausted",
        "customer_unreachable",
        "insolvency_or_deceased",
        "legal_or_compliance",
        "fraud_or_abuse",
        "ratingevent",
        "rating_score",
        "block_cause",
    ):
        assert forbidden not in sources


def test_m17_security_headers_cover_every_new_and_extended_surface() -> None:
    source = Path("app/security_headers.py").read_text(encoding="utf-8")
    assert 'M17_NO_STORE_PATH_PREFIXES: Final = ("/admin/debts",)' in source
    assert '"/shop/debts"' in source
    assert '"/shop/payments"' in source
    assert '"/customer/debts"' in source
    assert '"/customer/payments"' in source
    assert "script-src 'self'" in source
    assert "form-action 'self'" in source
    assert "object-src 'none'" in source
