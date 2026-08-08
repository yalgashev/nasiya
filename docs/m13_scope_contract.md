# Nasiya M13 Scope Contract

Status: authoritative M13 repository scope; implementation has not started.
Capability: **Tenant-Scoped Debt Proposal & Legal Acceptance Foundation**.
Product Owner disposition: `PO-M13-01..25 — 25/25 FINAL APPROVED`.

This document, `docs/m13_decisions.md`, and
`docs/m13_repository_map.md` are the executable authority for M13 tasks after
M13.08. `docs/m13_known_limitations.md` records accepted boundaries and does
not authorize additional capability. A required deviation is a stop condition,
not permission to infer a product change.

## Authority and exact baseline

Conflicts are resolved in this order:

1. `docs/tt_nasiya_web_v1.md`;
2. `/home/yalgashev/projects/nasiya_m13_00_final_scope_freeze.md`;
3. `/home/yalgashev/projects/nasiya_m13_product_gate.md`;
4. this contract, `docs/m13_decisions.md`, and
   `docs/m13_repository_map.md`;
5. M12 closeout and tracked M12 contracts;
6. inherited M2–M11 contracts;
7. repository implementation, migration, and tests as integration evidence.

| Evidence | Exact value |
|---|---|
| M12 eighth checkpoint | `4a36e96c887c5bda51317a80a13d5aeda9384278` |
| M12 remote-tested tree | `d9c80a272a1df86c5aefa1a7e0ff81e68e65c13d` |
| M12 implementation CI | run `31238158808`, job `93054450292`, `3472 passed`, zero nonpass |
| M12 docs-only closeout / M13 parent | `7eb138571b1e990b87b6810a05524ad32986bbab` (`docs: close M12 remote evidence`) |
| M12 checkpoints | `8/8`, intact linear ancestry |
| Alembic current/head before M13 | `e3f4a5b6c7d8` |
| TT tracked blob | `d77c0f0f330a1330155a4aee3c46b05d97cf5561` |
| M13.01 verification | clean/synced `main`, divergence `0 0`, one head, no M13 product code |
| M13.02–07 audits | source inventory, lock, policy/exposure, M9, idempotency/time, and threat matrix: PASS |

Protected TT, Product Gate, Final Scope Freeze, and micro-task guide are not
edited by M13 tasks unless their task explicitly authorizes it. M12 history is
never amended, rebased, squashed, or relabelled.

## One capability and state boundary

An authenticated, auth-active, live active ShopStaff in the session-derived
current active Shop may create one idempotent pending Debt for an existing
tenant-owned ShopCustomer. The server rechecks target User, self-phone-verified
TelegramLink, active Customer, current `DEBT_ACCEPTANCE` offer, local policy,
global hard-block projection, exposure, count, money, time, and idempotency.

The own active Customer may accept or reject its own pending Debt. Active
same-shop staff may cancel an unexpired pending Debt with a reason. At `now >=
pending_expires_at`, expiry wins and is recorded by SYSTEM.

Only these M13 persistence transitions exist:

```text
— -> pending
pending -> active | rejected | cancelled | expired
```

Python vocabulary may recognize future `paid`, `overdue`, `written_off`, and
`written_off_settled`, but M13 database, service, and router code may neither
persist nor transition to them.

## Exact IN and OUT scope

M13 owns only the bounded `app/debt/` and `app/idempotency/` domains, one
`debts` table, one `idempotency_keys` table, the debt association on existing
`offer_acceptances`, one Alembic child of `e3f4a5b6c7d8`, tenant/own debt reads
and mutations, whole-UZS/basis-point math, Tashkent business dates, exact
72-hour pending expiry, M12 policy/exposure/count consumption, a hard-block
read port, legal evidence extension, five audit events, nine server-rendered
web routes, and real PostgreSQL/browser evidence.

M13 must not create payments, payment methods, receipts, voids, balance or
cached exposure, paid/overdue/write-off runtime, rating or hard-block mutation,
notifications, outbox, scheduler process/job/cron/worker, reports, exports,
installments, bank integrations, public onboarding, PII decrypt/storage, debt
edit/delete/transfer/reopen, platform-admin debt actions, generic event/cache/
broker/distributed-lock framework, or a new direct runtime dependency.

Active Debt remains full open exposure until a separately authorized payment
milestone. This is staged delivery, not a completed lending ledger.

## Authority, roles, and live eligibility

Client `shop_customer_id`, `debt_id`, User, Customer, Telegram, status,
exposure, discounted amount, expiry, and legal snapshot values are locators or
displayed stale-form material, never authority. Current Shop comes from the
session and live ShopStaff membership; customer ownership comes from the
session User -> Customer chain.

| Operation | active OWNER | active MANAGER | active CASHIER | suspended Shop | platform-admin without membership |
|---|---:|---:|---:|---:|---:|
| tenant debt list/detail | allow | allow | allow | allow | deny |
| create pending debt | allow | allow | allow | deny | deny |
| cancel pending debt | allow | allow | allow | deny | deny |
| own-customer list/detail | n/a | n/a | n/a | own only | n/a |
| own accept | n/a | n/a | n/a | deny | n/a |
| own reject | n/a | n/a | n/a | allow | n/a |

