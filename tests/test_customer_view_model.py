from dataclasses import fields
from uuid import uuid4

from app.auth.phone import mask_phone_for_display
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer.view_model import build_customer_draft_view


def test_customer_draft_view_masks_phone_and_displays_status() -> None:
    customer = Customer(
        id=uuid4(),
        user_id=uuid4(),
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )
    raw_phone = "+998901234567"

    view = build_customer_draft_view(customer, raw_phone)

    assert view.masked_phone == mask_phone_for_display(raw_phone)
    assert view.onboarding_status_display == "draft"
    assert raw_phone not in repr(view)


def test_customer_draft_view_exposes_only_safe_template_fields() -> None:
    customer_id = uuid4()
    user_id = uuid4()
    customer = Customer(
        id=customer_id,
        user_id=user_id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )

    view = build_customer_draft_view(customer, "+998901234567")

    assert {field.name for field in fields(view)} == {
        "masked_phone",
        "onboarding_status_display",
    }
    assert not hasattr(view, "__dict__")
    assert str(customer_id) not in repr(view)
    assert str(user_id) not in repr(view)


def test_customer_draft_view_uses_safe_status_fallback() -> None:
    customer = Customer(
        id=uuid4(),
        user_id=uuid4(),
        onboarding_status="active",
    )

    view = build_customer_draft_view(customer, "+998901234567")

    assert view.onboarding_status_display == "unknown"
