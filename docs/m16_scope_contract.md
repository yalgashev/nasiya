# Nasiya M16 Scope Contract

M16 is the **Global Rating Ledger and Privacy-Safe Risk-Band Disclosure Foundation**. It is a bounded M14/M15 vertical: immutable `+5/-15` source events, deterministic score/band derivation, anti-farming, and audited band-only disclosure. It is not a collections, admin-rating, or scheduler platform.

## Authority and frozen baseline

Authority is ordered as follows: `docs/tt_nasiya_web_v1.md`; final scope freeze `/home/yalgashev/projects/nasiya_m16_00_final_scope_freeze.md`; final product gate `/home/yalgashev/projects/nasiya_m16_product_gate.md`; these tracked M16 documents; remote-GREEN M15 contracts/evidence; then repository evidence. The freeze controls M16 staging and every OUT boundary; deferred TT requirements are not cancelled.

M16 starts at repository `nasiya`, branch `main`, documentation-closeout HEAD `547723ffc8e4148c5b4de86763b7c5add0588e86`, tree `a8bc494c90dde3cf186b49aad8b6b8470af99c00`. Its eighth M15 implementation parent is `13bda85fb5df99d1be2b1da578e0f1a256f1d336`, tree `f99e1de25361438171ae26d1c4bc27d041d3b042`. Alembic has one head, `b6c7d8e9f0a1`; implementation CI job `31347914959/93333216249` reported `4090 passed in 213.63s`, and docs-closeout CI job `31348199466/93334007139` reported `4090 passed in 196.59s`, both with zero test non-passes. The frozen TT blob is `d77c0f0f330a1330155a4aee3c46b05d97cf5561` (SHA-256 `569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab`).

M16.00 owner approval is recorded externally. Protected planning-file start evidence is: Product Gate SHA-256 `1c57d9a31d5b02275925a37b59025c48c85908af45a34e837eacf0824944f379`; Scope Freeze SHA-256 `2c094903780a5f3526e6275162501974d058831deda55a9b4b593cc88a105b25`; and micro-task guide SHA-256 `5fa102979315cef760f35607da0b272b0aa25d46f704342d360819586377343f`. M16.06 creates authority only; it does not claim product implementation.

## Rating vocabulary, score, and band

The only persisted M16 event vocabulary is:

```text
on_time_paid -> +5
overdue      -> -15
recording_source = live | historical_reconciliation
```

The authoritative Customer-global ledger order and fold are:

```text
ordered_events = ORDER BY occurred_at ASC, debt_id ASC, event_type ASC
score_0 = 60
score_i = min(100, max(0, score_(i-1) + delta_i))
```

Final-sum-only clamp is forbidden. Score is derived from immutable events, not stored on Customer, Debt, Payment, or ShopCustomer. Canonical persisted band strings are lowercase `new|green|yellow|red|blocked`; uppercase names below are presentation labels only. A Customer with no event is `NEW` even though its internal initial score is 60. Otherwise `GREEN=75..100`, `YELLOW=50..74`, and `RED=0..49`. The M15 hard-block overlay has precedence:

```text
if global_hard_block: BLOCKED
elif event_count == 0: NEW
elif 75 <= score <= 100: GREEN
elif 50 <= score <= 74: YELLOW
else: RED
```

`BLOCKED` is not a numeric score. `RED` and `YELLOW` never deny a new Debt or lawful repayment; only the inherited Debt-derived hard block remains authoritative for proposal/acceptance denial.

## Exact source and anti-farming contract

Live `on_time_paid` is considered only after completed replay resolution, current authority, durable key resolution, the already locked Customer chain, the Debt lock, and the one M15 post-lock Payment clock capture. It is eligible only when all conditions hold:

```text
pre_status == active
payment_amount == discounted_remaining
post_status == paid
overdue_at is NULL and overdue_revision is NULL
original_amount_uzs >= 100000
tashkent_business_date(accepted_at) < tashkent_business_date(payment_created_at)
tashkent_business_date(payment_created_at) <= due_date
no on_time_paid for (shop_customer_id, payment_business_date)
```

`accepted_at`, never pending `created_at`, is the exposure anchor. Partial, same-day, below-threshold, late, effective/persisted-overdue, replay, daily-cap loser, and incoherent-ledger cases receive no `+5`; a daily-cap loser still has its lawful Payment and source audits. The cap key is exactly `(shop_customer_id, tashkent_business_date(payment_created_at))`.

Each lawful `active -> overdue` produces one `overdue` event regardless of amount, discount, prior partial payments, or positive cap. Its source instant is `debt.overdue_at`; event time equals that marker and the event business date is its Tashkent date. `(debt_id, event_type)` is unique. Live source events are append-only: no update, delete, reversal, compensation, or manual adjustment.

## Historical reconciliation and hard-block composition

Upgrade backfills only coherent immutable M14/M15 facts. A historical positive requires paid status/`paid_at`, no overdue markers, original amount threshold, accepted/paid/due date predicate, exact discounted Payment sum, terminal Payment at `paid_at` with final revision, and lawful `payment.recorded` plus `debt.paid` USER audits. Partition by pair/Tashkent paid day and retain earliest `(paid_at, debt.id)` only. A historical negative requires status `overdue|paid`, non-null paired marker/revision, and exact matching SYSTEM `debt.overdue` plus `debt.clawback_applied` audit facts at the marker/revision. Event identifiers are deterministic UUIDv5 from the revision-local namespace and `(event_type, debt_id)`.

An active past-due Debt lacking a persisted marker is never backfilled: it is effective `BLOCKED` until a future lawful materialization writes `-15`. Contradictory source data aborts the whole migration. Reconciliation inserts rating rows only, records `historical_reconciliation`, creates neither generic retroactive audit nor notification, and never updates Debt, Payment, or Audit.

