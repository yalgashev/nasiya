from dataclasses import fields, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.debt.enums import DebtBalanceBasis, DebtStatus
from app.debt.payment_progress import DebtPaymentProgressProjection
from app.debt.presentation import DebtWebLanguage
from app.debt.values import DebtId, DebtRevision, ShopId, UserId
from app.payment.commands import (
    CompletedM14PaymentReplayCandidate,
    CreatePaymentV2Command,
    CreatePaymentV2RawForm,
    assemble_create_payment_request,
)
from app.payment.contracts import (
    IncoherentPaymentHistoryError,
    PaymentReceiptProjection,
    create_payment_request_hash_v1,
    create_payment_request_hash_v2,
    resolve_current_balance_basis,
    resolve_historical_balance_basis,
)
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.enums import PaymentMethod
from app.payment.presentation import (
    PAYMENT_ROUTE_CONTRACTS,
    get_payment_web_copy,
)
from app.payment.values import PaymentAmountUZS
from app.shop.enums import ShopRole

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
SHOP_ID = UUID("22222222-2222-4222-8222-222222222222")
DEBT_ID = "33333333-3333-4333-8333-333333333333"
KEY = "44444444-4444-4444-8444-444444444444"


def _actor() -> DetachedPaymentActorContext:
    return DetachedPaymentActorContext(
        actor_user_id=ACTOR_ID,
        current_shop_id=SHOP_ID,
        role_hint=ShopRole.CASHIER,
        language=DebtWebLanguage.UZ_LATN,
    )


def _form(*, basis: str | None) -> CreatePaymentV2RawForm:
    return CreatePaymentV2RawForm(
        debt_id=DEBT_ID,
        amount_uzs="125000",
        method="transfer",
        idempotency_key=KEY,
        expected_revision="7",
        expected_balance_basis=basis,
    )


@pytest.mark.parametrize("basis", tuple(DebtBalanceBasis))
def test_m15_parser_returns_only_typed_v2_mutation_for_non_null_basis(
    basis: DebtBalanceBasis,
) -> None:
    result = assemble_create_payment_request(
        actor=_actor(),
        form=_form(basis=basis.value),
        header_idempotency_key=KEY,
    )

    assert result.error is None
    assert result.legacy_completed_replay is None
    assert isinstance(result.command, CreatePaymentV2Command)
    assert result.command.expected_balance_basis is basis
    assert result.command.request_hash == create_payment_request_hash_v2(
        actor_user_id=UserId(ACTOR_ID),
        shop_id=ShopId(SHOP_ID),
        debt_id=DebtId(UUID(DEBT_ID)),
        amount=PaymentAmountUZS(Decimal("125000")),
        method=PaymentMethod.TRANSFER,
        expected_revision=DebtRevision(7),
        expected_balance_basis=basis,
    )


def test_missing_basis_is_only_completed_m14_v1_replay_candidate() -> None:
    result = assemble_create_payment_request(
        actor=_actor(),
        form=_form(basis=None),
        header_idempotency_key=KEY,
    )

    assert result.error is None and result.command is None
    assert isinstance(
        result.legacy_completed_replay, CompletedM14PaymentReplayCandidate
    )
    candidate = result.legacy_completed_replay
    assert candidate.request_hash == create_payment_request_hash_v1(
        actor_user_id=UserId(ACTOR_ID),
        shop_id=ShopId(SHOP_ID),
        debt_id=DebtId(UUID(DEBT_ID)),
        amount=PaymentAmountUZS(Decimal("125000")),
        method=PaymentMethod.TRANSFER,
        expected_revision=DebtRevision(7),
    )
    assert not hasattr(candidate, "expected_balance_basis")


@pytest.mark.parametrize("basis", ("", "DISCOUNTED", " discounted", "future"))
def test_invalid_basis_cannot_become_mutation_or_legacy_candidate(basis: str) -> None:
    result = assemble_create_payment_request(
        actor=_actor(),
        form=_form(basis=basis),
        header_idempotency_key=KEY,
    )

    assert result.command is None
    assert result.legacy_completed_replay is None
    assert result.error is not None


