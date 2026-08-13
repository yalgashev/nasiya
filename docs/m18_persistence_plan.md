# Nasiya M18 Persistence Plan

## Revision boundary

This plan is frozen against protected baseline
`d341edf95511653d566726826304a74b3b3ffb60` /
`aedd8ef31a66e1bd15481e2f5079506f2bde61df`, head `d8e9f0a1b2c3`, and the
TT/external SHA-256 evidence in `m18_scope_contract.md` and `m18_decisions.md`.

One **PLANNED** linear revision is required:

```text
revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
```

It is the sole child/head and adds exactly one table, `payment_voids`. No new
Debt or Payment business column is allowed.

## PaymentVoid schema and predecessor chains

`payment_voids` has exactly nine columns:

```text
id UUID PK
payment_id UUID NOT NULL
debt_id UUID NOT NULL
shop_customer_id UUID NOT NULL
source_payment_revision INTEGER NOT NULL
debt_revision_after INTEGER NOT NULL
voided_by_user_id UUID NOT NULL
reason TEXT NOT NULL
voided_at TIMESTAMPTZ NOT NULL
```

Add parent unique `uq_payments_id_debt_id_debt_revision_after` on
`(id, debt_id, debt_revision_after)`. Keep existing
`uq_payments_debt_id_debt_revision_after` and use existing
`uq_debts_id_shop_customer_id` for the Debt chain. Child constraints are:

```text
pk_payment_voids
fk_payment_voids_payment_debt_revision
  (payment_id,debt_id,source_payment_revision)
  -> payments(id,debt_id,debt_revision_after) ON DELETE RESTRICT
fk_payment_voids_debt_shop_customer
  (debt_id,shop_customer_id) -> debts(id,shop_customer_id) ON DELETE RESTRICT
fk_payment_voids_voided_by_user_id_users_id ON DELETE RESTRICT
uq_payment_voids_payment_id
uq_payment_voids_debt_id_debt_revision_after
ck_payment_voids_reason_allowed
ck_payment_voids_source_payment_revision_positive
ck_payment_voids_debt_revision_after_positive
ck_payment_voids_revision_order
ix_payment_voids_shop_customer_voided_at_id
```

`ck_payment_voids_revision_order` requires
`source_payment_revision < debt_revision_after`. The reason check is exactly
the five frozen closed values.

## Rating, audit, and idempotency deltas

Add `rating_events.source_revision INTEGER`, explicitly populate it, then make
it NOT NULL and add `ck_rating_events_source_revision_positive`. Drop
`uq_rating_events_debt_id_event_type`; add:

```text
uq_rating_events_debt_event_source_revision
  (debt_id,event_type,source_revision)
ux_rating_events_single_debt_negative_source
  UNIQUE (debt_id,event_type) WHERE event_type IN ('overdue','written_off')
ix_rating_events_shop_customer_occurred_debt_event_src_rev
  (shop_customer_id,occurred_at,debt_id,event_type,source_revision)
```

Preserve `ux_rating_events_positive_shop_customer_business_date` with its exact
`event_type='on_time_paid'` predicate. Extend
`ck_rating_events_event_type_allowed`, `ck_rating_events_delta_matches_event`,
and `ck_rating_events_recording_source_allowed` only for live
`on_time_paid_voided/-5` and `written_off_settled_voided/-10`.

Extend `ck_audit_log_event_type_allowed`, `ck_audit_log_object_type_allowed`,
`ck_audit_log_actor_matches_event`, `ck_audit_log_object_matches_event`, and
`ck_audit_log_payload_exact_shape` for `payment.voided` USER/PAYMENT and
`debt.reopened_after_payment_void` USER/DEBT. The existing SYSTEM
`debt.overdue` and `debt.clawback_applied` payload checks narrowly accept
`source='payment_void'`; only overdue accepts `from_status='paid'`. Extend
`ck_idempotency_keys_endpoint_result_pair_allowed` with
`shop.payments.void` and existing result type `payment`.

## Explicit metadata backfill and preservation

This is an **explicit metadata backfill**, not a no-backfill migration. Its
first source-data action, after old-writer drain and proof of no in-flight old
transaction, is:

```sql
LOCK TABLE debts, payments, rating_events, audit_log IN SHARE ROW EXCLUSIVE MODE;
```

Old binaries must not restart after upgrade. The only source-row DML populates
new `rating_events.source_revision`; no PaymentVoid or compensation is
synthesized. Existing RatingEvent business columns and every Debt, Payment,
AuditLog, and IdempotencyKey value remain unchanged.

Each existing event needs exactly one coherent source:

| Event | Required source revision and facts |
| --- | --- |
| `on_time_paid` | Terminal Payment `debt_revision_after`, matching instant/Debt/ShopCustomer plus canonical payment-recorded and debt-paid audits. |
| `overdue` | `Debt.overdue_revision`, matching overdue marker and both SYSTEM overdue/clawback audits. |
| `written_off` | `Debt.written_off_revision`, matching marker and USER/Debt write-off audit. |
| `written_off_settled` | Terminal recovery Payment revision, matching settlement marker plus payment-recorded and settlement audits. |

No candidate, multiple candidates, wrong time/revision/Debt/ShopCustomer/audit,
or forbidden recording source aborts upgrade.

## Guarded downgrade

`_guard_m18_downgrade_loss()` runs before destructive DDL and independently
denies downgrade for: any `payment_voids` row; any compensation RatingEvent;
a duplicate M17 type per Debt that predecessor uniqueness cannot represent; a
`payment.voided` or `debt.reopened_after_payment_void` audit; a
`shop.payments.void` idempotency row; any M18-only `source=payment_void` audit
payload; or any `source_revision` inconsistent with reconstructible M17 facts.
It deletes, rewrites, and compensates nothing.

Only an M18-empty, M17-compatible database may downgrade. Then drop child
indexes/FKs/table, restore M17 rating checks/unique/order index and audit/key
checks, drop `source_revision`, and drop
`uq_payments_id_debt_id_debt_revision_after` last. Real-PostgreSQL proof covers
fresh upgrade/empty downgrade/re-upgrade, mixed M17 deterministic mapping,
independent guard classes, schema constraints, source preservation, and one head.
