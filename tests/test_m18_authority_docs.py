from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    name: PROJECT_ROOT / "docs" / name
    for name in (
        "m18_scope_contract.md",
        "m18_decisions.md",
        "m18_repository_map.md",
        "m18_persistence_plan.md",
    )
}


def _doc(name: str) -> str:
    return DOCS[name].read_text(encoding="utf-8")


def test_m18_authority_docs_share_exact_baseline_and_protected_hashes() -> None:
    documents = {name: _doc(name) for name in DOCS}

    assert all(path.is_file() for path in DOCS.values())
    for token in (
        "d341edf95511653d566726826304a74b3b3ffb60",
        "aedd8ef31a66e1bd15481e2f5079506f2bde61df",
        "d8e9f0a1b2c3",
    ):
        assert token in documents["m18_scope_contract.md"]
        assert token in documents["m18_decisions.md"]
    for digest in (
        "569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab",
        "0b1c4b8135678ffd95db4249307dad8a11dfeccc28f1bcd5fca64ee2f43b03cc",
        "d676ba09836306f6fee6e007a1b4dd91e394e239edf7356013f26078ec46e5d6",
        "4a77a4ee3c6d959383b6a0e185ac899e4d1fbada9d2dc477b9eed1f72e1cafed",
    ):
        assert digest in documents["m18_scope_contract.md"]
        assert digest in documents["m18_decisions.md"]


def test_m18_scope_freezes_void_money_rating_lock_and_privacy_contracts() -> None:
    scope = _doc("m18_scope_contract.md")

    for token in (
        "duplicate_payment | incorrect_amount | incorrect_method | "
        "payment_not_received | wrong_debt",
        "maximum `Payment.debt_revision_after`",
        "PaymentVoid(payment_id = Payment.id)",
        "written_off_settled + terminal recovery void    -> written_off",
        "explicit metadata backfill",
        "on_time_paid_voided = -5",
        "written_off_settled_voided = -10",
        "ORDER BY occurred_at, debt_id, event_type, source_revision",
        "Shop -> ShopStaff -> actor User -> Customer -> ShopCustomer -> IdempotencyKey",
        "shop.payments.void",
        "payment.voided",
        "GET  /shop/payments/{payment_id}/void",
        "POST /shop/payments/{payment_id}/void",
    ):
        assert token in scope


def test_m18_repository_map_separates_existing_extensions_and_planned_work() -> None:
    repository_map = _doc("m18_repository_map.md")

    for token in (
        "`EXISTS`",
        "`EXTEND`",
        "`PLANNED`",
        "does **not** claim that a path or symbol already exists",
        "| PLANNED | `app/payment/void_targeting.py`",
        "| PLANNED | `app/payment/void_source.py`",
        "| PLANNED | `app/payment/void_service.py`",
        "| PLANNED | "
        "`alembic/versions/e9f0a1b2c3d4_add_payment_void_and_rating_source_revision.py`",
        "never re-locked",
    ):
        assert token in repository_map


def test_m18_decisions_record_all_owner_decisions_and_containment() -> None:
    decisions = _doc("m18_decisions.md")

    for number in range(1, 9):
        assert decisions.count(f"| PO-M18-{number:02d} |") == 1
    for token in (
        "M18-LOCK-01",
        "M18-TX-01",
        "M18-KEY-01",
        "M18-MIG-01",
        "M18-DOWN-01",
        "M18 product code",
    ):
        assert token in decisions


def test_m18_persistence_plan_is_one_source_complete_loss_guarded_child() -> None:
    plan = _doc("m18_persistence_plan.md")

    for token in (
        'revision = "e9f0a1b2c3d4"',
        'down_revision = "d8e9f0a1b2c3"',
        "exactly one table, `payment_voids`",
        "uq_payments_id_debt_id_debt_revision_after",
        "fk_payment_voids_payment_debt_revision",
        "fk_payment_voids_debt_shop_customer",
        "ck_payment_voids_revision_order",
        "ck_rating_events_source_revision_positive",
        "ux_rating_events_single_debt_negative_source",
        "ix_rating_events_shop_customer_occurred_debt_event_src_rev",
        "LOCK TABLE debts, payments, rating_events, audit_log "
        "IN SHARE ROW EXCLUSIVE MODE",
        "explicit metadata backfill",
        "_guard_m18_downgrade_loss()",
    ):
        assert token in plan
