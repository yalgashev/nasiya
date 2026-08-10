# Nasiya M15 Final Technical Report

Status: M15 implementation is remote GREEN at the exact eighth checkpoint.
This docs-only closeout records that immutable implementation evidence and does
not claim or self-reference its own commit SHA.

## Delivered capability

M15 extends M14 with the bounded deterministic overdue lifecycle:

```text
active --(post-lock Tashkent day is after due date)--> overdue --(exact original remaining)--> paid
```

An overdue transition is exact-once, writes `overdue_at` and
`overdue_revision`, and appends `debt.overdue` plus `debt.clawback_applied` as
SYSTEM audit facts in the same transaction. An active Debt that is past due may
be projected as effective overdue on reads, but a GET never writes it. Inline
payment or the bounded service/harness batch materializes the state under the
fixed forward lock order.

On-time active payments use discounted remaining; effective/persisted overdue
payments use original remaining. Whole-UZS server arithmetic is:

```text
discounted remaining = discounted_amount - posted_total
original remaining   = original_amount - posted_total
clawback increase    = original_amount - discounted_amount
```

All values are nonnegative only after a coherent ledger check; over-credit is
rejected rather than clamped. Payment history below `overdue_revision` retains
discounted basis, history above it uses original basis, and equality fails
closed. Current balances remain derived rather than stored.

## Security and product boundary

- New proposal and own pending acceptance take a hard-block clock only after
  their Customer lock. Payment takes its clock only after Debt lock. Completed
  replay invokes neither clock.
- Any effective active-past-due or persisted overdue Debt blocks new proposal
  and acceptance across all Shops for that Customer. It exposes no cross-Shop
  name, count, or amount, and does not gate lawful repayment.
- Forms bind expected revision and balance basis into v2 idempotency. Missing
  basis may resolve only an already-completed M14 v1 replay; it cannot mutate.
- The existing six payment routes are retained: five GET and one POST. SSR,
  CSRF, PRG, no-store, autoescape, UZ/RU, and 320–430px presentation remain
  server-authoritative.
- Schema has one child revision, `b6c7d8e9f0a1`: two nullable overdue marker
  columns, named checks, audit registry extensions, and
  `ix_debts_status_due_date_id`. There is no Payment schema rewrite, new table,
  or backfill; downgrade is loss-guarded.

## Bounded batch

The batch is an application service/test harness operation only. It uses
bounded detached candidate discovery and one forward-locked transaction per
candidate. M15 intentionally adds no scheduler, cron, queue worker, retry
surface, CLI, admin trigger, `job_run` table, or materialization SLA.

## Controlled Chrome/PostgreSQL acceptance

Google Chrome headless via Chrome DevTools Protocol ran against an isolated,
synthetic local PostgreSQL database. The retained facts contain no session,
cookie, credential, phone, UUID, idempotency key, request hash, screenshot, or
fixture data.

- At 320px UZ, OWNER completed an on-time partial, effective/persisted overdue
  forms showed the expected discounted/original bases, an inline late partial
  and full late payoff completed, and a batch materializer was invoked only
  through the local service harness.
- At 430px RU, MANAGER and CASHIER completed on-time partials. Same-key replay
  returned the same receipt; stale discounted-basis submission and one-UZS
  overpayment were denied. Suspended history was read-only; revoked and
  wrong-tenant sessions were denied.
- The two-Shop/one-Customer pass denied Shop B create and Customer acceptance
  while Shop A had effective/persisted overdue state. After the final overdue
  payoff it allowed both; a second service-harness materialized overdue Debt
  restored the block. No cross-Shop financial detail appeared in the denial.
- At 430px RU, own Customer receipts rendered discounted historical and
  original paid-late/current labels. Guessed receipt access stayed generic.
- DevTools observed 10 browser POSTs for 10 explicit actions, document
  `no-store`, empty local/session/Cache Storage, and zero page-console errors.
  The final synthetic aggregate was only: `active=5`, `overdue=1`, `paid=2`,
  `pending=1`, `payments=6`, `audits=16`, `idempotency keys=7`.

Temporary Chrome profiles, local manifests, sessions, synthetic database, and
all helper harness files were deleted after capture.

## Checkpoints

1. `357cb8fd509c8c92314818f3de97d2459a6d18b8` — `M15: freeze overdue scope`
2. `32171f99239e8ff6663c8615ff5bcedc53d6ee20` — `M15: add overdue and clawback contracts`
3. `fa7072f1bec62962bf30e1369395a5fc45d9a05a` — `M15: add overdue persistence`
4. `d387a00eb554e73a66e0fdc60ba4a826fde416b4` — `M15: add deterministic overdue transitions`
5. `65a29f842095cdf8db3679327d7c29607c2744d6` — `M15: add late payments and global hard blocks`
6. `c6c4f40bba1b8e3feb3c38e4da3340bf94fad083` — `M15: expose overdue payment web flows`
7. `bc7206e1c599e287ab5711f86d6ee5464afa4c45` — `M15: harden overdue security and concurrency`
8. `13bda85fb5df99d1be2b1da578e0f1a256f1d336` — `M15: complete overdue payment foundation`

## M15.44 repeated validation

The clean explicit-env run used frozen dependencies, local PostgreSQL, and an
isolated pinned MinIO runtime. The MinIO policy init was idempotent and its
backup/restore evidence was `source=1 backup=1 restored=1 checksum=VERIFIED
privacy=PRIVATE`.

| Check | Result |
| --- | --- |
| Frozen dependency sync | GREEN; 48 packages |
| Alembic upgrade/current/single head | GREEN; `b6c7d8e9f0a1` |
| Ruff check / format-check | GREEN / GREEN; 610 files |
| CI-equivalent MinIO and containment gates | 173 passed in 21.31s |
| Full real-PostgreSQL pytest | 4090 passed in 304.68s; 307.17s wall; zero failed/skipped/xfailed/xpassed/warnings |

## Exact implementation remote evidence

The normal, non-force push checked out implementation SHA
`13bda85fb5df99d1be2b1da578e0f1a256f1d336`, tree
`f99e1de25361438171ae26d1c4bc27d041d3b042`, in GitHub Actions run
[`31347914959`](https://github.com/yalgashev/nasiya/actions/runs/31347914959),
job
[`93333216249`](https://github.com/yalgashev/nasiya/actions/runs/31347914959/job/93333216249).
The job completed successfully in 4m52s on 2026-08-10. Frozen dependency sync,
Alembic upgrade/current/head `b6c7d8e9f0a1`, Ruff check and format-check over
610 files, pinned private MinIO backup/restore and containment gates all
succeeded. The exact-checkout full PostgreSQL suite reported `4090 passed in
213.63s (0:03:33)` with zero test non-passes.