Create gates, all under the domain transaction, are: active current Shop,
auth-active actor and target User, live active membership, tenant-scoped
ShopCustomer, current active self-phone-verified TelegramLink, active Customer,
complete current `DEBT_ACCEPTANCE` offer, non-blacklisted local policy, no
hard-block, inclusive credit limit, strict max open-count, valid money/time,
and an unused or replayable key. Whitelist bypasses none of these gates.

Customer accept rechecks own active Customer, active Shop, verified Telegram,
non-blacklisted/non-blocked policy, pending/unexpired Debt, business-date due
constraint, and exact current displayed offer. Other-customer or wrong-tenant
debt locators converge to `DEBT_UNAVAILABLE` without an existence leak.

## Value and persistence contract

Money is `Decimal` in domain and `NUMERIC(18,0)` in PostgreSQL. Original UZS is
an ASCII whole input in `1..1_000_000_000_000`; float, sign, separator,
fraction, and scientific notation are rejected. Discount input is an ASCII
percentage `0..100.00` with at most two fractional digits, persisted as
`SMALLINT` basis points `0..10000`.

```text
discounted_amount_uzs = ROUND_HALF_UP(
  original_amount_uzs * (10000 - discount_basis_points) / 10000
)
```

Discounted amount must be `1..original_amount`; a zero result is
`VALIDATION_ERROR`. Amounts and due date are immutable after create.

`due_date` is an Asia/Tashkent `DATE`, not a UTC calendar date. Timestamps are
aware UTC `timestamptz`; `pending_expires_at` is exactly `created_at + 72
hours`; `due_date >= Asia/Tashkent date(pending_expires_at)`; and expiry is
`now >= pending_expires_at`. Acceptance also requires Tashkent date(now) not
after `due_date`.

`debts` has UUID PK; RESTRICT FKs to `shop_customers` and creator `users`;
original/discount/discounted values; due/expiry/status/revision; terminal
reason and timestamp fields; and created/updated timestamps. Named checks
enforce frozen numeric, five-status, terminal metadata, reason, revision, TTL,
and timestamp invariants. Its indexes are:

```text
(shop_customer_id, created_at DESC, id)
(shop_customer_id, status, due_date, id)
(status, pending_expires_at, id)
```

It has no payment, balance, remaining amount, PII, offer body, raw key, rating,
or notification field.

`idempotency_keys` has UUID PK; RESTRICT actor User FK; endpoint
`shop.debts.create`; lowercase SHA-256 `key_digest` and `request_hash`; fixed
result type `debt`; pre-generated Debt result UUID; timestamp; and unique
`(actor_user_id, endpoint, key_digest)`. Raw keys are accepted only at the
boundary and are never persisted, logged, audited, rendered, or represented.

Existing `offer_acceptances` receives nullable RESTRICT `debt_id`. Registration
rows require `debt_id IS NULL` and retain registration-only partial uniqueness.
Debt acceptance requires `debt_id IS NOT NULL` and has a debt-only partial
unique index. Existing registration rows are unchanged; no second legal table
is permitted.

One migration follows `e3f4a5b6c7d8`. Downgrade fails closed when any Debt,
idempotency key, debt-scoped acceptance, or M13 audit exists; empty M13 state
restores the exact M12/M9 shape.

## Exposure, idempotency, and decisions

M13 open exposure is the sum of original amounts for `pending` and `active`
Debts; no payment rows exist. It is calculated once in a narrow repository
adapter after the ShopCustomer parent is locked. A later payment milestone must
replace that adapter with `max(original - total_non_voided_payments, 0)` logic,
not add cached Debt exposure.

All creates for one ShopCustomer lock that parent before open-set reads. The
target Customer lock is retained as the future cross-shop hard-block
serialization point. Debt candidates lock and revalidate in UUID ascending
order.

A form receives a server UUID key; fetch/HTMX may use `Idempotency-Key`. If
both are present they must be exact equals after canonical boundary validation.
The request hash is a domain-prefixed, length-safe encoding of internal actor,
current Shop, ShopCustomer, original amount, basis points, and due date. A
completed same request replays with zero domain/audit writes; a different
request is `IDEMPOTENCY_CONFLICT`; failed eligibility never burns a key.

The key row, pre-generated Debt, and `debt.created` audit share one outer
transaction. Expected conflict recovery is limited to the named idempotency
unique constraint in a nested savepoint; unexpected `IntegrityError` is raised.

Accept inserts one immutable debt-scoped acceptance, transitions pending to
active with revision +1 and `accepted_at`, and appends one audit. Active own
Debt with its acceptance is a zero-write replay. Reject has optional normalized
reason; cancel requires one; both transition once with revision +1 and one
audit. Expiry transitions once before any pending action at the exact boundary.

