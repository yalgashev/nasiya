from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    name: PROJECT_ROOT / "docs" / name
    for name in (
        "m17_scope_contract.md",
        "m17_decisions.md",
        "m17_repository_map.md",
        "m17_persistence_plan.md",
    )
}


def _doc(name: str) -> str:
    return DOCS[name].read_text(encoding="utf-8")


def test_m17_authority_docs_share_exact_start_and_protected_evidence() -> None:
    documents = {name: _doc(name) for name in DOCS}

    assert all(path.is_file() for path in DOCS.values())
    assert (
        "fdfe7258da70b4ad8c948f8e5dfd2ce7e6117057" in documents["m17_scope_contract.md"]
    )
    assert "4349b7fbdbc6ee1c7ba08a756a6b6fb647cdf30c" in documents["m17_decisions.md"]
    for digest in (
        "569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab",
        "e64ae346601475a411a7a2a74d3ad4780c95f878825ce48235d0181383260d8d",
        "ff5fd0269e1fa9cc80aeaa2ff2dbf5f27fdba85908aecbb52717a0924317774c",
        "9b7af60dcd08a81b75b3e74ec081e3759eeaaec056553c1d0a9f3f0690d5d631",
    ):
        assert digest in documents["m17_scope_contract.md"]
        assert digest in documents["m17_decisions.md"]


def test_m17_scope_freezes_state_sources_rating_block_and_routes() -> None:
    scope = _doc("m17_scope_contract.md")

    for token in (
        "persisted overdue -- platform-admin write-off --> written_off",
        "written_off_settled",
        "RatingEvent(overdue,",
        "debt.clawback_applied",
        "written_off -> -40",
        "written_off_settled -> +10",
        "ORDER BY occurred_at ASC, debt_id ASC, event_type ASC",
        "unresolved `written_off`",
        "admin.debts.write_off",
        "debt.written_off_settled",
        "{reason_provided,from_status,to_status,written_off_revision}",
        "GET  /admin/debts/write-off-candidates",
        "POST /admin/debts/{debt_id}/write-off",
    ):
        assert token in scope


def test_m17_repository_map_separates_existing_extension_and_planned_work() -> None:
    repository_map = _doc("m17_repository_map.md")

    for token in (
        "`EXISTS`",
        "`EXTEND`",
        "`PLANNED`",
        "Dormant vocabulary only; not in `M15_PERSISTED_STATUSES`.",
        "| PLANNED | `app/debt/write_off_targeting.py`",
        "| PLANNED | `app/debt/write_off_service.py`",
        "| PLANNED | `alembic/versions/d8e9f0a1b2c3_add_written_off_debt_recovery.py`",
        "does **not** claim that the path or symbol already exists",
        "never re-locked",
    ):
        assert token in repository_map


def test_m17_decisions_record_all_36_owner_decisions_individually() -> None:
    decisions = _doc("m17_decisions.md")

    for number in range(1, 37):
        assert decisions.count(f"| PO-M17-{number:02d} |") == 1
    for token in (
        "admin.debts.write_off",
        "debt.written_off_settled",
        "no v3",
        "M17 product code",
    ):
        assert token in decisions


def test_m17_persistence_plan_is_one_guarded_schema_only_child() -> None:
    plan = _doc("m17_persistence_plan.md")

    for token in (
        'revision = "d8e9f0a1b2c3"',
        'down_revision = "c7d8e9f0a1b2"',
        "zero tables",
        "fk_debts_written_off_actor_user_id_users_id",
        "ix_debts_status_overdue_at_id(status, overdue_at, id)",
        "ck_debts_written_off_metadata_complete",
        "ck_rating_events_delta_matches_event",
        "admin.debts.write_off",
        "LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE",
        "no\nbackfill",
        "_guard_m17_downgrade_loss()",
    ):
        assert token in plan
