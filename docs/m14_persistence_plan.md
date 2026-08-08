# M14 Persistence and Guarded-Downgrade Plan

This is the executable design authority for M14.19. It records the delta from
the immutable M13 head before any Payment ORM model or Alembic revision is
written. The implementation must remain one PostgreSQL/Alembic revision and
must not use `create_all`, SQLite, manual operator DDL, or a second head.

## Audited M13 baseline

- The single current revision is `f4a5b6c7d8e`; the M14 revision has
  `down_revision = "f4a5b6c7d8e"`.
- `debts` persists exactly `pending`, `active`, `rejected`, `cancelled`, and
  `expired`; it has no `paid_at` column. Its three indexes stay unchanged.
- `idempotency_keys` has separate endpoint and result-type checks plus the
  existing `(actor_user_id, endpoint, key_digest)` unique constraint.
- `audit_log` has exact event, object, actor/event, object/event, and payload
  checks. M14 extends those checks rather than adding an event table or bus.
- Runtime registration is in `app/db.py`, Alembic registration is in
  `alembic/env.py`, and `tests/postgresql.py:M2_CLEANUP_TABLE_NAMES` is the
  authoritative FK-safe test cleanup list.
- The M13 downgrade guard is source-scoped. The M14 revision must likewise own
  frozen M13 and M14 SQL snapshots; historical restoration must not import a
  later live `app.audit.models` payload expression.

## Exact Payment metadata delta

The only new table is `payments`, with columns in this exact order:

| Column | PostgreSQL type | Null/default contract |
| --- | --- | --- |
| `id` | UUID | primary key, non-null, application pre-generated |
| `debt_id` | UUID | non-null |
| `recorded_by_user_id` | UUID | non-null |
| `amount_uzs` | NUMERIC(18,0) | non-null, no default |
| `method` | TEXT | non-null, no default |
| `debt_revision_after` | INTEGER | non-null, no default |
| `created_at` | TIMESTAMPTZ | non-null, no default; explicit captured UTC time |

The exact named constraints are:

| Name | Contract |
| --- | --- |
| `pk_payments` | primary key (`id`) |
| `ck_payments_amount_uzs_bounds` | `amount_uzs BETWEEN 1 AND 1000000000000` |
| `ck_payments_method_allowed` | `method IN ('cash', 'card', 'transfer', 'other')` |
| `ck_payments_debt_revision_after_positive` | `debt_revision_after > 0` |
| `uq_payments_debt_id_debt_revision_after` | unique (`debt_id`, `debt_revision_after`) |
| `fk_payments_debt_id_debts_id` | `debt_id -> debts.id ON DELETE RESTRICT` |
| `fk_payments_recorded_by_user_id_users_id` | `recorded_by_user_id -> users.id ON DELETE RESTRICT` |

There is no secondary Payment index. `pk_payments` serves the joined receipt
locator; the unique B-tree on `(debt_id, debt_revision_after)` serves forward
and reverse per-Debt ledger ordering. `id` is only a defensive tie-breaker in
queries because revision-after is already unique for a Debt. No mutable or
cached `updated_at`, `status`, `void`, `note`, `reference`, `balance`,
`remaining`, `exposure`, `customer`, `shop`, or PII column is allowed.

`created_at` deliberately has no ORM or database default. The mutation
coordinator captures the server UTC instant once after locking the Debt and
writes that same value to Payment `created_at`, Debt `updated_at`, and, for a
full payoff, Debt `paid_at`.

## Exact existing-table deltas

### Debt

Add nullable `debts.paid_at TIMESTAMPTZ` with no default. Replace only these
checks, preserving their names:

- `ck_debts_status_allowed` permits exactly `pending`, `active`, `rejected`,
  `cancelled`, `expired`, and `paid`.
- `ck_debts_status_metadata_matches_status` adds `paid_at IS NULL` to every
  non-paid branch. Its paid branch requires `accepted_at IS NOT NULL`,
  `paid_at IS NOT NULL`, and all rejection/cancellation/expiry timestamps and
  reasons null.
