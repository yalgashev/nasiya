# Nasiya M18 Scope Contract

M18 is **Idempotent Payment Void & Rating Compensation**: an authorized Shop actor
may void only the latest non-voided Payment without changing the original Payment.
The void is append-only and atomically restores lawful Debt, balance, rating, and
hard-block state. It is not a refund, reversal, or broad operations platform.

## Authority and frozen start

Precedence is TT, external M18 Final Scope Freeze, external M18 Product Gate,
these four tracked M18 documents, remote-GREEN M17 evidence, then repository
evidence. TT and all three external M18 planning files are read-only.

M18.01–05 start evidence is M17 implementation
`5da0ee8d24e0f68f1597e4c96c66237909f8676c`, tree
`dbee3dfbe8663a8b1098d932b91325ae9821b35a`, run/job
`31688853605` / `94411245048`, `4371 passed in 226.71s`; M17 docs closeout is
`d341edf95511653d566726826304a74b3b3ffb60`, tree
`aedd8ef31a66e1bd15481e2f5079506f2bde61df`, run/job
`31689365507` / `94412857756`, `4371 passed in 238.67s`. Start is clean,
`HEAD == origin/main`, divergence `0 0`, and the sole Alembic head is
`d8e9f0a1b2c3`.

Protected evidence is exact:

| Artifact | Blob / SHA-256 |
| --- | --- |
| TT | `d77c0f0f330a1330155a4aee3c46b05d97cf5561` / `569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab` |
| Product Gate | `0b1c4b8135678ffd95db4249307dad8a11dfeccc28f1bcd5fca64ee2f43b03cc` |
| Final Scope Freeze | `d676ba09836306f6fee6e007a1b4dd91e394e239edf7356013f26078ec46e5d6` |
| Microtask guide | `4a77a4ee3c6d959383b6a0e185ac899e4d1fbada9d2dc477b9eed1f72e1cafed` |

A protected-hash, ancestry, clean-tree, or one-head mismatch blocks M18 until
the Product Gate is reconciled. The M18.06 resulting docs-only state is not
remote-GREEN or an implementation checkpoint.

## Frozen capability and state

Only a live active-Shop `OWNER|MANAGER` may void. CASHIER, Customer,
platform-admin status, another Shop membership, and hidden UI are never
substitutes. Inactive Customer does not prevent correction of an otherwise
coherent existing tenant ledger. A closed reason is exactly:

```text
duplicate_payment | incorrect_amount | incorrect_method | payment_not_received | wrong_debt
```

The target is the maximum `Payment.debt_revision_after` among Payments for the
locked Debt that have no PaymentVoid. UUID, creation time, and client ordering
are not authority. A Payment is voided at most once. Original Payment amount,
method, actor, Debt, revision, and recorded time remain immutable.

```text
active/overdue/written_off + partial void       -> same status
paid + terminal void, no overdue marker         -> active when void date <= due date
paid + terminal void, no overdue marker, late   -> overdue + canonical -15
paid + terminal void, overdue marker exists     -> overdue
written_off_settled + terminal recovery void    -> written_off
```

Every successful void increments Debt revision once and sets
`updated_at == voided_at`. It clears only the current `paid_at` or current
settlement marker pair as applicable; accepted, overdue, and write-off markers,
reason, actor, original Payments, historic ratings, voids, and audits remain.
Resulting non-voided remaining must be positive; incoherent source fails closed.

## Money, source history, and rating

Current posted total is exact whole-UZS ledger arithmetic:

```text
SUM(Payment.amount_uzs) WHERE NOT EXISTS PaymentVoid(payment_id = Payment.id)
```

At Debt revision `R`, include Payment `P` iff `P.debt_revision_after <= R` and
there is no PaymentVoid for it with `debt_revision_after <= R`. Current reads
use the anti-join; historical receipt reads use this revision-as-of predicate.
No float, stored balance, negative clamp, or cache is authoritative.

Every RatingEvent has positive `source_revision`. The four migrated source
mappings are `on_time_paid -> terminal Payment revision`,
`overdue -> Debt.overdue_revision`, `written_off -> Debt.written_off_revision`,
and `written_off_settled -> Debt.written_off_settled_revision`. Migration is
an **explicit metadata backfill** of only this new column: old RatingEvent
business columns and all Debt/Payment/Audit/Idempotency values stay unchanged.
Missing, duplicate, contradictory, or forbidden source facts abort upgrade.

Live M18 compensation is only:

```text
on_time_paid_voided = -5
written_off_settled_voided = -10
```

It must pair to the exact voided Payment's matching `+5` or `+10`, source
revision, time, Debt, ShopCustomer, and audit chain. `-15` and `-40` are
never compensated or deleted. A no-positive lawful +5 outcome creates no
negative. A later lawful terminal Payment or recovery uses a fresh source
revision; a compensated +5 still consumes its customer-Shop/business-day slot,
while a later eligible day may re-earn. +10 has no daily cap.

Authoritative fold order and per-event clamp are:

```text
ORDER BY occurred_at, debt_id, event_type, source_revision
score_i = min(100, max(0, score_(i-1) + delta_i))
```

Lawful cycles permit source-linked `+5/-5` cycles before at most one overdue,
then optional written-off and source-linked `+10/-10` cycles. Compensation
cannot precede or duplicate its positive. Hard block remains a Debt-derived
effective/persisted overdue or unresolved written-off condition, never a score
shortcut. Fresh disclosure/create/accept sees a complete pre- or post-void
Customer-serialized state; existing snapshots are immutable.