The existing M15 boolean remains authoritative:

```text
persisted unpaid overdue
OR active due_date < tashkent_business_date(trusted_as_of)
```

Disclosure reads that reader after Customer lock plus ordered rating events to derive a safe band. Late full payoff removes only the overlay when no other effective/persisted overdue remains; the `-15` history remains.

## Transaction, lock, disclosure, and privacy boundary

The inherited forward order is extended at the append tail:

```text
Shop -> ShopStaff -> User -> Customer -> ShopCustomer -> IdempotencyKey
-> Debt -> Payment -> RatingEvent -> DisclosureViewLog -> AuditLog
```

Unused classes are skipped and same-class UUID locks ascend. On-time payment stages `Debt -> Payment -> optional RatingEvent -> AuditLog`; overdue batch stages `Debt -> RatingEvent -> AuditLog`; inline late payment stages `Debt -> Payment -> RatingEvent(-15) -> AuditLog`. The current overdue helper must be refactored to return a typed pending overdue effect before it appends audits, so the old audit-before-inline-Payment order cannot survive. Repositories borrow a Session and never commit, rollback, or close; one coordinator owns the transaction. `app.debt` and `app.payment` use local structural ports, never a concrete `app.rating` model/repository import. One composition-wired adapter reuses the locked Customer and has no production no-op/default path.

Fresh disclosure is only an active current-Shop OWNER/MANAGER/CASHIER POST for an exact linked ShopCustomer, with CSRF, a closed purpose (`debt_proposal_review`, `credit_limit_review`, or `existing_debt_review`), and canonical idempotency key. Its v1 request hash binds actor, current Shop, ShopCustomer, and purpose. POST locks through Customer, resolves replay before clock/rating, snapshots only `band`, `purpose`, and `viewed_at`, writes one `disclosure.risk_band_viewed` USER audit, and 303 redirects. Same key/hash returns the original snapshot; a different hash conflicts.

M16 adds exactly these SSR routes:

```text
POST /shop/customers/{shop_customer_id}/risk-band-disclosures
GET  /shop/risk-band-disclosures/{disclosure_view_id}
```

GET is historical retrieval only: it creates no M16/domain row, does not lock the target or recompute band. A suspended Shop denies fresh POST but a still-live authorised member may read that actor/Shop's immutable historical snapshot. Revoked, foreign, guessed, corrupt, or missing results are generic unavailable. Platform-admin status is not a membership bypass.

Shop presentation contains only band, purpose, and viewed time. It must not contain score, delta, event/history/count, amount/balance, other-Shop fact, hard-block cause, PII, business identifiers, raw key, digest, request hash, or internal authority IDs. The only narrow transports are an authorised ShopCustomer POST action locator, transient raw key in a same-origin no-store hidden field, and opaque disclosure-view locator in PRG Location/GET path. All templates are UZ-Latn/RU, SSR/autoescaped, no-store and CSP compatible; browser rating calculation/storage is forbidden.

## Threat model and proof obligation

| Threat | Frozen control and required evidence |
| --- | --- |
| Duplicate payment/overdue source or batch/inline overlap | Customer serialization, unique `(debt_id,event_type)`, and source/audit atomicity; deterministic duplicate and overlap barriers prove one event only. |
| Same-day `+5` farming race | Customer lock plus partial unique positive cap; concurrent eligible payments prove exactly one winner and lawful loser Payment/audits. |
| Lock inversion or accidental Customer re-lock | One parent-chain order, local structural ports, and composition-only adapter; deterministic combined-flow barrier proves forward acquisition without timing workarounds. |
| Corrupt/partial or old-writer migration source | Mandatory drain/restart prohibition, `LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE`, coherent fact predicates, transaction rollback, and guarded downgrade tests. |
| Effective overdue silently acquiring retroactive debt | Marker-free active past-due stays only `BLOCKED`; migration and disclosure tests prove no `-15` or GET mutation. |
| Disclosure replay, stale recomputation, IDOR, or data leak | Canonical actor/Shop/ShopCustomer/purpose hash, immutable snapshot GET, current-Shop parent-chain lookup, generic unavailable, and web/security/browser-storage evidence. |
| Suspended/revoked authority confusion | Live active membership for POST; suspended own-Shop historical GET only; revoked/foreign denial tests. |

## Exact IN, OUT, and required evidence

IN is typed rating/band/source/purpose contracts; sequential fold; immutable ledger; deterministic M14/M15 reconciliation; live exact `+5/-15`; daily cap; effective hard-block overlay; two-table persistence; disclosure idempotency and audit; two SSR routes; UZ/RU/mobile/accessibility/privacy; one guarded Alembic child; real PostgreSQL migration/concurrency/privacy evidence; and M1–M15 compatibility containment.

OUT includes written-off/settlement/`-40/+10`; Payment void/refund/edit/delete/correction/import or compensation; overrides/settings/free adjustment; cached score/band/event-count/hard-block fields; Customer/admin/raw score UI; global search; scheduler/cron/worker/CLI/job-run/retry; notification/outbox; reports/export; public API/JSON/HTMX; self-pay; generic event infrastructure; and new runtime dependencies.

Required evidence is real PostgreSQL source/backfill/corruption/downgrade and deterministic barrier tests; source/fault rollback; same-source and daily-cap races; inline/batch overlap; lock-order proof; band/privacy/IDOR/CSRF/PRG and browser-storage checks; static OUT guards; synthetic Chrome/PostgreSQL acceptance; and exact-SHA remote closure. No skip, xfail, sleep, retry, timeout, NOWAIT, SKIP LOCKED, advisory lock, SQLite, `create_all`, or manual DDL substitute is acceptable.
