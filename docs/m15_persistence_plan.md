# M15 Persistence and Guarded-Downgrade Plan

This is the executable design authority for one M15 Alembic revision. It is a
delta from immutable M14 head `a5b6c7d8e9f0`; no M15 ORM model or revision
exists when this plan is written.

## Exact schema delta

The only new stored columns are added to `debts`:

| Column | PostgreSQL type | Contract |
| --- | --- | --- |
| `overdue_at` | TIMESTAMPTZ | nullable, no default; server time of one rollover |
| `overdue_revision` | INTEGER | nullable, no default; revision assigned by rollover |

No new table, Payment column, balance cache, Customer flag, event/outbox table,
or second index is allowed. Add exactly one index:

```text
ix_debts_status_due_date_id (status, due_date, id)
```

The migration replaces appropriate M14 Debt checks while preserving their names
and all unaffected M14 constraints, FKs, defaults, and indexes. Expanded
`ck_debts_status_allowed` permits exactly `pending`, `active`, `rejected`,
`cancelled`, `expired`, `paid`, and `overdue`.

The exact added named checks are:

| Name | Contract |
| --- | --- |
| `ck_debts_overdue_metadata_pair` | `overdue_at` and `overdue_revision` are both null or both non-null. |
| `ck_debts_overdue_revision_positive` | non-null `overdue_revision > 0`. |
| `ck_debts_overdue_revision_not_after_revision` | non-null marker is `<= debts.revision`. |

`ck_debts_status_metadata_matches_status` requires markers null for `pending`,
`active`, `rejected`, `cancelled`, and `expired`; accepted and both markers for
`overdue`; and permits late `paid` only with both markers and
`overdue_revision < revision`. On-time `paid` has both markers null. Non-paid
rows have null `paid_at`; lawful full payoff is its sole null-to-non-null path.

`ck_debts_timestamp_order` retains M14 ordering and, for a marked Debt,
requires `paid_at >= overdue_at >= accepted_at` when paid and
`updated_at >= overdue_at`. Transition never changes original/discounted money,
due date, or acceptance metadata.

## Audit and idempotency extension

No audit table or generic event facility is created. Extend existing exact
audit registry/payload checks only with:

| Event | Actor | Object | Exact safe payload keys |
| --- | --- | --- | --- |
| `debt.overdue` | SYSTEM | `debt` | `source`, `from_status`, `to_status`, `overdue_revision`, `business_date` |
| `debt.clawback_applied` | SYSTEM | `debt` | `source`, `from_basis`, `to_basis`, `balance_increase_uzs`, `overdue_revision` |

Constrained existing payment/debt event shapes extend only for lawful overdue
transition; extra payload keys remain rejected.

Idempotency has no schema delta. M15 payment uses v2 hash binding
`expected_balance_basis`; M14 v1 hash is only for completed legacy replay when
form basis is missing. Missing basis cannot enter a mutation command.

## Upgrade operation order

One M15 revision has `down_revision = "a5b6c7d8e9f0"` and, transactionally:

1. Adds nullable, default-free `overdue_at` and `overdue_revision` to `debts`.
2. Replaces Debt status/metadata/timestamp checks and creates the three named
   marker checks above.
3. Creates `ix_debts_status_due_date_id`.
4. Replaces only audit registry/payload checks required for the two exact M15
   event shapes and lawful overdue payment transition.

It does not backfill historic rows, create `payments`, modify Payment metadata,
create a job table, or create a second Alembic head. Registration changes only
as needed to expose extended existing Debt/audit models; cleanup ordering stays
valid.

## Fail-closed downgrade

Before every downgrade DDL operation, reject when either predicate is true:

```text
EXISTS (SELECT 1 FROM debts
        WHERE status = 'overdue'
           OR overdue_at IS NOT NULL
           OR overdue_revision IS NOT NULL)
EXISTS (SELECT 1 FROM audit_log
        WHERE event_type IN ('debt.overdue', 'debt.clawback_applied'))
```

The audit predicate remains even if corruption removed a marker. Any true
predicate raises stable M15 downgrade-blocked `RuntimeError` before schema
mutation. It covers late-paid rows because they retain markers.

Only with empty M15 lifecycle footprint may downgrade restore exact frozen M14
checks/audit registry from revision-local SQL, drop
`ix_debts_status_due_date_id`, then drop `overdue_revision` and `overdue_at`.
It restores M14 six statuses exactly, preserves M14 tables/Payment data, leaves
one M14 head, and loses no lawful M14 data.