## Atomic authority, audit, and web boundary

Detached scalar discovery resolves the server-side Debt identity for the hash;
it is not authority. The exact forward order is:

```text
Shop -> ShopStaff -> actor User -> Customer -> ShopCustomer -> IdempotencyKey
-> Debt -> Payment -> PaymentVoid -> RatingEvent -> AuditLog
```

Customer is the global serialization pivot and is never re-locked. Repositories
borrow the caller Session and never commit, roll back, or close it. Payment uses
only a local structural rating port; the concrete rating adapter is wired in
`app.main`, with no concrete inverse import or production no-op.

The endpoint is `shop.payments.void` with existing `payment` result type. Its
v1 hash binds actor User, current Shop, Payment, server-resolved Debt, expected
Debt revision, and normalized reason. Same key/hash returns the original Payment
result after current authority recheck but before clock, money, rating, or
mutation; different hash conflicts with zero writes. The raw key is only a
same-origin no-store hidden POST value and is never persisted, logged, rendered,
or placed in an error/audit/repr.

One NEW path stages transactional key, then Debt, PaymentVoid, exact
compensation, optional paid-to-overdue `-15`, SYSTEM overdue/clawback audits,
`payment.voided`, and only on status change
`debt.reopened_after_payment_void`. One caller-owned transaction makes all
facts durable together; any key, FK, unique, flush, rating, or audit fault rolls
all of them back.

The only new routes are:

```text
GET  /shop/payments/{payment_id}/void
POST /shop/payments/{payment_id}/void
```

POST is CSRF-protected PRG to the existing Shop receipt for NEW and REPLAY.
Shop receipt/history may show voided status/time and a localized closed reason
label, never actor. Customer may show only voided status/time; reason, actor,
raw rating, cause, other-Shop facts, keys/hashes, PaymentVoid ID, and internal
IDs remain absent from safe projections, errors, logs, repr, flash, and audit
payloads.

## Exact IN, OUT, and first evidence

IN is append-only latest-only void, current/as-of anti-join money, lawful reopen,
paid-after-due canonical overdue/clawback/-15, source-linked -5/-10, rating
cycles/re-earn, hard-block and disclosure composition, one schema child,
idempotency/audit, exactly two SSR routes, and focused static/unit/PG/web/barrier
and manual evidence.

OUT is refund/payout/chargeback; unvoid; Payment edit/delete/correction;
arbitrary historical void; forgiveness; write-off reversal/reason edit; -15/-40
compensation; score override/settings; scheduler/worker/CLI/retry; notification/
outbox; reports/export; Customer/admin/bulk/search/API/JSON/HTMX void surfaces;
cached money/rating/block; new currency/fee/interest/installments; new runtime
dependency; and M19 code.

Required first-failure evidence covers unauthorized/IDOR, latest/double void,
same/different key, stale revision, void interleavings with Payment/overdue/
write-off/disclosure/create/accept, missing or duplicate positive source,
re-earn farming, fault rollback, privacy, metadata ambiguity, old writer/data
loss, downgrade loss, and OUT containment. Concurrency evidence is deterministic
`Barrier`/`Event`, never sleep, retry, timeout, `NOWAIT`, or `SKIP LOCKED`.

| Frozen threat | PLANNED first-failure evidence |
| --- | --- |
| Unauthorized role or IDOR | `test_owner_manager_only_void_authority_and_generic_tenant_denials`; `test_void_role_suspension_idor_and_guessed_locator_matrix` |
| Earlier/latest/double target | `test_latest_non_voided_revision_stack_and_one_void_per_payment` |
| Same/different key or stale revision | `test_same_key_waits_to_replay_and_changed_hash_is_zero_write_conflict`; `test_stale_expected_revision_denies_before_void_clock_and_writes` |
| Void versus Payment/overdue/write-off | `test_void_vs_new_payment_preserves_one_linear_latest_stack`; `test_void_vs_overdue_batch_is_complete_old_or_new_state`; `test_void_vs_write_off_and_recovery_is_one_lawful_state` |
| Void versus disclosure/create/accept | `test_void_vs_disclosure_create_accept_has_complete_balance_score_block_snapshot` |
| Wrong or duplicate positive source | `test_compensation_requires_exact_positive_payment_revision_time_and_audit`; `test_duplicate_compensation_unique_wait_fails_closed_or_replays_exactly` |
| Re-earn farming | `test_compensated_daily_slot_remains_consumed_and_later_day_reearns`; `test_settlement_cycle_compensation_cannot_farm_bonus` |
| Partial commit or mixed block/score | `test_each_void_stage_fault_rolls_back_key_debt_void_rating_and_audits`; `test_cross_shop_void_disclosure_sees_complete_pre_or_post_state` |
| Reason/actor or identifier leak | `test_shop_customer_void_projection_privacy_matrix`; `test_void_repr_log_and_template_have_no_reason_actor_or_raw_key_leak` |
| Metadata ambiguity, old writer, or data loss | `test_each_four_type_source_revision_missing_duplicate_or_mismatch_aborts_upgrade`; `test_m17_fixture_backfill_preserves_all_old_business_values_and_drain_lock_is_first` |
| OUT creep | `test_m18_runtime_and_routes_exclude_exact_out_vocabulary` |
