# Nasiya M14 Scope Contract

M14 is the **Tenant-Scoped Active Debt Payment and Receipt Foundation**. This
document turns the M14.01–07 read-only audit into the implementation contract.
It is intentionally narrower than a general repayments, collections, or
payments subsystem.

## Authority and exact baseline

Authority is ordered as follows: `docs/tt_nasiya_web_v1.md`; the frozen scope
file `/home/yalgashev/projects/nasiya_m14_00_final_scope_freeze.md`; the
product gate `/home/yalgashev/projects/nasiya_m14_product_gate.md`; these
tracked M14 authority documents; M13 closeout/contracts; earlier milestones;
then repository evidence. TT defines M14 behaviour; the freeze controls staged
scope and OUT boundaries. Nothing here changes freeze semantics.

The implementation baseline is repository `nasiya`, branch `main`, at M13
documentation head `b12a8b23335a5aad6290b8ca96007decd59cb4d1`, with Alembic
head `f4a5b6c7d8e`. The M13 implementation checkpoint is
`c6b5eb0aed9fcf0b87dd1aabbc5816957e25b840`; its CI job
`31261604184/93113180042` and its closeout job `31261849730/93113793150` each
reported 3,643 passed and zero non-passes. The frozen TT blob is
`d77c0f0f330a1330155a4aee3c46b05d97cf5561` (SHA-256
`569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab`). M14
starts from that green, clean, origin-synchronised baseline; it does not claim
that M14 product code already exists.

## Capability and lifecycle boundary

A current, active-shop OWNER, MANAGER, or CASHIER may record one idempotent,
whole-UZS partial or full payment against a current-tenant **active** Debt on
or before its Tashkent due date. Ledger sums derive remaining balance and
exposure. An exact remaining payment changes `active` to `paid`; the payment
history and receipt remain immutable. Tenant staff can read tenant receipts;
the linked Customer can read only their own history and receipts.

M14 persists exactly these Debt statuses: `pending`, `active`, `rejected`,
`cancelled`, `expired`, and `paid`. It adds only:

| Before | Operation | After |
| --- | --- | --- |
| `active` | partial payment | `active`, revision +1 |
| `active` | exact remaining payment | `paid`, revision +1 |

Python may retain vocabulary for future `overdue`, `written_off`, and
`written_off_settled`, but M14 schema, service, and routes must not persist or
write those states. In particular, an otherwise active Debt whose Tashkent
due date is past is fail-closed as `DEBT_NOT_PAYABLE`; M14 must not silently
model it as overdue or accept a late payment.

## Exact IN and OUT scope

IN is one bounded `app/payment/` feature: contracts/values/enums/model,
repository, targeting, policy, command/service/read/presentation/router; one
`payments` table; the Debt `paid_at` field and six-status constraint; a narrow
payment idempotency endpoint/result pair; one Alembic revision after `f4`; SSR
tenant/customer reads; whole-UZS validation; ledger-derived balances/exposure;
immutable audit; and real PostgreSQL concurrency evidence.

The following are explicitly OUT, and no field, route, dependency, migration,
test helper, or placeholder may pre-build them: overdue/clawback/late-payment
scheduler; void/refund/edit/delete/correction/import; written-off settlement
or hard blocks; rating events, farming, backfill, and disclosure; notifications
or outbox; scheduler/job runs; reports/exports; instalments, fees, interest, or
multi-currency; gateway/bank/webhook/PAN/reference support; PDF/fiscal/serial
receipts or object storage; cached paid/balance/remaining/exposure columns;
customer self-pay/public API; platform-admin payment console; mutation of
Customer, identity, Telegram, offer, or PII; generic broker/cache/distributed,
advisory, or direct dependency locks.

There is no M14 admin/API/JSON/void route. Browser code performs no money math,
time/actor/tenant authority decision, local/session storage of financial data,
financial service-worker cache, or inline JavaScript.

## Authority, money, time, and formulas

The live active Shop session, not a form locator, establishes tenant authority.
OWNER/MANAGER/CASHIER can perform permitted tenant reads and on-time creation;
a suspended Shop permits historical reads only. Inactive/revoked staff are
never authority, and a platform admin is not a substitute. A Customer's
server-resolved own Customer relation permits only their own history/receipt,
including while the Shop is suspended; absent/foreign payment reads return
generic `PAYMENT_UNAVAILABLE`.

Repayment is not gated by the target Customer's active state, Telegram,
blacklist/whitelist, credit policy, or rating; those belong to debt origination.
Mutation gates are live actor/shop/tenant/debt/payability/revision/amount and
idempotency. `expected_debt_revision` is only a stale-write check.

Amounts are `Decimal` backed by `NUMERIC(18,0)`, accepted only as strict ASCII
base-10 input from `1` through `1_000_000_000_000`. No signs, separators,
embedded whitespace, fraction, exponent, bool, or float are valid. The service
also rejects an amount above locked current remaining with
`PAYMENT_AMOUNT_EXCEEDS_BALANCE`. Methods are exactly `cash`, `card`,
`transfer`, and `other`.

`created_at`, `paid_at`, and `updated_at` are aware UTC server timestamps. Once
the Debt is locked the server captures time exactly once before payability or
balance decisions; `tashkent_business_date(created_at) <= due_date` is
inclusive. On a full payment, `Payment.created_at == Debt.paid_at ==
Debt.updated_at`.

For a Debt, `posted_total = sum(payment.amount_uzs)` and
`remaining_due = max(discounted_amount_uzs - posted_total, 0)`. A receipt for
payment revision `r` uses only payments with `debt_revision_after <= r`:
`historical_balance_after = max(discounted_amount_uzs - posted_through, 0)`.
Current views use all payments. Open exposure is original amount for `pending`,
`max(original_amount_uzs - posted_total, 0)` for `active`, and zero for `paid`;
open-count includes only `pending` and `active`. Original exposure and
discounted repayment balance are deliberately different. No value is cached.

