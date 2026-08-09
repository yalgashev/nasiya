from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    name: PROJECT_ROOT / "docs" / name
    for name in (
        "m15_scope_contract.md",
        "m15_decisions.md",
        "m15_repository_map.md",
        "m15_persistence_plan.md",
    )
}


def _doc(name: str) -> str:
    return DOCS[name].read_text(encoding="utf-8")


def test_m15_authority_docs_exist_and_share_frozen_baseline() -> None:
    documents = {name: _doc(name) for name in DOCS}

    assert all(path.is_file() for path in DOCS.values())
    assert (
        "881413608f16db054078448676d6fae71afe6221" in documents["m15_scope_contract.md"]
    )
    for name in (
        "m15_scope_contract.md",
        "m15_decisions.md",
        "m15_persistence_plan.md",
    ):
        assert "a5b6c7d8e9f0" in documents[name]


def test_scope_freezes_overdue_basis_and_post_lock_time_authority() -> None:
    scope = _doc("m15_scope_contract.md")

    for token in (
        "tashkent_business_date(captured_now) > debt.due_date",
        "expected_revision",
        "expected_balance_basis",
        "`discounted` or `original`",
        "after the Debt lock",
        "after the Customer lock",
        "Payment revision below\n`overdue_revision`",
        "v1 completed-key replay",
        "zero-write denial",
    ):
        assert token in scope


def test_repository_map_marks_prospective_symbols_and_preserves_containment() -> None:
    repository_map = _doc("m15_repository_map.md")

    for token in (
        "`EXISTS`",
        "`EXTEND`",
        "`PLANNED`",
        "| EXISTS | `app/debt/overdue_ports.py`",
        "| EXISTS | `app/debt/overdue_targeting.py`",
        "`app/debt/overdue_service.py`",
        "`alembic/versions/b6c7d8e9f0a1_add_overdue_persistence.py`",
        "`app.debt` must not import\n`app.payment`",
        "Shop -> Customer -> ShopCustomer -> Debt\n-> AuditLog",
    ):
        assert token in repository_map


def test_persistence_plan_is_debt_only_delta_with_guarded_downgrade() -> None:
    plan = _doc("m15_persistence_plan.md")

    for token in (
        "`overdue_at`",
        "`overdue_revision`",
        "`ix_debts_status_due_date_id`",
        "`ck_debts_overdue_metadata_pair`",
        "`ck_debts_overdue_revision_positive`",
        "`ck_debts_overdue_revision_not_after_revision`",
        'down_revision = "a5b6c7d8e9f0"',
        "status = 'overdue'",
        "'debt.overdue', 'debt.clawback_applied'",
        "ck_debts_status_allowed",
        "ck_debts_status_metadata_matches_status",
        "ck_debts_timestamp_order",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_actor_matches_event",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_payload_exact_shape",
        "ix_debts_shop_customer_id_created_at_id",
        "ix_debts_shop_customer_id_status_due_date_id",
        "ix_debts_status_pending_expires_at_id",
        "`overdue -> overdue|paid`",
        "contains no `UPDATE`",
        "alter `payments`",
        "alter\n`idempotency_keys`",
        "payload ->> 'from_status' = 'overdue'",
        "<exact M14 status/metadata shape>",
        "M15 downgrade blocked:",
    ):
        assert token in plan
    assert "No new table, Payment column" in plan


def test_decisions_keep_deferred_work_out_of_m15() -> None:
    decisions = _doc("m15_decisions.md")

    product_owner_section = decisions.split("## Product Owner decisions — final", 1)[
        1
    ].split("## Implementation decisions", 1)[0]

    assert (
        sum(product_owner_section.count(f"\n{number}.") for number in range(1, 31))
        == 30
    )
    assert "cron, worker, scheduler, `job_run`, retry, admin trigger" in decisions
    assert "CR-M6-03 clawback reversal remains valid" in decisions
    assert "Rating `+5/-15`" in decisions


def test_all_authority_docs_use_exact_clawback_event_name() -> None:
    documents = "\n".join(_doc(name) for name in DOCS)

    assert "debt.clawback_applied" in _doc("m15_scope_contract.md")
    assert "debt.clawback_applied" in _doc("m15_decisions.md")
    assert "debt.clawback_applied" in _doc("m15_repository_map.md")
    assert "debt.clawback_applied" in _doc("m15_persistence_plan.md")
    assert "`debt.clawback`" not in documents
