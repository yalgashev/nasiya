import pytest

from app.customer_activation.contracts import (
    TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
    CustomerLifecycleStatus,
    OrdinaryTelegramUnlinkOutcome,
    ProtectedActiveTelegramRelinkResult,
    ProtectedTelegramRelinkLinkDisposition,
    ProtectedTelegramRelinkOutcome,
    decide_ordinary_telegram_unlink,
)
from app.otp.contracts import OtpPurpose


@pytest.mark.parametrize(
    "customer_status",
    [None, CustomerLifecycleStatus.DRAFT],
)
def test_no_customer_and_draft_keep_inherited_unlink_contract(
    customer_status: CustomerLifecycleStatus | None,
) -> None:
    result = decide_ordinary_telegram_unlink(customer_status)

    assert result.outcome is OrdinaryTelegramUnlinkOutcome.INHERITED_UNLINK_ALLOWED
    assert result.mutation_allowed is True
    assert result.error_code is None


def test_active_customer_ordinary_unlink_is_exact_zero_mutation_denial() -> None:
    result = decide_ordinary_telegram_unlink(CustomerLifecycleStatus.ACTIVE)

    assert result.outcome is OrdinaryTelegramUnlinkOutcome.ACTIVE_CUSTOMER_DENIED
    assert result.mutation_allowed is False
    assert result.error_code == TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER
    assert not hasattr(result, "event")
    assert not hasattr(result, "invalidated_otp_purposes")
    assert not hasattr(result, "customer_transition")


def test_successful_protected_active_relink_invalidates_both_otp_purposes() -> None:
    result = ProtectedActiveTelegramRelinkResult(
        outcome=ProtectedTelegramRelinkOutcome.RELINKED
    )

    assert result.customer_status is CustomerLifecycleStatus.ACTIVE
    assert (
        result.link_disposition
        is ProtectedTelegramRelinkLinkDisposition.GENERATION_REPLACED
    )
    assert result.invalidated_otp_purposes == (
        OtpPurpose.LOGIN,
        OtpPurpose.REGISTRATION,
    )


def test_protected_active_relink_collision_preserves_old_link_and_all_state() -> None:
    result = ProtectedActiveTelegramRelinkResult(
        outcome=ProtectedTelegramRelinkOutcome.CHAT_COLLISION
    )

    assert result.customer_status is CustomerLifecycleStatus.ACTIVE
    assert (
        result.link_disposition
        is ProtectedTelegramRelinkLinkDisposition.CURRENT_LINK_PRESERVED
    )
    assert result.invalidated_otp_purposes == ()
    assert not hasattr(result, "customer_transition")


def test_telegram_contracts_reject_untyped_state_and_outcome() -> None:
    with pytest.raises(TypeError, match="lifecycle status"):
        decide_ordinary_telegram_unlink("active")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="relink outcome"):
        ProtectedActiveTelegramRelinkResult(outcome="RELINKED")  # type: ignore[arg-type]
