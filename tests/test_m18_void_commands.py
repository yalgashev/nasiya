from dataclasses import fields
from uuid import UUID, uuid4

import pytest

from app.debt.values import DebtId, DebtRevision, ShopId, UserId
from app.idempotency.contracts import (
    VoidPaymentRequestHash,
    create_void_payment_request_hash_v1,
)
from app.payment.commands import (
    VoidPaymentCommand,
    VoidPaymentCommandAssembly,
    VoidPaymentFailure,
    VoidPaymentMutationResult,
    VoidPaymentRawForm,
    assemble_void_payment_command,
)
from app.payment.enums import PaymentVoidOutcome, PaymentVoidReason
from app.payment.values import PaymentId

_ACTOR_ID = UserId(UUID("11111111-1111-4111-8111-111111111111"))
_SHOP_ID = ShopId(UUID("22222222-2222-4222-8222-222222222222"))
_PAYMENT_ID = PaymentId(UUID("33333333-3333-4333-8333-333333333333"))
_DEBT_ID = DebtId(UUID("44444444-4444-4444-8444-444444444444"))
_KEY = "55555555-5555-4555-8555-555555555555"


def _raw(**changes: object) -> VoidPaymentRawForm:
    values: dict[str, object] = {
        "expected_revision": "7",
        "reason": PaymentVoidReason.DUPLICATE_PAYMENT.value,
        "idempotency_key": _KEY,
        "confirmed": "yes",
    }
    values.update(changes)
    return VoidPaymentRawForm(**values)  # type: ignore[arg-type]


def _assemble(**changes: object) -> VoidPaymentCommandAssembly:
    values: dict[str, object] = {
        "actor_user_id": _ACTOR_ID,
        "current_shop_id": _SHOP_ID,
        "payment_id": _PAYMENT_ID,
        "server_resolved_debt_id": _DEBT_ID,
        "raw": _raw(),
    }
    values.update(changes)
    return assemble_void_payment_command(**values)  # type: ignore[arg-type]


def test_raw_form_contains_no_client_debt_payment_or_time_authority() -> None:
    assert tuple(field.name for field in fields(VoidPaymentRawForm)) == (
        "expected_revision",
        "reason",
        "idempotency_key",
        "confirmed",
    )
    assert all(not field.repr for field in fields(VoidPaymentRawForm))
    raw = _raw()
    assert repr(raw) == "VoidPaymentRawForm(<redacted>)"
    assert _KEY not in repr(raw)
    assert PaymentVoidReason.DUPLICATE_PAYMENT.value not in repr(raw)


def test_each_closed_reason_builds_a_server_resolved_tenant_command() -> None:
    for reason in PaymentVoidReason:
        assembly = _assemble(raw=_raw(reason=reason.value))
        assert assembly.failure is None
        assert assembly.command is not None
        command = assembly.command
        assert command.actor_user_id == _ACTOR_ID
        assert command.current_shop_id == _SHOP_ID
        assert command.payment_id == _PAYMENT_ID
        assert command.debt_id == _DEBT_ID
        assert command.expected_revision == DebtRevision(7)
        assert command.reason is reason
        assert tuple(field.name for field in fields(command)) == (
            "actor_user_id",
            "current_shop_id",
            "payment_id",
            "debt_id",
            "expected_revision",
            "reason",
            "idempotency_key",
            "request_hash",
        )
        assert all(not field.repr for field in fields(command))


@pytest.mark.parametrize(
    "raw",
    (
        _raw(reason=None),
        _raw(reason="other"),
        _raw(reason="free text"),
        _raw(reason="refund"),
        _raw(idempotency_key=None),
        _raw(idempotency_key="not-a-key"),
        _raw(expected_revision=""),
        _raw(expected_revision="0"),
        _raw(expected_revision="+7"),
        _raw(expected_revision="07"),
        _raw(confirmed=None),
        _raw(confirmed="no"),
        _raw(confirmed="YES"),
    ),
)
def test_invalid_reason_key_revision_or_confirmation_never_builds_command(
    raw: VoidPaymentRawForm,
) -> None:
    assembly = _assemble(raw=raw)
    assert assembly.command is None
    assert assembly.failure is VoidPaymentFailure.INVALID_INPUT
    assert "not-a-key" not in repr(assembly)
    assert "free text" not in repr(assembly)


