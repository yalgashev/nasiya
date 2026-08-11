from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    name: PROJECT_ROOT / "docs" / name
    for name in (
        "m16_scope_contract.md",
        "m16_decisions.md",
        "m16_repository_map.md",
        "m16_persistence_plan.md",
    )
}


def _doc(name: str) -> str:
    return DOCS[name].read_text(encoding="utf-8")


def test_m16_authority_docs_exist_and_share_exact_baseline() -> None:
    documents = {name: _doc(name) for name in DOCS}

    assert all(path.is_file() for path in DOCS.values())
    for name in DOCS:
        assert "547723ffc8e4148c5b4de86763b7c5add0588e86" in documents[name]
        assert "a8bc494c90dde3cf186b49aad8b6b8470af99c00" in documents[name]
        assert "b6c7d8e9f0a1" in documents[name]
    for sha256 in (
        "1c57d9a31d5b02275925a37b59025c48c85908af45a34e837eacf0824944f379",
        "2c094903780a5f3526e6275162501974d058831deda55a9b4b593cc88a105b25",
        "5fa102979315cef760f35607da0b272b0aa25d46f704342d360819586377343f",
    ):
        assert sha256 in documents["m16_scope_contract.md"]
        assert sha256 in documents["m16_decisions.md"]


def test_scope_freezes_formula_source_eligibility_and_privacy() -> None:
    scope = _doc("m16_scope_contract.md")

    for token in (
        "ORDER BY occurred_at ASC, debt_id ASC, event_type ASC",
        "score_i = min(100, max(0, score_(i-1) + delta_i))",
        "if global_hard_block: BLOCKED",
        "tashkent_business_date(accepted_at)",
        "no on_time_paid for (shop_customer_id, payment_business_date)",
        "Each lawful `active -> overdue` produces one `overdue` event",
        "typed pending overdue effect",
        "POST /shop/customers/{shop_customer_id}/risk-band-disclosures",
        "GET  /shop/risk-band-disclosures/{disclosure_view_id}",
        "does not lock the target or recompute band",
        "raw key, digest, request hash",
    ):
        assert token in scope


def test_repository_map_labels_existing_extensions_and_prospective_symbols() -> None:
    repository_map = _doc("m16_repository_map.md")

    for token in (
        "`EXISTS`",
        "`EXTEND`",
        "`PLANNED`",
        "| EXISTS | `app/debt/overdue_targeting.py`",
        "| EXTEND | `app/debt/overdue_service.py`",
        "| EXTEND | `app/payment/service.py`",
        "| PLANNED | `app/rating/contracts.py`",
        "| PLANNED | `app/debt/rating_ports.py`",
        "does **not** claim that the path or symbol already exists",
        "Shop -> ShopStaff -> User -> Customer -> ShopCustomer -> IdempotencyKey",
    ):
        assert token in repository_map


def test_persistence_plan_freezes_schema_reconciliation_and_guards() -> None:
    plan = _doc("m16_persistence_plan.md")

    for token in (
        'revision = "c7d8e9f0a1b2"',
        'down_revision = "b6c7d8e9f0a1"',
        "LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE",
        "ck_rating_events_delta_matches_event",
        "uq_rating_events_debt_id_event_type",
        "ux_rating_events_positive_shop_customer_business_date",
        "ix_rating_events_shop_customer_occurred_debt_event",
        "fk_rating_events_debt_shop_customer",
        "fk_disclosure_logs_shop_customer_shop",
        "shop.risk_band_disclosures.create",
        "disclosure.risk_band_viewed",
        "historical_reconciliation",
        "M16 downgrade blocked:",
    ):
        assert token in plan
    assert "No update/delete/reversal/compensation" not in plan
    assert "UPDATE/DELETE/reversal/compensation are forbidden" in plan


def test_decisions_record_all_final_owner_decisions_and_containment() -> None:
    decisions = _doc("m16_decisions.md")
    owner_section = decisions.split("## Product Owner decisions — final", 1)[1].split(
        "## Implementation decisions fixed by M16.01–05", 1
    )[0]

    assert (
        sum(owner_section.count(f"\n{number}. **PO-M16-") for number in range(1, 37))
        == 36
    )
    for token in (
        "Operational writer drain",
        "platform-admin has no bypass",
        "no JSON/API/fragment/admin route",
        "One linear Alembic child",
        "does not add M16 product code, migration, or a checkpoint commit",
    ):
        assert token in decisions
