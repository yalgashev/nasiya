# M15 Persistence and Guarded-Downgrade Plan

This is the executable design authority for one M15 Alembic revision. It is a
delta from immutable M14 head `a5b6c7d8e9f0`. M15.14 reconciled every name
below against that revision and the M14 ORM metadata before any M15 migration
was written.

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

The migration drops and recreates exactly these M14 Debt checks under their
existing names:

```text
ck_debts_status_allowed
ck_debts_status_metadata_matches_status
ck_debts_timestamp_order
```

All other M14 Debt checks, FKs, defaults and these existing indexes remain
untouched:

```text
ix_debts_shop_customer_id_created_at_id
ix_debts_shop_customer_id_status_due_date_id
ix_debts_status_pending_expires_at_id
```

Expanded
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

`ck_debts_timestamp_order` retains the complete M14 expression and adds, for a
marked Debt, `overdue_at >= accepted_at`, `updated_at >= overdue_at`, and (when
paid) `paid_at >= overdue_at`. Transition never changes original/discounted
money, due date, or acceptance metadata.

## Audit and idempotency extension

No audit table or generic event facility is created. Drop and recreate exactly
the five existing registry checks under the same names:

```text
ck_audit_log_event_type_allowed
ck_audit_log_object_type_allowed
ck_audit_log_actor_matches_event
ck_audit_log_object_matches_event
ck_audit_log_payload_exact_shape
```

The object-type allowed set itself does not change: `debt` already exists.
Extend the event/actor/object/payload registries only with:

| Event | Actor | Object | Exact safe payload keys |
| --- | --- | --- | --- |
| `debt.overdue` | SYSTEM | `debt` | `source`, `from_status`, `to_status`, `overdue_revision`, `business_date` |
| `debt.clawback_applied` | SYSTEM | `debt` | `source`, `from_basis`, `to_basis`, `balance_increase_uzs`, `overdue_revision` |

`payment.recorded` changes from exact `active -> active|paid` to the union of
exact `active -> active|paid` and `overdue -> overdue|paid`. Its five safe keys
remain unchanged. `debt.paid` keeps its exact M14 payload. Extra payload keys
remain rejected.

Idempotency has no schema delta. M15 payment uses v2 hash binding
`expected_balance_basis`; M14 v1 hash is only for completed legacy replay when
form basis is missing. Missing basis cannot enter a mutation command.

## Upgrade operation order

One M15 revision has `down_revision = "a5b6c7d8e9f0"` and, transactionally:

1. Adds nullable, default-free `overdue_at` and `overdue_revision` to `debts`.
2. Drops/recreates the three named M14 Debt checks, then creates the three named
   marker checks above.
3. Creates exactly `ix_debts_status_due_date_id(status, due_date, id)`.
4. Drops/recreates the five named audit registry checks for the two exact M15
   event shapes and lawful overdue payment transition.

It contains no `UPDATE`, historic-row loop, `now()`, `CURRENT_DATE`, or
due-date-derived write. Existing M14 rows retain every stored value and the two
new columns are NULL. It does not create `payments`, alter `payments`, alter
`idempotency_keys`, create a job table, or create a second Alembic head.
Registration changes only expose extended existing Debt/audit metadata; cleanup
ordering stays valid.

## Fail-closed downgrade

Before every downgrade DDL operation, evaluate all loss predicates and reject
when any is true:

```text
EXISTS (SELECT 1 FROM debts
        WHERE status = 'overdue'
           OR overdue_at IS NOT NULL
           OR overdue_revision IS NOT NULL)
EXISTS (SELECT 1 FROM audit_log
        WHERE event_type IN ('debt.overdue', 'debt.clawback_applied'))
EXISTS (SELECT 1 FROM audit_log
        WHERE event_type = 'payment.recorded'
          AND payload ->> 'from_status' = 'overdue')
EXISTS (SELECT 1 FROM debts
        WHERE NOT (<exact M14 status-allowed>
                   AND <exact M14 status/metadata shape>
                   AND <exact M14 timestamp-order shape>))
```

The event predicates remain even if corruption removed a marker or Debt row.
The exact M14-shape predicate and audit rollback payload come from immutable
revision-local SQL (the latter from the exact M14 predecessor revision), never
from live application models. Any true predicate raises stable `M15 downgrade blocked:`
`RuntimeError` before schema mutation. Late-paid rows are covered by retained
markers; overdue payment audit history is covered even without those rows.

Only with empty M15 lifecycle footprint may downgrade restore exact frozen M14
audit checks, drop `ix_debts_status_due_date_id`, drop the three M15-only Debt
checks, restore the three exact frozen M14 Debt checks, then drop
`overdue_revision` and `overdue_at`.
It restores M14 six statuses exactly, preserves M14 tables/Payment data, leaves
one M14 head, and loses no lawful M14 data.
