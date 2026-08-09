# M14 Final Technical Report (Local Draft)

Status: `M14 LOCAL GREEN — EIGHTH CHECKPOINT PENDING`

Date: 2026-08-09

This is a truthful local technical draft. It records the implemented M14
foundation, controlled manual Chrome/PostgreSQL evidence, and repeated local
validation. It does **not** claim remote CI, a push, or final remote closure.

## Baseline and authority

- M14 began from M13 docs-only closeout `b12a8b23335a5aad6290b8ca96007decd59cb4d1`.
- Pre-M14 migration head was `f4a5b6c7d8e`; current M14 head is
  `a5b6c7d8e9f0`.
- Scope authority remains
  [m14_scope_contract.md](m14_scope_contract.md),
  [m14_decisions.md](m14_decisions.md), and
  [m14_repository_map.md](m14_repository_map.md). This report does not amend
  their freeze semantics.

## Delivered capability

M14 adds tenant-scoped, append-only repayment for an active Debt that is still
on or before its Asia/Tashkent due date. OWNER, MANAGER, and CASHIER can record
strict whole-UZS payments using `cash`, `card`, `transfer`, or `other`.
Partial payment keeps the Debt active; exact locked remaining changes it to
paid. A linked own Customer has read-only history and receipt access.

The exact SSR routes are:

1. `GET /shop/debts/{debt_id}/payments`
2. `GET /shop/debts/{debt_id}/payments/new`
3. `POST /shop/debts/{debt_id}/payments`
4. `GET /shop/payments/{payment_id}`
5. `GET /customer/debts/{debt_id}/payments`
6. `GET /customer/payments/{payment_id}`

There is no M14 admin, API/JSON, customer self-pay, edit, void, refund, or
delete route.

## Persistence, formulae, and atomicity

- `payments` is an immutable ledger table with UUID identity, RESTRICT Debt/User
  FKs, `NUMERIC(18,0)` amount, one exact method enum, positive
  `debt_revision_after`, unique `(debt_id, debt_revision_after)`, and aware UTC
  `created_at`.
- Debt has exactly six persisted M14 statuses and nullable `paid_at`; only
  `active -> paid` is reachable from payment.
- `posted_total = sum(payment.amount_uzs)`.
- `remaining_due = max(discounted_amount_uzs - posted_total, 0)`.
- Pending exposure is original amount; active exposure is
  `max(original_amount_uzs - posted_total, 0)`; paid exposure is zero. Open
  count includes pending and active only.
- Receipt balance through payment revision `r` is
  `max(discounted_amount_uzs - sum(payments through r), 0)`; current balance
  uses all payments. Neither balance nor exposure is cached.
- Mutation holds the frozen forward order, inserts a new idempotency key before
  Debt lock, locks/re-sums the ledger, then writes Payment, Debt,
  `payment.recorded`, and conditional `debt.paid` in one caller-owned
  PostgreSQL transaction. Replay is exact-once and emits no second mutation or
  audit.

## Security and privacy result

Tenant and own-Customer joins are authoritative; foreign/absent locators are
generic unavailable outcomes. Live staff/shop state is rechecked for mutation
and completed replay. Suspended shops retain historical reads but not payment
creation. POST uses CSRF and PRG. Authenticated payment documents are
`no-store`, CSP remains external-script-only, and safe projections exclude raw
idempotency keys, request hashes, UUIDs, staff identity, Customer PII, bank
detail, and fiscal/PDF data.

The hardening suite includes real PostgreSQL total-order barriers for
payment/payment, payment/new-Debt, payment/pending accept/cancel/expire,
same-key waiting, audit append, one-winner full payoff, money/time boundaries,
rollback faults, IDOR, and OUT containment. It contains no retry, sleep,
NOWAIT, SKIP LOCKED, timeout, advisory-lock, cache, gateway, scheduler,
rating, or notification workaround.

## Controlled manual acceptance (M14.67–68)

Google Chrome 150 headless was driven through the Chrome DevTools Protocol
against local controlled PostgreSQL. All accounts, shops, debts, session
fixtures, and labels were synthetic. The safe evidence is intentionally only
status/count/checklist material:

- At 320px, OWNER completed a cash partial payment, refresh/replay converged to
  the same receipt with one browser POST, and the first receipt later retained
  its historical balance while current balance/status changed after full payoff.
- At 430px and RU, MANAGER completed card partial payment. CASHIER completed
  transfer partial payment. OWNER completed `other` exact-full payment; a new
  paid-Debt key was denied.
- Browser stale submission was denied; overpayment was denied; all four frozen
  methods were rendered and exercised.
- Suspended Shop history remained read-only with no create control. Revoked and
  wrong-tenant shop access were denied.
- Own Customer no-payment, partial, paid, suspended-Shop history, and receipt
  flows rendered read-only in UZ/RU at 430px. Foreign and guessed payment
  locators converged to generic unavailable.
- DevTools observed one POST per submitted browser action, authenticated
  document `no-store`, no local/session/Cache Storage financial artifact, and
  no console exception/error. Receipts did not render idempotency or staff
  detail.

Temporary browser profile, screenshots, local session cookies, and controlled
fixture data were removed after evidence capture. No raw token, key, hash,
UUID, phone, PII, or screenshot is retained in this report.

## Automated local validation

| Check | Result |
| --- | --- |
| Frozen dependency sync | GREEN; 48 packages |
| Alembic current / single head | GREEN; `a5b6c7d8e9f0` / `a5b6c7d8e9f0` |
| Ruff check / format check | GREEN / GREEN; 590 files formatted |
| Static/containment and real PostgreSQL guards | GREEN in the repeated full suite |
| Full pytest | 3,919 passed in 305.79s; zero failed/skipped/xfailed/xpassed/warnings |

The seventh checkpoint before this docs draft was `11d4575` and passed 3,919
tests in 348.65 seconds. The table records the clean post-draft repeated M14.69
run, with zero failed, skipped, xfailed, xpassed, or warning outcomes.

## Checkpoints

1. `c5c19f1` — `M14: freeze active payment scope`
2. `ca479ac` — `M14: add payment and balance contracts`
3. `309c59e` — `M14: add payment persistence`
4. `127c3b3` — `M14: add idempotent active debt payments`
5. `577bdb9` — `M14: add payment balances and receipts`
6. `f044eb3` — `M14: expose payment web flows`
7. `11d4575` — `M14: harden payment security and concurrency`
8. Pending — final M14 implementation/closure checkpoint has not been created.

## Accepted staged boundary

M14 intentionally leaves historical rating/notification gaps untouched and
does not convert past-due active Debt into an overdue lifecycle. A past-due
active Debt is nonpayable. Void/refund/correction, written-off settlement,
reports, exports, scheduler/worker, gateway/bank integration, multi-currency,
fees, instalments, public/self-payment, and cached financial projections remain
OUT. See [m14_known_limitations.md](m14_known_limitations.md).

Remote evidence is pending. This document must not be read as remote GREEN.
