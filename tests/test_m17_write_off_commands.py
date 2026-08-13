from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.debt.commands import (
    M17_ADMIN_WRITE_OFF_ROUTES,
    WriteOffDebtFailure,
    WriteOffDebtMutationResult,
    WriteOffDebtRawForm,
    WriteOffReason,
    assemble_write_off_debt_command,
)
from app.debt.values import DebtId, DebtRevision
from app.idempotency.contracts import (
    IdempotencyOutcome,
    create_write_off_debt_request_hash_v1,
)
from app.offers.authorization import require_platform_admin_actor


def _actor():
    return require_platform_admin_actor(
        SimpleNamespace(id=uuid4(), is_active=True, is_platform_admin=True)
    )


def _raw(**changes: object) -> WriteOffDebtRawForm:
    values: dict[str, object] = {
        "debt_id": str(uuid4()),
        "expected_revision": "3",
        "reason": WriteOffReason.COLLECTION_EXHAUSTED.value,
        "idempotency_key": str(uuid4()),
    }
    values.update(changes)
    return WriteOffDebtRawForm(**values)  # type: ignore[arg-type]


def test_all_closed_reasons_assemble_with_admin_capability() -> None:
    assert tuple(reason.value for reason in WriteOffReason) == (
        "collection_exhausted",
        "customer_unreachable",
        "insolvency_or_deceased",
        "legal_or_compliance",
        "fraud_or_abuse",
    )
    assert tuple(failure.value for failure in WriteOffDebtFailure) == (
        "DEBT_UNAVAILABLE",
        "DEBT_CHANGED",
        "DEBT_NOT_WRITABLE_OFF",
        "WRITE_OFF_REASON_INVALID",
        "IDEMPOTENCY_CONFLICT",
    )
    assert tuple(field.name for field in fields(WriteOffDebtRawForm)) == (
        "debt_id",
        "expected_revision",
        "reason",
        "idempotency_key",
    )
    actor = _actor()
    for reason in WriteOffReason:
        assembly = assemble_write_off_debt_command(
            actor=actor,
            raw=_raw(reason=reason.value),
        )
        assert assembly.failure is None
        assert assembly.command is not None
        assert assembly.command.reason is reason
        rendered = repr(assembly.command)
        assert reason.value not in rendered
        assert str(assembly.command.debt_id.as_uuid()) not in rendered


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": None},
        {"reason": "other"},
        {"reason": "free text"},
        {"idempotency_key": None},
        {"idempotency_key": "not-a-key"},
        {"expected_revision": "0"},
        {"expected_revision": "+3"},
        {"debt_id": "not-a-debt"},
    ],
)
def test_invalid_raw_inputs_never_produce_a_mutation_command(
    changes: dict[str, object],
) -> None:
    assembly = assemble_write_off_debt_command(actor=_actor(), raw=_raw(**changes))
    assert assembly.command is None
    assert assembly.failure is WriteOffDebtFailure.INVALID_REASON
    assert "not-a" not in repr(assembly)


def test_shop_staff_like_context_cannot_substitute_for_platform_admin() -> None:
    with pytest.raises(TypeError, match="PlatformAdminActor"):
        assemble_write_off_debt_command(  # type: ignore[arg-type]
            actor=SimpleNamespace(actor_user_id=uuid4(), current_shop_id=uuid4()),
            raw=_raw(),
        )


def test_v1_hash_binds_actor_debt_revision_and_reason() -> None:
    actor = uuid4()
    debt = DebtId(uuid4())
    revision = DebtRevision(3)
    reason = WriteOffReason.COLLECTION_EXHAUSTED
    baseline = create_write_off_debt_request_hash_v1(
        actor_user_id=actor,
        debt_id=debt,
        expected_revision=revision,
        reason=reason,
    )
    variants = (
        {"actor_user_id": uuid4()},
        {"debt_id": DebtId(uuid4())},
        {"expected_revision": DebtRevision(4)},
        {"reason": WriteOffReason.CUSTOMER_UNREACHABLE},
    )
    common = {
        "actor_user_id": actor,
        "debt_id": debt,
        "expected_revision": revision,
        "reason": reason,
    }
    for change in variants:
        assert (
            create_write_off_debt_request_hash_v1(
                **(common | change)  # type: ignore[arg-type]
            )
            != baseline
        )
    assert baseline.value not in repr(baseline)


def test_internal_result_is_new_or_replay_and_redacts_locator() -> None:
    debt_id = DebtId(UUID("12345678-1234-5678-1234-567812345678"))
    for outcome in (IdempotencyOutcome.NEW, IdempotencyOutcome.REPLAY):
        result = WriteOffDebtMutationResult(outcome=outcome, debt_id=debt_id)
        assert str(debt_id.as_uuid()) not in repr(result)
    with pytest.raises(ValueError, match="outcome"):
        WriteOffDebtMutationResult(
            outcome=IdempotencyOutcome.CONFLICT,
            debt_id=debt_id,
        )


def test_exact_three_admin_ssr_routes_are_closed() -> None:
    assert {(route.method, route.path) for route in M17_ADMIN_WRITE_OFF_ROUTES} == {
        ("GET", "/admin/debts/write-off-candidates"),
        ("GET", "/admin/debts/{debt_id}/write-off"),
        ("POST", "/admin/debts/{debt_id}/write-off"),
    }
    assert all("api" not in route.path for route in M17_ADMIN_WRITE_OFF_ROUTES)
