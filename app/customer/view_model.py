from dataclasses import dataclass
from typing import Final

from app.auth.phone import mask_phone_for_display
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer

CUSTOMER_DRAFT_STATUS_DISPLAY: Final = "draft"
UNKNOWN_CUSTOMER_STATUS_DISPLAY: Final = "unknown"


@dataclass(frozen=True, slots=True)
class CustomerDraftView:
    masked_phone: str
    onboarding_status_display: str


def build_customer_draft_view(customer: Customer, user_phone: str) -> CustomerDraftView:
    return CustomerDraftView(
        masked_phone=mask_phone_for_display(user_phone),
        onboarding_status_display=_get_onboarding_status_display(
            customer.onboarding_status,
        ),
    )


def _get_onboarding_status_display(status: str) -> str:
    if status == CUSTOMER_ONBOARDING_STATUS_DRAFT:
        return CUSTOMER_DRAFT_STATUS_DISPLAY
    return UNKNOWN_CUSTOMER_STATUS_DISPLAY
