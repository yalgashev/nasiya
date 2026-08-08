from dataclasses import FrozenInstanceError, fields
from datetime import UTC, date, datetime, timedelta, timezone
from inspect import signature
from uuid import UUID, uuid4

import pytest

from app.debt.commands import (
    CreateDebtCommand,
    CreateDebtRawForm,
    assemble_create_debt_command,
)


def _form(**overrides: object) -> CreateDebtRawForm:
    values: dict[str, object] = {
        "original_amount_uzs": "1000",
        "discount_percent": "1.25",
        "due_date": "2026-08-12",
        "idempotency_key": str(uuid4()),
    }
    values.update(overrides)
    return CreateDebtRawForm(**values)  # type: ignore[arg-type]


def test_assembler_builds_only_server_authoritative_immutable_command() -> None:
    key = str(uuid4())
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)

    command = assemble_create_debt_command(
        form=_form(idempotency_key=key),
        header_idempotency_key=key,
        now=now,
    )

    assert command.idempotency_key.as_uuid() == UUID(key)
    assert command.original_amount.value == 1000
    assert command.discount_basis_points.value == 125
    assert command.discounted_amount.value == 988
    assert command.due_date == date(2026, 8, 12)
    assert command.created_at == now
    assert command.pending_expires_at == now + timedelta(hours=72)
    assert tuple(field.name for field in fields(CreateDebtRawForm)) == (
        "original_amount_uzs",
        "discount_percent",
        "due_date",
        "idempotency_key",
    )
    assert tuple(field.name for field in fields(CreateDebtCommand)) == (
        "idempotency_key",
        "original_amount",
        "discount_basis_points",
        "discounted_amount",
        "due_date",
        "created_at",
        "pending_expires_at",
    )
    assert {"discounted_amount", "pending_expires_at", "status"}.isdisjoint(
        field.name for field in fields(CreateDebtRawForm)
    )
    assert "discounted" not in signature(assemble_create_debt_command).parameters
    with pytest.raises(FrozenInstanceError):
        command.due_date = date(2026, 8, 13)  # type: ignore[misc]

    header_only = assemble_create_debt_command(
        form=_form(idempotency_key=None),
        header_idempotency_key=key,
        now=now,
    )
    assert header_only.idempotency_key.as_uuid() == UUID(key)


def test_assembler_rejects_malformed_money_discount_due_date_and_key_before_write() -> (
    None
):
    now = datetime(2026, 8, 8, 18, tzinfo=UTC)
    valid_key = str(uuid4())
    invalid_forms = (
        _form(original_amount_uzs="0"),
        _form(original_amount_uzs="1.0"),
        _form(original_amount_uzs="1e3"),
        _form(discount_percent="100"),
        _form(discount_percent="-1"),
        _form(due_date="2026-08-10"),
        _form(due_date="08/12/2026"),
        _form(idempotency_key="not-a-uuid"),
    )
    for form in invalid_forms:
        with pytest.raises((TypeError, ValueError)):
            assemble_create_debt_command(
                form=form,
                header_idempotency_key=valid_key,
                now=now,
            )
    with pytest.raises(ValueError, match="do not match"):
        assemble_create_debt_command(
            form=_form(idempotency_key=valid_key),
            header_idempotency_key=str(uuid4()),
            now=now,
        )
    with pytest.raises(ValueError, match="required"):
        assemble_create_debt_command(
            form=_form(idempotency_key=None),
            header_idempotency_key=None,
            now=now,
        )
    with pytest.raises(ValueError, match="aware datetime"):
        assemble_create_debt_command(
            form=_form(idempotency_key=valid_key),
            header_idempotency_key=None,
            now=datetime(2026, 8, 8, 18),
        )


def test_assembler_uses_injected_time_and_tashkent_expiry_business_date() -> None:
    now = datetime(2026, 8, 8, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    key = str(uuid4())
    expiry = now.astimezone(UTC) + timedelta(hours=72)
    allowed_due_date = expiry.astimezone(timezone(timedelta(hours=5))).date()

    command = assemble_create_debt_command(
        form=_form(idempotency_key=key, due_date=allowed_due_date.isoformat()),
        header_idempotency_key=None,
        now=now,
    )

    assert command.created_at == now.astimezone(UTC)
    assert command.pending_expires_at == expiry
    assert command.due_date == allowed_due_date
