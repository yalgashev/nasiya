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

The planned revision does not exist at the M17.14 plan-verification boundary.
It must be the sole child/head. M17 adds zero tables and no Payment,
ShopCustomer, Customer, User, or DisclosureViewLog business column.

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
ck_debts_written_off_revision_chain
ck_debts_written_off_settled_revision_chain
```

The exact predicates are:

- `ck_debts_written_off_metadata_complete`: all four write-off fields are NULL
  or all four are non-NULL;
- `ck_debts_written_off_reason_allowed`: NULL or one of the exact five frozen
  reason values;
- `ck_debts_written_off_revision_positive`: NULL or `> 0`;
- `ck_debts_written_off_revision_not_after_revision`: NULL or `<= revision`;
- `ck_debts_written_off_settled_metadata_pair`: settlement fields are both NULL
  or both non-NULL;
- `ck_debts_written_off_settled_revision_positive`: NULL or `> 0`;
- `ck_debts_written_off_settled_revision_not_after_revision`: NULL or
  `<= revision`.
- `ck_debts_written_off_revision_chain`: write-off requires an earlier overdue
  revision;
- `ck_debts_written_off_settled_revision_chain`: settlement requires an earlier
  write-off revision.

The extended lifecycle check requires all six fields NULL for every M16 status.
`written_off` requires accepted/overdue evidence, all four write-off fields,
NULL settlement pair and `paid_at`; `written_off_settled` additionally requires
the settlement pair and `written_off_settled_revision = revision`. Revisions are
strict `overdue_revision < written_off_revision < settled_revision`; timestamps
are nondecreasing `accepted_at <= overdue_at <= written_off_at <=
written_off_settled_at <= updated_at`. The queue index has exact ascending
columns `(status, overdue_at, id)` and no predicate.

## Closed registry extensions

Extend `ck_rating_events_event_type_allowed`,
`ck_rating_events_delta_matches_event`, and
`ck_rating_events_recording_source_allowed` for exactly:

```text
written_off          / -40 / live
written_off_settled  / +10 / live
```

The recording-source predicate is pair-aware: `historical_reconciliation` is
legal only for `on_time_paid|overdue`; both M17 event types require `live`.
Keep `uq_rating_events_debt_id_event_type`,
`fk_rating_events_debt_shop_customer`, and the positive partial unique
unchanged. Extend `audit_log` event, actor, object, and exact JSON checks for
`debt.written_off` and `debt.written_off_settled`, both USER/Debt. Extend
`ck_idempotency_keys_endpoint_result_pair_allowed` for
`admin.debts.write_off` with existing `debt` result type.

## Upgrade and preservation

This is schema-only and has no backfill. Current `ck_debts_status_allowed` excludes written-off
statuses and M16 rating checks exclude `-40/+10`; no lawful historical M17
source exists. Upgrade creates no synthetic event/audit/notification, no
backfill, and performs no source DML.

Operations require old-writer drain before upgrade and old-version restart prohibition
after it. The migration's first source-data action, before catalog
replacement or scans, is:

```sql
LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE
```

The migration preserves each pre-M17 Debt-column canonical projection and leaves
Payment, RatingEvent, AuditLog, and IdempotencyKey rows unchanged. This is
logical equality, not physical tuple-byte identity after `ALTER TABLE`.

## Guarded downgrade

Before any downgrade DDL, `_guard_m17_downgrade_loss()` rejects these
independently seedable classes: a written-off status or any non-NULL new Debt
metadata; `written_off|written_off_settled` rating events; either M17 audit
event; `admin.debts.write_off` idempotency rows; or any row not compatible with
the predecessor M16 status/rating/audit/idempotency predicates. It never
deletes, rewrites, or compensates data.

Only an M17-empty, M16-compatible database may downgrade. Exact DDL order is:
restore audit/idempotency/rating checks; drop `ix_debts_status_overdue_at_id`;
drop extended Debt lifecycle/timestamp and nine M17 checks; restore the M16
Debt lifecycle/timestamp checks; drop
`fk_debts_written_off_actor_user_id_users_id`; then drop the six child columns.
M16 tables, parent uniques, and `+5/-15` history remain untouched. Real-PG
proof covers fresh upgrade/empty downgrade/re-upgrade, mixed-M16 preservation,
invalid check/FK/registry denial, independently seedable guard classes, exact
queue order/page bound, and one head. No `create_all`, manual DDL, skip, or
second head is valid evidence.
