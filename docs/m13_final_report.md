# M13 Final Technical Report — Pre-Remote Draft

Status: `LOCAL GREEN — EIGHTH CHECKPOINT AND REMOTE EVIDENCE PENDING`
Date: 2026-08-08

This draft records the implemented M13 tree through its immutable seventh
checkpoint, real PostgreSQL and Chrome acceptance, and local validation. It does
not claim an eighth checkpoint, push, GitHub Actions run, or remote green.

## Baseline And Authority

- M13 parent: M12 docs-only closeout
  `7eb138571b1e990b87b6810a05524ad32986bbab`.
- M12 remote-tested tree:
  `d9c80a272a1df86c5aefa1a7e0ff81e68e65c13d`.
- Pre-M13 Alembic head: `e3f4a5b6c7d8`.
- Current M13 Alembic head/current: `f4a5b6c7d8e` / `f4a5b6c7d8e`.
- Authority order and protected-document policy remain those frozen in
  `docs/m13_scope_contract.md`; TT, Product Gate, Final Scope Freeze, and the
  micro-task guide were not edited.

## Delivered Capability

M13 adds one bounded pending-debt foundation. An active OWNER, MANAGER, or
CASHIER in the session-derived active shop may propose an idempotent pending
Debt for an existing tenant ShopCustomer after current-offer, customer,
Telegram, local policy, hard-block read seam, exposure, count, amount, and time
checks. The own customer may accept or reject; active same-shop staff may cancel
with a reason; exact 72-hour expiry wins at its boundary.

The only runtime transitions are:

```text
-- -> pending
pending -> active | rejected | cancelled | expired
```

## Persistence, Transactions, And Security

| Item | Local result |
|---|---|
| Migration parent / head | `e3f4a5b6c7d8` / `f4a5b6c7d8e` |
| New tables | `debts`, `idempotency_keys` |
| Existing-table extension | nullable debt association and debt-scoped uniqueness on `offer_acceptances` |
| Money/time | whole-UZS `NUMERIC(18,0)`, basis points, server math, Asia/Tashkent business date, exact 72 hours |
| State | `pending`, `active`, `rejected`, `cancelled`, `expired` only |
| Transaction ownership | route/coordinator owns commit/rollback; borrowed services do neither |
| Concurrency | deterministic PostgreSQL row-lock order and barriers; no retry/sleep/NOWAIT/SKIP LOCKED/advisory lock |
| Authority | current-shop live membership or own Customer chain; opaque locators are not authority |
| Privacy | raw reason/key/hash/UA/offer body/phone/PII/session/CSRF excluded from safe output and audits |

## Exact Web Surface

1. `GET /shop/customers/{shop_customer_id}/debts`
2. `GET /shop/customers/{shop_customer_id}/debts/new`
3. `POST /shop/customers/{shop_customer_id}/debts`
4. `GET /shop/debts/{debt_id}`
5. `POST /shop/debts/{debt_id}/cancel`
6. `GET /customer/debts`
7. `GET /customer/debts/{debt_id}`
8. `POST /customer/debts/{debt_id}/accept`
9. `POST /customer/debts/{debt_id}/reject`

All POST routes are CSRF-protected PRG flows. Authenticated debt pages are
no-store, UZ-Latn/RU copy is feature-local, and rendering remains autoescaped.
There is no admin, JSON/API, public, or expiry-trigger route.

## Exact Audit Surface

| Event | Actor | Safe meaning |
|---|---|---|
| `debt.created` | USER | bounded financial/date projection |
| `debt.accepted` | USER | legal version/language/hash projection |
| `debt.rejected` | USER | reason-present boolean only |
| `debt.cancelled` | USER | reason-present boolean only |
| `debt.expired` | SYSTEM | inline/batch source only |

Replay/no-op emits no duplicate audit. Audit faults roll back the enclosing
Debt, idempotency-key, decision, or acceptance mutation.

## Local Automated Validation

| Check | Result |
|---|---|
| Frozen dependency sync | GREEN |
| Ruff format/check and diff-check | GREEN |
| Focused corrected regression matrix | 53 passed |
| Full real-PostgreSQL suite at M13.65/M13.66 | 3643 passed; zero failed/skipped/xfailed/xpassed |
| Alembic current / single head | `f4a5b6c7d8e` / `f4a5b6c7d8e` |
| M13.69 repeated full suite after docs draft | 3643 passed in 331.65s; zero failed/skipped/xfailed/xpassed |

The two reported warnings are upstream deprecations and are not nonpass test
outcomes.

## Controlled Manual Acceptance

The image labeled with seventh-checkpoint revision `afef316` was rebuilt from
the clean checkpoint and run against real PostgreSQL. Google Chrome 150 used
synthetic data; evidence retained only safe statuses and counts.

- Shop: OWNER/MANAGER/CASHIER create passed; identical replay returned the same
  result with one Debt/key/audit; changed payload conflicted; inclusive limit,
  strict max-count, blacklist, and whitelist behavior passed.
- Authority: foreign-tenant locators were unavailable, suspended mutations were
  denied, revoked membership lost shop context, and tenant list/detail output
  contained neither raw phone nor key/session material.
- Customer: own list/detail and current legal text passed; accept and replay
  produced one acceptance/audit; optional reject and required cancel reasons
  behaved correctly; foreign-customer locators were unavailable.
- Races/boundaries: stale displayed offer was denied after a current-offer
  switch and succeeded after refresh; suspended-shop accept was denied while
  reject remained allowed; expiry was reachable at the exact boundary; terminal
  accept/reject produced one winner.
- Final safe counts were 3 active, 2 rejected, 1 cancelled, 1 expired, and 6
  pending synthetic Debts, with 3 debt acceptances and exact terminal audits.
  Payment/rating/notification/scheduler tables or mutations were absent.

Session-bearing temporary fixtures and the Chrome profile were deleted after
acceptance; raw UUIDs, phones, keys, reasons, offer bodies, credentials, and
sessions are not recorded here.

## Checkpoints

1. `f80ca1438c1c14014ade6ce9f6096cf38fe59135` — `M13: freeze pending debt scope`
2. `4b89ac2007f71edd2eddf6d041df4da1cb71f73a` — `M13: add debt and idempotency contracts`
3. `24c4c9523c5d24932d038ed6ac706f2895874d21` — `M13: add pending debt persistence`
4. `dfdac5b190180b603aede5bfd93c35ad255e0bc3` — `M13: add tenant debt proposals`
5. `b3996e81d7ba821438c58dfe61e4241b7e0ad7af` — `M13: add customer debt decisions`
6. `84aa3640b123d8472e42dbab32ad37f34b12d07d` — `M13: expose pending debt web flows`
7. `afef31649251ee4f6abf257e51164a9c71c78701` — `M13: harden debt security and concurrency`
8. PENDING — `M13: complete pending debt foundation`

The first seven subjects have intact linear ancestry. The eighth checkpoint is
deliberately not created by M13.69, and nothing has been pushed.

## Accepted OUT Boundary

M13 does not add payment or balance behavior, ratings or hard-block mutation,
notifications/outbox, scheduler/worker/cron, reports/exports, installments,
bank integration, public onboarding, PII decrypt/storage, debt edit/delete/
transfer/reopen, platform-admin debt operations, generic infrastructure, or a
new direct runtime dependency. Active Debt remains full open exposure until a
separately authorized payment milestone.
