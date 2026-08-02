from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

import app.customer_activation.contracts as activation_contracts
from app.customer_activation.contracts import (
    CustomerActivationTransitionOutcome,
    CustomerLifecycleState,
    CustomerLifecycleStatus,
    transition_customer_to_active,
)

_CREATED = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
_UPDATED = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
_ACTIVATED = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _draft() -> CustomerLifecycleState:
    return CustomerLifecycleState(
        status=CustomerLifecycleStatus.DRAFT,
        created_at=_CREATED,
        updated_at=_UPDATED,
        activated_at=None,
    )


def test_customer_lifecycle_status_and_state_shape_are_exact() -> None:
    assert tuple(status.value for status in CustomerLifecycleStatus) == (
        "draft",
        "active",
    )
    assert tuple(field.name for field in fields(CustomerLifecycleState)) == (
        "status",
        "created_at",
        "updated_at",
        "activated_at",
    )
    assert {
        "customer_id",
        "user_id",
        "otp_id",
        "offer_id",
        "document_id",
        "session_id",
        "activation_method",
    }.isdisjoint(field.name for field in fields(CustomerLifecycleState))


def test_draft_transitions_once_with_equal_activation_and_update_time() -> None:
    result = transition_customer_to_active(_draft(), now=_ACTIVATED)

    assert result.outcome is CustomerActivationTransitionOutcome.ACTIVATED
    assert result.state == CustomerLifecycleState(
        status=CustomerLifecycleStatus.ACTIVE,
        created_at=_CREATED,
        updated_at=_ACTIVATED,
        activated_at=_ACTIVATED,
    )


def test_active_replay_is_exact_noop_without_timestamp_rewrite() -> None:
    active = CustomerLifecycleState(
        status=CustomerLifecycleStatus.ACTIVE,
        created_at=_CREATED,
        updated_at=_ACTIVATED,
        activated_at=_ACTIVATED,
    )
    replay_time = _ACTIVATED + timedelta(days=1)

    result = transition_customer_to_active(active, now=replay_time)

    assert result.outcome is CustomerActivationTransitionOutcome.ALREADY_ACTIVE
    assert result.state is active
    assert result.state.updated_at == _ACTIVATED
    assert result.state.activated_at == _ACTIVATED


def test_missing_customer_is_zero_create_result() -> None:
    result = transition_customer_to_active(None, now=_ACTIVATED)

    assert result.outcome is CustomerActivationTransitionOutcome.MISSING
    assert result.state is None


def test_customer_lifecycle_timestamp_and_state_invariants_fail_closed() -> None:
    invalid = (
        {
            "status": CustomerLifecycleStatus.DRAFT,
            "created_at": _CREATED,
            "updated_at": _UPDATED,
            "activated_at": _ACTIVATED,
        },
        {
            "status": CustomerLifecycleStatus.ACTIVE,
            "created_at": _CREATED,
            "updated_at": _UPDATED,
            "activated_at": None,
        },
        {
            "status": CustomerLifecycleStatus.DRAFT,
            "created_at": _UPDATED,
            "updated_at": _CREATED,
            "activated_at": None,
        },
        {
            "status": CustomerLifecycleStatus.DRAFT,
            "created_at": datetime(2026, 8, 1, 8, 0),
            "updated_at": _UPDATED,
            "activated_at": None,
        },
    )
    for values in invalid:
        with pytest.raises((TypeError, ValueError)):
            CustomerLifecycleState(**values)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="activation time"):
        transition_customer_to_active(
            _draft(),
            now=_UPDATED - timedelta(seconds=1),
        )


def test_customer_lifecycle_exposes_no_reverse_or_correction_api() -> None:
    forbidden = {
        "transition_customer_to_draft",
        "deactivate_customer",
        "correct_active_customer",
        "delete_customer",
    }

    assert forbidden.isdisjoint(vars(activation_contracts))