## Persistence, idempotency, audit, and migration

`payments` has UUID `id`; non-null restricted FKs `debt_id` and
`recorded_by_user_id`; `amount_uzs NUMERIC(18,0)`; method; positive
`debt_revision_after`; and `created_at`. It has exactly these named invariants:
`ck_payments_amount_uzs_bounds`, `ck_payments_method_allowed`,
`ck_payments_debt_revision_after_positive`,
`uq_payments_debt_id_debt_revision_after`, `fk_payments_debt_id_debts_id`, and
`fk_payments_recorded_by_user_id_users_id`. It has no update/status/void/note/
reference/balance/remaining/PII fields and its model representation is redacted.

Debt gains `paid_at`. `paid` requires `accepted_at` and `paid_at`, has no
terminal/reason fields, and every non-paid row has null `paid_at`; timestamp
ordering is `paid_at >= accepted_at >= created_at` and `updated_at >= paid_at`.
The payment idempotency pairs are exactly `shop.debts.create -> debt` and
`shop.debt_payments.create -> payment`; the existing `(actor_user_id, endpoint,
key_digest)` uniqueness remains. The completed result becomes safely typed by
`result_object_id`, preserving existing debt behaviour and rejecting a result
type mismatch.

The payment hash is domain-prefixed and length-safe over actor, current Shop,
Debt, amount, method, and expected revision—not displayed balance, customer
locator, raw key, time, or result. A form UUID and matching header (if both are
present) provide the key. Same actor/endpoint/key/hash replays the old Payment
before status validation with zero writes; the same key with a different hash
is `IDEMPOTENCY_CONFLICT`; a different key with stale revision is
`DEBT_CHANGED` with zero writes. Validation failures do not consume a key.
Unexpected `IntegrityError` is never represented as a replay.

Every payment appends `payment.recorded` for a `payment` and, only when fully
settled, `debt.paid` for a `debt`, each with USER actor and safe payload. The
former records amount, method, status transition, and revision-after; the latter
records `source=payment` and revision-after. No arbitrary event bus is added.

One migration after `f4a5b6c7d8e` creates the table and Debt constraints. Its
downgrade must refuse if any payment, payment idempotency key, paid/paid_at
Debt, or M14 audit row exists; only an empty M14 state may revert exactly to
M13. Test cleanup deletes payments before debts and users.

## Transaction, locking, routes, and errors

The POST uses two transactions: TX-A authenticates/touches session, resolves
current Shop, and validates CSRF, then commits/closes; only detached scalars
cross into TX-B. TX-B performs target/replay resolution, forward locks, key,
Debt, Payment, Debt update, audit, and a route-coordinated commit/rollback.
Repositories/services borrow a supplied session and never commit, full rollback,
or close it. There is no external I/O, ORM object handoff, retry/sleep,
`NOWAIT`, `SKIP LOCKED`, timeout, or advisory lock.

The global order is `Shop -> ShopStaff -> TelegramLinkToken -> OtpDispatch ->
OtpChallenge -> User -> TelegramLink -> Customer -> OfferVersion ->
OfferAcceptance -> CustomerIdentity -> ObjectFile -> CustomerDocument ->
ShopCustomer -> IdempotencyKey -> Debt -> Payment -> AuthSession`; same-class
rows are ascending UUID and unused classes are skipped. Creation specifically
locks Shop, staff, actor User, target Customer (without target status gating),
ShopCustomer, new IdempotencyKey, Debt, new Payment, then AuditLog. The
completed-key discovery is non-locking; a new key is inserted after ShopCustomer
and before Debt; only the expected idempotency unique conflict uses a named
savepoint. The locked Debt serialises every lawful append, so no existing
Payment lock is needed after it; FK actor/debt rows are already held. Both
payment and new-Debt exposure decisions lock ShopCustomer before their open set,
so observers see either old or new complete state.

The only payment routes are SSR:

| Method | Route |
| --- | --- |
| GET | `/shop/debts/{debt_id}/payments` |
| GET | `/shop/debts/{debt_id}/payments/new` |
| POST | `/shop/debts/{debt_id}/payments` |
| GET | `/shop/payments/{payment_id}` |
| GET | `/customer/debts/{debt_id}/payments` |
| GET | `/customer/payments/{payment_id}` |

POST is CSRF-protected PRG and a success/replay redirects to the shop receipt.
Payment reads take no locks. New exact error codes are `PAYMENT_UNAVAILABLE`,
`PAYMENT_AMOUNT_EXCEEDS_BALANCE`, and `DEBT_CHANGED`; reuse
`DEBT_UNAVAILABLE`, `DEBT_NOT_PAYABLE`, `IDEMPOTENCY_CONFLICT`, `UNAUTHORIZED`,
`FORBIDDEN`, `SHOP_SUSPENDED`, `VALIDATION_ERROR`, and `CSRF_FAILED`, localized
in UZ/RU. `PAYMENT_NOT_VOIDABLE` is not emitted in M14.

## Required evidence and non-regression

Tests must demonstrate real-PostgreSQL same-key replay/conflict, partial/full
races, locked-ledger arithmetic, payment-vs-new-Debt exposure serialization,
time boundaries, rollback atomicity, access matrices and IDOR denial, receipt
history/current distinction, migration downgrade guards, privacy/redaction, and
OUT-scope static scans. Denials leave no payment, Debt, audit, or key write,
apart from a valid pre-existing completed replay which makes no new write.
M1–M13 tests and their source-scoped containment guards remain in force.