def test_v2_hash_binds_basis_and_command_rejects_hash_substitution() -> None:
    discounted = assemble_create_payment_request(
        actor=_actor(),
        form=_form(basis="discounted"),
        header_idempotency_key=KEY,
    ).command
    original = assemble_create_payment_request(
        actor=_actor(),
        form=_form(basis="original"),
        header_idempotency_key=KEY,
    ).command

    assert discounted is not None and original is not None
    assert discounted.request_hash != original.request_hash
    with pytest.raises(ValueError, match="request hash"):
        replace(discounted, request_hash=original.request_hash)


@pytest.mark.parametrize(
    ("payment_revision", "overdue_revision", "expected"),
    (
        (2, None, DebtBalanceBasis.DISCOUNTED),
        (2, 3, DebtBalanceBasis.DISCOUNTED),
        (4, 3, DebtBalanceBasis.ORIGINAL),
    ),
)
def test_historical_receipt_basis_is_marker_revision_aware(
    payment_revision: int,
    overdue_revision: int | None,
    expected: DebtBalanceBasis,
) -> None:
    assert (
        resolve_historical_balance_basis(
            payment_revision=DebtRevision(payment_revision),
            overdue_revision=(
                None if overdue_revision is None else DebtRevision(overdue_revision)
            ),
        )
        is expected
    )


def test_payment_revision_equal_to_overdue_marker_fails_closed() -> None:
    with pytest.raises(IncoherentPaymentHistoryError, match="cannot equal"):
        resolve_historical_balance_basis(
            payment_revision=DebtRevision(3),
            overdue_revision=DebtRevision(3),
        )


def test_current_basis_uses_effective_overdue_without_persisted_mutation() -> None:
    due_date = date(2026, 8, 9)

    assert (
        resolve_current_balance_basis(
            status=DebtStatus.ACTIVE,
            due_date=due_date,
            server_now=datetime(2026, 8, 9, 18, 59, tzinfo=UTC),
            overdue_revision=None,
        )
        is DebtBalanceBasis.DISCOUNTED
    )
    assert (
        resolve_current_balance_basis(
            status=DebtStatus.ACTIVE,
            due_date=due_date,
            server_now=datetime(2026, 8, 9, 19, tzinfo=UTC),
            overdue_revision=None,
        )
        is DebtBalanceBasis.ORIGINAL
    )
    with pytest.raises(IncoherentPaymentHistoryError, match="Active debt"):
        resolve_current_balance_basis(
            status=DebtStatus.ACTIVE,
            due_date=due_date,
            server_now=datetime(2026, 8, 9, 18, 59, tzinfo=UTC),
            overdue_revision=DebtRevision(3),
        )
    assert (
        resolve_current_balance_basis(
            status=DebtStatus.PAID,
            due_date=due_date,
            server_now=datetime(2026, 8, 10, tzinfo=UTC),
            overdue_revision=DebtRevision(3),
        )
        is DebtBalanceBasis.ORIGINAL
    )


def test_safe_progress_and_receipt_contracts_exclude_raw_markers_and_ids() -> None:
    progress_fields = {field.name for field in fields(DebtPaymentProgressProjection)}
    receipt_fields = {field.name for field in fields(PaymentReceiptProjection)}

    assert {"balance_basis", "is_effectively_overdue"} <= progress_fields
    assert {"historical_balance_basis", "current_balance_basis"} <= receipt_fields
    for forbidden in (
        "overdue_at",
        "overdue_revision",
        "payment_id",
        "debt_id",
        "request_hash",
        "idempotency_key",
    ):
        assert forbidden not in progress_fields | receipt_fields
    for secret in (KEY, DEBT_ID, "125000"):
        assert secret not in repr(_form(basis="original"))


def test_overdue_copy_is_localized_and_route_surface_stays_exactly_six() -> None:
    for language in DebtWebLanguage:
        copy = get_payment_web_copy(language)
        assert copy["status_overdue"].strip()
        assert copy["original_basis"].strip()

    assert len(PAYMENT_ROUTE_CONTRACTS) == 6
    create_route = next(
        route
        for route in PAYMENT_ROUTE_CONTRACTS
        if route.name == "shop_debt_payment_create"
    )
    assert "expected_balance_basis" in create_route.form_fields
    assert not any(
        token in route.path
        for route in PAYMENT_ROUTE_CONTRACTS
        for token in ("/admin", "/api", "trigger", "void")
    )