## Transaction and global lock contract

POSTs have closed phases:

```text
TX-A: authenticate, touch session, resolve relevant current Shop, validate CSRF;
      commit/close
TX-B: domain locks, live checks, idempotency/debt/acceptance/audit;
      route/coordinator owns commit/rollback
```

No ORM object crosses phases. Borrowed repositories/services never call
`commit()`, full `rollback()`, or `close()`. No external I/O overlaps TX-B.

The mandatory forward row-lock order is:

```text
Shop -> ShopStaff -> TelegramLinkToken -> OtpDispatch -> OtpChallenge -> User
-> TelegramLink -> Customer -> OfferVersion -> OfferAcceptance
-> CustomerIdentity -> ObjectFile -> CustomerDocument -> ShopCustomer
-> IdempotencyKey -> Debt -> AuthSession
```

Unused classes are skipped; each same-class multi-row set is UUID ascending.
Non-locking discovery returns only candidates and always has a locked live
recheck. Completed idempotency replay is a non-locking early exit before a
domain mutation; a new key is inserted after ShopCustomer and before Debt.

Debt acceptance has one narrow append rule. No existing OfferAcceptance row may
be locked after ShopCustomer or Debt. After current OfferVersion/current-text
locks and locked Debt revalidation, a brand-new debt-scoped OfferAcceptance may
be inserted. The locked Debt serializes the debt-only unique key; replay reads
the already-decided Debt projection rather than acquiring a late acceptance
lock. The debt resolver must lock its current text with the OfferVersion stage;
the inherited registration resolver remains registration-only.

Retry, sleep, NOWAIT, SKIP LOCKED, lock timeout, and advisory locks are not
correctness mechanisms.

## Audit, errors, routes, and evidence

Exactly these central audit events are added:

| Event | Actor | Object | Safe payload |
|---|---|---|---|
| `debt.created` | USER | debt | amounts, basis points, due date, expiry |
| `debt.accepted` | USER | debt | offer version/language/hash |
| `debt.rejected` | USER | debt | `reason_provided` only |
| `debt.cancelled` | USER | debt | `reason_provided` only |
| `debt.expired` | SYSTEM | debt | `source=inline|batch` |

SYSTEM is valid only for bootstrap and `debt.expired`. Payloads exclude IDs,
raw reason/key/request hash/UA/offer body, phone, PII, session, IP, SQL, and
provider details. Audit failure rolls back the enclosing mutation, key, and
acceptance; replay/no-op emits no duplicate audit.

M13 adds `CUSTOMER_NOT_ACTIVE`, `CUSTOMER_BLACKLISTED`,
`CUSTOMER_RATING_BLOCKED`, `CREDIT_LIMIT_EXCEEDED`, `MAX_OPEN_DEBTS`,
`DEBT_UNAVAILABLE`, `DEBT_NOT_PENDING`, `DEBT_EXPIRED`, and
`IDEMPOTENCY_CONFLICT`; it reuses inherited authorization, offer, CSRF,
validation, reason-required, and shop-suspension errors. UZ-Latn/RU messages
are feature-local and identifier-safe.

| Route | Authority |
|---|---|
| `GET /shop/customers/{shop_customer_id}/debts` | tenant active staff; suspended read allowed |
| `GET /shop/customers/{shop_customer_id}/debts/new` | tenant staff; create control only for active Shop |
| `POST /shop/customers/{shop_customer_id}/debts` | active tenant staff; idempotent create |
| `GET /shop/debts/{debt_id}` | current tenant staff; suspended read allowed |
| `POST /shop/debts/{debt_id}/cancel` | active same-shop staff |
| `GET /customer/debts` | own Customer only |
| `GET /customer/debts/{debt_id}` | own Customer only |
| `POST /customer/debts/{debt_id}/accept` | own active Customer |
| `POST /customer/debts/{debt_id}/reject` | own active Customer |

There is no admin/API/JSON/expiry-trigger route. POSTs use CSRF and PRG; debt
pages/forms are no-store. Browser financial math, inline JavaScript, and local
or session storage are forbidden.

Evidence is real PostgreSQL for migration, constraints, repository, and barrier
races; zero skip/xfail/xpass; unit/static/web/manual tests cover all frozen
threats, failure zero-mutation, privacy, M1–M12 regressions, and OUT-scope
containment. Manual evidence uses synthetic/operator-controlled data only.

M13 checkpoint subjects, in order, are:

1. `M13: freeze pending debt scope`
2. `M13: add debt and idempotency contracts`
3. `M13: add pending debt persistence`
4. `M13: add tenant debt proposals`
5. `M13: add customer debt decisions`
6. `M13: expose pending debt web flows`
7. `M13: harden debt security and concurrency`
8. `M13: complete pending debt foundation`

Only after remote GREEN may `docs: close M13 remote evidence` follow. No
product checkpoint is committed before its explicit checkpoint task; no push
occurs before M13.72.