def test_v1_hash_is_deterministic_and_binds_every_server_mutation_field() -> None:
    common = {
        "actor_user_id": _ACTOR_ID,
        "shop_id": _SHOP_ID,
        "payment_id": _PAYMENT_ID,
        "debt_id": _DEBT_ID,
        "expected_revision": DebtRevision(7),
        "reason": PaymentVoidReason.DUPLICATE_PAYMENT,
    }
    baseline = create_void_payment_request_hash_v1(**common)
    assert create_void_payment_request_hash_v1(**common) == baseline
    variants = (
        {"actor_user_id": UserId(uuid4())},
        {"shop_id": ShopId(uuid4())},
        {"payment_id": PaymentId(uuid4())},
        {"debt_id": DebtId(uuid4())},
        {"expected_revision": DebtRevision(8)},
        {"reason": PaymentVoidReason.INCORRECT_AMOUNT},
    )
    for change in variants:
        assert (
            create_void_payment_request_hash_v1(
                **(common | change)  # type: ignore[arg-type]
            )
            != baseline
        )


def test_command_recomputes_hash_and_rejects_mismatched_identity() -> None:
    assembly = _assemble()
    assert assembly.command is not None
    command = assembly.command
    with pytest.raises(ValueError, match="request hash"):
        VoidPaymentCommand(
            actor_user_id=command.actor_user_id,
            current_shop_id=command.current_shop_id,
            payment_id=command.payment_id,
            debt_id=command.debt_id,
            expected_revision=command.expected_revision,
            reason=command.reason,
            idempotency_key=command.idempotency_key,
            request_hash=create_void_payment_request_hash_v1(
                actor_user_id=command.actor_user_id,
                shop_id=command.current_shop_id,
                payment_id=PaymentId(uuid4()),
                debt_id=command.debt_id,
                expected_revision=command.expected_revision,
                reason=command.reason,
            ),
        )


def test_command_hash_key_locators_and_result_id_are_redacted() -> None:
    assembly = _assemble()
    assert assembly.command is not None
    command = assembly.command
    rendered = " ".join(
        (
            repr(command),
            repr(command.request_hash),
            repr(assembly),
        )
    )
    for secret in (
        str(_ACTOR_ID),
        str(_SHOP_ID),
        str(_PAYMENT_ID.as_uuid()),
        str(_DEBT_ID.as_uuid()),
        _KEY,
        command.request_hash.value,
        command.reason.value,
    ):
        assert secret not in rendered
    assert fields(VoidPaymentRequestHash)[0].repr is False

    for outcome in PaymentVoidOutcome:
        result = VoidPaymentMutationResult(outcome=outcome, payment_id=_PAYMENT_ID)
        assert tuple(field.name for field in fields(result)) == (
            "outcome",
            "payment_id",
        )
        assert fields(result)[1].repr is False
        assert str(_PAYMENT_ID.as_uuid()) not in repr(result)


def test_stable_void_failures_are_exact_and_result_accepts_only_new_replay() -> None:
    assert tuple(failure.value for failure in VoidPaymentFailure) == (
        "PAYMENT_UNAVAILABLE",
        "PAYMENT_NOT_VOIDABLE",
        "DEBT_CHANGED",
        "VALIDATION_ERROR",
        "IDEMPOTENCY_CONFLICT",
    )
    assert tuple(PaymentVoidOutcome) == (
        PaymentVoidOutcome.NEW,
        PaymentVoidOutcome.REPLAY,
    )
    with pytest.raises(ValueError, match="outcome"):
        VoidPaymentMutationResult(
            outcome="conflict",  # type: ignore[arg-type]
            payment_id=_PAYMENT_ID,
        )


def test_non_raw_context_type_errors_collapse_without_identifier_disclosure() -> None:
    for change in (
        {"actor_user_id": "actor"},
        {"current_shop_id": "shop"},
        {"payment_id": uuid4()},
        {"server_resolved_debt_id": uuid4()},
    ):
        assembly = _assemble(**change)
        assert assembly.command is None
        assert assembly.failure is VoidPaymentFailure.INVALID_INPUT
        assert str(change) not in repr(assembly)

    with pytest.raises(TypeError, match="VoidPaymentRawForm"):
        _assemble(raw={})
