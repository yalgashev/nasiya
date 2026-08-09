# Nasiya M15 Scope Contract

M15 is the **Deterministic Overdue, Clawback, Late Payment and Debt-Derived
Hard-Block Foundation**. It is a bounded lifecycle extension to completed M14,
not a collections platform.

## Authority and frozen baseline

Authority is ordered as follows: `docs/tt_nasiya_web_v1.md`; final scope freeze
`/home/yalgashev/projects/nasiya_m15_00_final_scope_freeze.md`; final product
gate `/home/yalgashev/projects/nasiya_m15_product_gate.md`; these tracked M15
documents; M14 closeout/contracts; then repository evidence. The freeze controls
M15 staging and OUT boundaries.

M15 begins at repository `nasiya`, branch `main`, documentation-closeout HEAD
`881413608f16db054078448676d6fae71afe6221`, whose M14 implementation parent is
`2293b97459218b61ab796863b4c0a0522edbdb6a`. Alembic has one head,
`a5b6c7d8e9f0`. CI jobs `31295227718/93199127585` and
`31295448176/93199689855` each reported 3,919 passed and zero non-passes. The
frozen TT blob is `d77c0f0f330a1330155a4aee3c46b05d97cf5561` (SHA-256
`569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab`). M15
starts with no M15 product code or migration.

## Lifecycle, status, and monetary authority

M15 persists exactly `pending`, `active`, `rejected`, `cancelled`, `expired`,
`paid`, and `overdue`. Python may retain inherited `written_off` and
`written_off_settled` vocabulary, but M15 adds no runtime, persistence, route,
service, or workflow wiring for either. An accepted active Debt becomes overdue
exactly once when:

```text
tashkent_business_date(captured_now) > debt.due_date
```

The due date remains payable inclusively. A GET may project active past-due as
**effective overdue**, but never writes it. A locked mutation or bounded
materializer first makes it persisted overdue. The transition writes `status`,
`overdue_at`, and `overdue_revision`, and makes discounted repayment unavailable
for all later payments. Markers are nullable together; overdue and late-paid
Debt have both, while on-time paid Debt has neither.

For immutable payments, `posted_total` is the sum of all Payment amounts:

```text
discounted basis: max(discounted_amount_uzs - posted_total, 0)
original basis:   max(original_amount_uzs - posted_total, 0)
```

On-time active payment uses discounted basis. Partial late payment remains
`overdue`; exact original remaining becomes `paid`. Amounts remain positive,
whole UZS, and never exceed locked current remaining. No money or clawback
result is cached.

Historical receipt basis is marker-derived: Payment revision below
`overdue_revision` is discounted-basis history; above is original-basis history;
equality is incoherent and fails closed. Payments and their schema are not
rewritten. Open exposure is original amount for `pending`, original minus posted
total for `active`/`overdue`, and zero for `paid`; open count includes those
first three. Any effective or persisted open overdue Debt globally hard-blocks
new proposal and customer acceptance across Shops. It is debt-derived, not a
Customer flag/cache, and does not block lawful late repayment.

## Concurrency, time, and idempotency

The payment clock is a callable captured exactly once **after the Debt lock**.
Routes must not pass a datetime captured by a request dependency; a completed
replay returns before clock invocation. Hard-block business date is captured
once **after the Customer lock** for proposal and acceptance. Thus lock waits
across Tashkent midnight cannot use yesterday's terms or eligibility.

The forward lock order is `Shop -> ShopStaff -> TelegramLinkToken ->
OtpDispatch -> OtpChallenge -> User -> TelegramLink -> Customer ->
OfferVersion -> OfferAcceptance -> CustomerIdentity -> ObjectFile ->
CustomerDocument -> ShopCustomer -> IdempotencyKey -> Debt -> Payment ->
AuthSession`; unused classes are skipped and same-class UUIDs ascend. Overdue
materialization is `Shop -> Customer -> ShopCustomer -> Debt -> AuditLog`, not
a Debt-only bulk lock.

New forms require `expected_revision` and closed enum
`expected_balance_basis` (`discounted` or `original`). M15 v2 hash includes
basis. Missing basis is not a mutation command: it may enter only a narrow M14
v1 completed-key replay lookup using the v1 hash; no matching completed v1 key
is a zero-write denial. V2 replay retains normal precedence after actor/shop/
result visibility. Inline late materialization may advance revision by two;
already persisted overdue payment advances it by one.

## Persistence, audit, migration, and web boundary

The sole schema delta is nullable `debts.overdue_at TIMESTAMPTZ` and
`debts.overdue_revision INTEGER`, paired/positive/revision-bound/status/timestamp
checks, and `ix_debts_status_due_date_id(status, due_date, id)`. One Alembic
child of `a5b6c7d8e9f0` has no backfill, no second head, no new table, and no
Payment schema delta. Downgrade refuses for any overdue/late marker, status, or
M15 audit evidence.

Only safe immutable audit extensions are SYSTEM `debt.overdue` and
`debt.clawback_applied`, plus constrained existing payment/debt event transition
vocabulary. `paid_at` may populate once at lawful full payoff; original money,
due/acceptance data, and already-populated terminal metadata are immutable.

SSR forms expose hidden revision and basis. POST stays CSRF-protected PRG;
there is no JSON/API route, browser money calculation, or client financial
state. Tenant/customer receipt visibility stays server-resolved and generic on
absence or foreign access.

## Exact IN and OUT boundary

IN is deterministic inline rollover, a bounded idempotent service/harness
batch, original-basis clawback, late payment/receipt basis, global hard block,
the two Debt columns/check/index/audit extensions, v2 basis idempotency with
narrow completed M14-v1 replay, SSR tokens/copy, migration/downgrade evidence,
PostgreSQL race tests, and source-scoped containment tests.

OUT includes production scheduler, cron, queue worker, CLI, admin trigger,
job-run table, notification/outbox, rating/score/event/backfill/disclosure,
clawback reversal (CR-M6-03 remains deferred), void/refund/correction/import,
written-off lifecycle, reports/exports, public API, self-pay, fees/interest,
gateway/bank work, cache, new Customer state, and Payment rewrite. Batch is
callable only through application service and test/service harness: no new
production invocation surface is authorised.

## Required evidence

Evidence covers due-date/midnight barriers, exactly-once rollover/clawback,
batch overlap and races, cross-Shop block/unblock, basis drift and v1/v2
replay, receipt rebasing, overpayment, rollback/audit atomicity, access/privacy/
suspension, migration guards, and OUT containment. Denials write nothing,
except a valid pre-existing completed replay which writes nothing new.
