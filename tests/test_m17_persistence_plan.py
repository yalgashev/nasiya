from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = (ROOT / "docs/m17_persistence_plan.md").read_text(encoding="utf-8")


def test_m17_plan_freezes_exact_revision_table_and_column_delta() -> None:
    assert 'revision = "d8e9f0a1b2c3"' in PLAN
    assert 'down_revision = "c7d8e9f0a1b2"' in PLAN
    assert "zero tables" in PLAN
    for column in (
        "written_off_at",
        "written_off_revision",
        "written_off_reason",
        "written_off_actor_user_id",
        "written_off_settled_at",
        "written_off_settled_revision",
    ):
        assert column in PLAN
    assert "fk_debts_written_off_actor_user_id_users_id" in PLAN
    assert "ON DELETE RESTRICT" in PLAN
    assert "ix_debts_status_overdue_at_id(status, overdue_at, id)" in PLAN


def test_m17_plan_names_every_check_registry_and_preservation_rule() -> None:
    for name in (
        "ck_debts_status_allowed",
        "ck_debts_status_metadata_matches_status",
        "ck_debts_timestamp_order",
        "ck_debts_written_off_metadata_complete",
        "ck_debts_written_off_reason_allowed",
        "ck_debts_written_off_revision_positive",
        "ck_debts_written_off_revision_not_after_revision",
        "ck_debts_written_off_settled_metadata_pair",
        "ck_debts_written_off_settled_revision_positive",
        "ck_debts_written_off_settled_revision_not_after_revision",
        "ck_debts_written_off_revision_chain",
        "ck_debts_written_off_settled_revision_chain",
        "ck_rating_events_event_type_allowed",
        "ck_rating_events_delta_matches_event",
        "ck_rating_events_recording_source_allowed",
        "ck_idempotency_keys_endpoint_result_pair_allowed",
    ):
        assert name in PLAN
    assert "schema-only" in PLAN
    assert "no backfill" in PLAN
    assert "performs no source DML" in PLAN
    assert "old-writer drain" in PLAN
    assert "old-version restart prohibition" in PLAN
    assert "LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE" in PLAN


def test_m17_plan_freezes_guard_before_ddl_and_dependency_safe_drop_order() -> None:
    guard = PLAN.index("_guard_m17_downgrade_loss()")
    restore = PLAN.index("Exact DDL order is")
    drop_fk = PLAN.index(
        "`fk_debts_written_off_actor_user_id_users_id`; then drop the six"
    )
    assert guard < restore < drop_fk
    for loss_class in (
        "written-off status",
        "rating events",
        "M17 audit",
        "idempotency rows",
        "predecessor M16",
    ):
        assert loss_class in PLAN
