# Nasiya M17 Persistence Plan

## Revision boundary

This plan is frozen against baseline
`fdfe7258da70b4ad8c948f8e5dfd2ce7e6117057` /
`4349b7fbdbc6ee1c7ba08a756a6b6fb647cdf30c` and the protected hashes recorded
in `m17_scope_contract.md` and `m17_decisions.md`.

One **PLANNED** linear revision is required:

```text
revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
```

The planned revision does not exist. M17 adds zero tables and no Payment,
ShopCustomer, Customer, or DisclosureViewLog business column.

## Debt schema delta

Add only nullable `debts` fields:

```text
written_off_at TIMESTAMPTZ
written_off_revision INTEGER
written_off_reason TEXT
written_off_actor_user_id UUID REFERENCES users(id) ON DELETE RESTRICT
written_off_settled_at TIMESTAMPTZ
written_off_settled_revision INTEGER
```

The actor FK is `fk_debts_written_off_actor_user_id_users_id`. Add
`ix_debts_status_overdue_at_id(status, overdue_at, id)`. Extend existing
`ck_debts_status_allowed`, `ck_debts_status_metadata_matches_status`, and
`ck_debts_timestamp_order`. Add:

```text
ck_debts_written_off_metadata_complete
ck_debts_written_off_reason_allowed
ck_debts_written_off_revision_positive
ck_debts_written_off_revision_not_after_revision
ck_debts_written_off_settled_metadata_pair
ck_debts_written_off_settled_revision_positive
ck_debts_written_off_settled_revision_not_after_revision
```

The checks enforce four-field write-off evidence, closed reason, settlement
pair, positive revisions, overdue < write-off < settlement revisions, settlement
revision equal to current revision, nondecreasing timestamps, and all-six-NULL
outside M17 statuses. Written-off statuses retain accepted/overdue evidence and
`paid_at IS NULL`.

## Closed registry extensions

Extend `ck_rating_events_event_type_allowed`,
`ck_rating_events_delta_matches_event`, and
`ck_rating_events_recording_source_allowed` for exactly:

```text
written_off          / -40 / live
written_off_settled  / +10 / live
```

Keep `uq_rating_events_debt_id_event_type`,
`fk_rating_events_debt_shop_customer`, and the positive partial unique
unchanged. Extend `audit_log` event, actor, object, and exact JSON checks for
`debt.written_off` and `debt.written_off_settled`, both USER/Debt. Extend
`ck_idempotency_keys_endpoint_result_pair_allowed` for
`admin.debts.write_off` with existing `debt` result type.

## Upgrade and preservation

This is schema-only. Current `ck_debts_status_allowed` excludes written-off
statuses and M16 rating checks exclude `-40/+10`; no lawful historical M17
source exists. Upgrade creates no synthetic event/audit/notification, no
backfill, and performs no source DML.

Operations require old-writer drain and old-version restart prohibition. First
source-data action is:

```sql
LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE
```

The migration preserves each pre-M17 Debt-column canonical projection and leaves
Payment, RatingEvent, AuditLog, and IdempotencyKey rows unchanged. This is
logical equality, not physical tuple-byte identity after `ALTER TABLE`.

## Guarded downgrade

Before any DDL, one named M17 loss guard rejects a written-off status; any
non-NULL new Debt metadata; `written_off|written_off_settled` rating events;
either M17 audit event; `admin.debts.write_off` idempotency rows; or data not
compatible with predecessor M16 checks. It never deletes, rewrites, or
compensates data.

Only an empty compatible database may downgrade. Then registries are restored,
queue index/FK/check dependencies removed, and child columns dropped safely.
M16 tables, parent uniques, and `+5/-15` history remain untouched. Real-PG
proof covers fresh upgrade/empty downgrade/re-upgrade, mixed-M16 preservation,
invalid check/FK/registry denial, independently seedable guard classes, exact
queue order/page bound, and one head. No `create_all`, manual DDL, skip, or
second head is valid evidence.