- `ck_debts_timestamp_order` preserves every M13 timestamp rule and additionally
  requires `paid_at >= accepted_at >= created_at` and `updated_at >= paid_at`
  whenever `paid_at` is non-null.

All other Debt columns, checks, foreign keys, defaults, and these indexes remain
byte-for-byte equivalent in meaning:

- `ix_debts_shop_customer_id_created_at_id`
- `ix_debts_shop_customer_id_status_due_date_id`
- `ix_debts_status_pending_expires_at_id`

### Idempotency

Replace `ck_idempotency_keys_endpoint_allowed` and
`ck_idempotency_keys_result_object_type_allowed` with the single exact pairwise
check `ck_idempotency_keys_endpoint_result_pair_allowed`:

```text
(endpoint = 'shop.debts.create' AND result_object_type = 'debt')
OR
(endpoint = 'shop.debt_payments.create' AND result_object_type = 'payment')
```

This rejects both crossed pairs and all unknown pairs. Digest/hash checks,
columns, actor RESTRICT FK, timestamp, and
`uq_idempotency_keys_actor_user_id_endpoint_key_digest` remain unchanged.

### Audit

Extend the existing five exact registry/payload checks with only:

| Event | Actor | Object | Exact payload keys |
| --- | --- | --- | --- |
| `payment.recorded` | USER | `payment` | `amount_uzs`, `method`, `from_status`, `to_status`, `debt_revision_after` |
| `debt.paid` | USER | `debt` | `source`, `debt_revision_after` |

`payment.recorded` requires whole `amount_uzs` in
`1..1000000000000`, an exact four-value method, `from_status='active'`,
`to_status IN ('active','paid')`, and a positive revision. `debt.paid` requires
`source='payment'` and a positive revision. Extra payload keys are rejected.
No Audit column, FK, index, system-actor event, arbitrary event, or arbitrary
object is added.

## Upgrade operation order

One M14 revision performs these operations transactionally:

1. Add nullable, default-free `debts.paid_at`.
2. Replace the three Debt checks with the six-status/paid metadata versions.
3. Create `payments` after its existing `users` and `debts` FK parents, with the
   exact seven columns and seven named constraints above.
4. Replace the two independent idempotency allow-list checks with the one
   pairwise check.
5. Replace the five extensible audit checks with their frozen M14 forms.

Companion changes register `app.payment.models` in both `app/db.py` and
`alembic/env.py`, put `payments` before `idempotency_keys`, `debts`, and `users`
in PostgreSQL cleanup order, and update the CI exact-head assertion to the one
new M14 revision. They do not create another revision.

## Fail-closed downgrade and exact M13 restoration

Before any downgrade DDL, the revision evaluates all four predicates:

```text
EXISTS (SELECT 1 FROM payments)
EXISTS (SELECT 1 FROM idempotency_keys
        WHERE endpoint = 'shop.debt_payments.create'
           OR result_object_type = 'payment')
EXISTS (SELECT 1 FROM debts WHERE status = 'paid' OR paid_at IS NOT NULL)
EXISTS (SELECT 1 FROM audit_log
        WHERE event_type IN ('payment.recorded', 'debt.paid'))
```

Any true predicate raises a stable M14 downgrade-blocked `RuntimeError` before
schema mutation. This includes structurally corrupted payment-result rows and
therefore fails closed beyond the lawful pairwise state.

Only when all four predicates are false may downgrade:

1. Restore the exact frozen M13 audit event/object/actor/object-map/payload
   checks from revision-local M13 SQL, never from mutable live model source.
2. Drop `ck_idempotency_keys_endpoint_result_pair_allowed` and recreate the
   exact two M13 checks and names.
3. Drop `payments`, whose FK children are empty by guard.
4. Restore the exact three M13 Debt check expressions, then drop `paid_at`.

The result must compare as exact M13 metadata: no Payment table, no `paid_at`,
five Debt statuses, the two M13 idempotency checks, the M13 audit registry and
payload shapes, unchanged M13 indexes/FKs/defaults, one head, and no data loss.
