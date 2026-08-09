from dataclasses import fields
from decimal import Decimal
from uuid import UUID

import pytest

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage
from app.payment.commands import (
    CreatePaymentCommand,
    CreatePaymentRawForm,
    assemble_create_payment_command,
)
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.enums import PaymentMethod
from app.payment.presentation import get_payment_web_error_message
from app.shop.enums import ShopRole

_ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
_SHOP_ID = UUID("22222222-2222-4222-8222-222222222222")
_DEBT_ID = "33333333-3333-4333-8333-333333333333"
_KEY = "44444444-4444-4444-8444-444444444444"


def _actor() -> DetachedPaymentActorContext:
    return DetachedPaymentActorContext(
        actor_user_id=_ACTOR_ID,
        current_shop_id=_SHOP_ID,
        role_hint=ShopRole.CASHIER,
        language=DebtWebLanguage.RU,
    )


def _form(**changes: object) -> CreatePaymentRawForm:
    values: dict[str, object] = {
        "debt_id": _DEBT_ID,
        "amount_uzs": "125000",
        "method": "transfer",
        "idempotency_key": _KEY,
        "expected_revision": "7",
    }
    values.update(changes)
    return CreatePaymentRawForm(**values)  # type: ignore[arg-type]


def test_assembler_creates_only_canonical_typed_server_scoped_command() -> None:
    result = assemble_create_payment_command(
        actor=_actor(), form=_form(), header_idempotency_key=_KEY
    )

    assert result.error is None
    assert isinstance(result.command, CreatePaymentCommand)
    assert tuple(field.name for field in fields(result.command)) == (
        "actor_user_id",
        "current_shop_id",
        "debt_id",
        "amount",
        "method",
        "idempotency_key",
        "expected_revision",
        "request_hash",
    )
    assert result.command.actor_user_id == _ACTOR_ID
    assert result.command.current_shop_id == _SHOP_ID
    assert result.command.debt_id.as_uuid() == UUID(_DEBT_ID)
    assert result.command.amount.value == Decimal("125000")
    assert result.command.method is PaymentMethod.TRANSFER
    assert result.command.expected_revision.value == 7


@pytest.mark.parametrize(
    ("form_key", "header_key"),
    (
        (_KEY, None),
        (None, _KEY),
        (_KEY, _KEY),
    ),
)
def test_form_header_key_presence_matrix_converges_to_one_canonical_key(
    form_key: str | None,
    header_key: str | None,
) -> None:
    result = assemble_create_payment_command(
        actor=_actor(),
        form=_form(idempotency_key=form_key),
        header_idempotency_key=header_key,
    )

    assert result.error is None and result.command is not None
    assert result.command.idempotency_key.as_uuid() == UUID(_KEY)


@pytest.mark.parametrize(
    "changes,header_key",
    (
        ({"debt_id": "{" + _DEBT_ID + "}"}, _KEY),
        ({"amount_uzs": "125 000"}, _KEY),
        ({"amount_uzs": "125000.0"}, _KEY),
        ({"amount_uzs": None}, _KEY),
        ({"method": "Cash"}, _KEY),
        ({"method": None}, _KEY),
        ({"expected_revision": "0"}, _KEY),
        ({"expected_revision": "07"}, _KEY),
        ({"expected_revision": " 7"}, _KEY),
        ({"idempotency_key": None}, None),
        ({"idempotency_key": _KEY}, "55555555-5555-4555-8555-555555555555"),
        ({"idempotency_key": "{" + _KEY + "}"}, _KEY),
    ),
)
def test_bad_boundary_fields_collapse_to_localized_validation_error(
    changes: dict[str, object], header_key: str | None
) -> None:
    result = assemble_create_payment_command(
        actor=_actor(), form=_form(**changes), header_idempotency_key=header_key
    )

    assert result.command is None
    assert result.error is ErrorCode.VALIDATION_ERROR
    assert (
        get_payment_web_error_message(DebtWebLanguage.RU, result.error)
        == "Проверьте введённые данные."
    )


def test_command_and_failed_assembly_redact_raw_key_hash_locator_and_amount() -> None:
    result = assemble_create_payment_command(
        actor=_actor(), form=_form(), header_idempotency_key=_KEY
    )
    assert result.command is not None

    for value in (_KEY, _DEBT_ID, "125000", result.command.request_hash.value):
        assert value not in repr(result.command)
        assert value not in repr(result)
        assert value not in repr(_form())


def test_each_hashed_command_field_changes_the_typed_request_hash() -> None:
    baseline = assemble_create_payment_command(
        actor=_actor(), form=_form(), header_idempotency_key=_KEY
    ).command
    assert baseline is not None
    variants = (
        assemble_create_payment_command(
            actor=_actor(), form=_form(amount_uzs="125001"), header_idempotency_key=_KEY
        ).command,
        assemble_create_payment_command(
            actor=_actor(),
            form=_form(debt_id="77777777-7777-4777-8777-777777777777"),
            header_idempotency_key=_KEY,
        ).command,
        assemble_create_payment_command(
            actor=_actor(), form=_form(method="cash"), header_idempotency_key=_KEY
        ).command,
        assemble_create_payment_command(
            actor=_actor(),
            form=_form(expected_revision="8"),
            header_idempotency_key=_KEY,
        ).command,
        assemble_create_payment_command(
            actor=DetachedPaymentActorContext(
                actor_user_id=UUID("66666666-6666-4666-8666-666666666666"),
                current_shop_id=_SHOP_ID,
                role_hint=ShopRole.CASHIER,
                language=DebtWebLanguage.RU,
            ),
            form=_form(),
            header_idempotency_key=_KEY,
        ).command,
        assemble_create_payment_command(
            actor=DetachedPaymentActorContext(
                actor_user_id=_ACTOR_ID,
                current_shop_id=UUID("88888888-8888-4888-8888-888888888888"),
                role_hint=ShopRole.CASHIER,
                language=DebtWebLanguage.RU,
            ),
            form=_form(),
            header_idempotency_key=_KEY,
        ).command,
    )

    assert all(variant is not None for variant in variants)
    assert all(variant.request_hash != baseline.request_hash for variant in variants)
