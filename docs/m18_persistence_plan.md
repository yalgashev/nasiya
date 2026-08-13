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

The predecessor order index
`ix_rating_events_shop_customer_occurred_debt_event` is replaced, not retained
in parallel. The post-upgrade rating index/unique facts are the cycle unique
`uq_rating_events_debt_event_source_revision`, the partial single-negative
unique `ux_rating_events_single_debt_negative_source`, the unchanged daily-cap
unique `ux_rating_events_positive_shop_customer_business_date`, and the exact
five-column order index
`ix_rating_events_shop_customer_occurred_debt_event_src_rev` (in addition to
the primary key). No score, balance, cap-slot, or cycle state is stored
elsewhere.

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

Each existing event needs exactly one coherent source. Candidate selection is
performed while all four source tables are locked, and `COUNT(*) = 1` is
required per RatingEvent before any row is updated:

| Event | Required source revision and facts |
| --- | --- |
| `on_time_paid` | `Payment.debt_revision_after` from the one Payment whose `id` is the object of a same-instant `payment.recorded` audit, whose Debt is the event Debt, whose revision equals the audit `debt_revision_after`, and whose same-instant Debt audit is `debt.paid` with the same revision. The Payment and Debt both resolve to the event ShopCustomer. |
| `overdue` | `Debt.overdue_revision` where event time equals `Debt.overdue_at`, with same-instant SYSTEM/DEBT `debt.overdue` and `debt.clawback_applied` audits whose `overdue_revision` equals that marker. Both audits use the same predecessor source (`inline_payment` or `batch`) and the Debt resolves to the event ShopCustomer. |
| `written_off` | `Debt.written_off_revision` where event time equals `Debt.written_off_at`, with a same-instant USER/DEBT `debt.written_off` audit whose `written_off_revision` equals that marker and whose Debt resolves to the event ShopCustomer. |
| `written_off_settled` | `Payment.debt_revision_after` from the one terminal recovery Payment whose revision equals `Debt.written_off_settled_revision`, whose `created_at` equals both event time and `Debt.written_off_settled_at`, and which has same-instant `payment.recorded` (object Payment) and `debt.written_off_settled` (object Debt) audits with that revision. The Payment and Debt both resolve to the event ShopCustomer. |

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
indexes/FKs/table first; restore M17 rating checks, the
`uq_rating_events_debt_id_event_type` unique, and
`ix_rating_events_shop_customer_occurred_debt_event`; restore audit/key checks;
drop `source_revision`; and drop
`uq_payments_id_debt_id_debt_revision_after` last. The independent guards run
before every one of those destructive steps. Real-PostgreSQL proof covers
fresh upgrade/empty downgrade/re-upgrade, mixed M17 deterministic mapping,
independent guard classes, schema constraints, source preservation, and one head.
