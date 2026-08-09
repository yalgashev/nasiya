# Nasiya M14 Known Limitations

Status: authoritative accepted M14 limitations; `M14 REMOTE GREEN — CLOSED`.
Controlled M14.67–68 Chrome/PostgreSQL acceptance passed, and the exact eighth
implementation checkpoint is remotely GREEN.

M14 deliberately provides a narrow active-Debt payment ledger foundation. These
are known staged boundaries, not defects that an implementation may fill in
without a new authorised milestone.

## Controlled local manual evidence

Synthetic Google Chrome 150 acceptance exercised the active OWNER/MANAGER/
CASHIER payment flows at 320–430px, all four methods, partial/full/replay,
stale/overpayment/paid-new-key denial, tenant suspension/revocation/foreign
denial, own Customer history/receipt, UZ/RU, and suspended-Shop historical
read. DevTools observed no duplicate POST, authenticated `no-store`, no
financial browser storage/cache artifact, and no console error. Only safe
checklist facts were retained; temporary browser/session/fixture material was
removed. This is local evidence only and does not claim remote GREEN.

The repeated clean M14.69 PostgreSQL suite passed 3,919 tests in 305.79 seconds
with zero failed, skipped, xfailed, xpassed, or warning outcomes. This confirms
the local boundary. Exact implementation SHA
`2293b97459218b61ab796863b4c0a0522edbdb6a` then passed remote run
[31295227718](https://github.com/yalgashev/nasiya/actions/runs/31295227718), job
[93199127585](https://github.com/yalgashev/nasiya/actions/runs/31295227718/job/93199127585):
3,919 passed in 184.32 seconds with zero failed, skipped, xfailed, or xpassed
outcomes, at Alembic head `a5b6c7d8e9f0`.

## KL-M14-01 — Paid is not the full debt lifecycle

M14 adds only active partial/full repayment and `active -> paid`. It does not
implement overdue, late-payment acceptance, clawback, written-off,
written-off-settled, settlement, or a scheduler. A debt that remains `active`
after its Tashkent due date is deliberately rejected as `DEBT_NOT_PAYABLE`.
This past-due-active limitation prevents silent lifecycle invention and must be
visible in product/support expectations.

## KL-M14-02 — No reversal or correction path

Payments are immutable. M14 has no void, refund, edit, delete, correction,
import, reversal reason, or speculative corresponding schema field. A mistaken
payment requires a future explicitly authorised accounting workflow; it cannot
be repaired by mutating M14 data.

## KL-M14-03 — Rating and notification are historical gaps

M13 has no rating producer/disclosure and M14 does not add one. M14 payment or
paid events must not farm, backfill, or display rating data. Likewise there is
no notification, outbox, Telegram dispatch, email/SMS, or event bus. The two
audit events are immutable internal evidence only, not delivery triggers.

## KL-M14-04 — Payment methods are labels, not integrations

`cash`, `card`, `transfer`, and `other` are staff-recorded method labels.
There is no gateway, bank reconciliation, webhook, PAN, payment reference,
provider status, fiscal document, PDF, receipt sequence, or object storage.
The SSR receipt is an in-application historical view, not proof of settlement
outside this system.

## KL-M14-05 — Reads are intentionally narrow

Only active tenant staff can use tenant payment reads (a suspended Shop keeps
historical read access), and only a server-resolved own Customer can use customer
history/receipt. There is no customer self-pay, public API, admin payment
console, cross-tenant support lookup, report, export, or broad search. Foreign
and absent payment reads intentionally collapse to `PAYMENT_UNAVAILABLE`.

## KL-M14-06 — No cached financial state

Current and historical balances/exposure come from immutable Payment ledger
aggregation. There are no stored `remaining`, `balance`, `exposure`, or cached
paid columns. This favours correctness and historical clarity over a reporting
optimisation; reports/exports and performance-oriented projections are future
scope requiring their own correctness and privacy contract.

## KL-M14-07 — Single-currency and whole-UZS only

M14 accepts only positive strict-integer UZS within the declared bound. There
is no fraction, float, decimal currency scale, interest, fee, instalment plan,
exchange rate, multi-currency, rounding policy beyond the whole-UZS contract,
or automatic amount suggestion trusted by the server.

## KL-M14-08 — Concurrency proof is PostgreSQL-specific

The contract relies on PostgreSQL row locking, the named idempotency unique
savepoint, and real PostgreSQL race tests. It does not promise equivalent
behaviour on SQLite or another database. It also does not introduce a generic
broker/cache/distributed/advisory lock or retry framework.

## KL-M14-09 — Browser is a presentation boundary

M14 SSR pages use server calculations and authority. They do not provide offline
financial operation, service-worker financial caching, local/session storage of
financial state, inline JavaScript, or client-side balance/time/tenant decisions.
Network interruption and duplicate submission are handled by server
idempotency, not browser persistence.

## KL-M14-10 — Customer and identity data remain untouched

Repayment does not mutate Customer status, identity/documents, Telegram links,
offers/acceptance, PII, blacklist/whitelist, or credit policy. The target
Customer status is deliberately not a payment eligibility gate. These systems
remain origination or identity concerns.

## Explicit OUT scope is a safety boundary

The exclusions above do not represent implied implementation plans. Adding an
OUT route, schema field, background job, side effect, dependency, or UX affordance
would change financial semantics and requires a future approved scope contract.
M14 docs authorize only the narrow active-Debt, immutable-ledger capability in
[`m14_scope_contract.md`](m14_scope_contract.md).
