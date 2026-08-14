# Nasiya M18 Final Technical Report

Status: **M18 REMOTE GREEN — CLOSED**.

M18 is the bounded **Idempotent Payment Void & Rating Compensation**
capability. It preserves the immutable original Payment and appends one
PaymentVoid-ledger fact only when a live Shop `OWNER|MANAGER` voids the latest
non-voided Payment for that locked Debt.

## Delivered capability

- Authority is an active Shop's live `OWNER|MANAGER` only. CASHIER, Customer,
  platform-admin status without a live Shop role, another Shop, inactive or
  revoked authority, guessed locator, stale target, and double void are denied
  without an authority oracle.
- The five closed reasons are localized Shop labels only. An explicit
  confirmation, CSRF, v1 idempotency hash, and no-store PRG protect the exact
  two SSR routes: `GET` and `POST /shop/payments/{payment_id}/void`.
- Latest means maximum non-voided `Payment.debt_revision_after` beneath the
  locked Debt. A successful void increments that Debt exactly once, shares one
  trusted UTC instant, and leaves the original Payment immutable.
- Partial void stays in its current state. Paid reopens to active or overdue;
  a paid-after-due void appends the canonical `payment_void` overdue/clawback
  evidence and `-15`. A terminal settlement void returns to written-off.
- Current money uses the non-voided anti-join; receipt history uses the exact
  revision-as-of predicate. No float, cached balance, or stored score is
  authoritative.
- Live source-paired compensation is only `+5/-5` and `+10/-10`. Every event
  has positive `source_revision`; fold order is
  `(occurred_at, debt_id, event_type, source_revision)` with a per-event clamp.
  A compensated daily +5 slot remains consumed, while a later lawful day can
  re-earn; +10 has no daily cap. `-15` and `-40` are never reversed.

## Persistence, transaction, audit, and privacy

Alembic child `e9f0a1b2c3d4` is the sole child of `d8e9f0a1b2c3`. It adds the
one append-only `payment_voids` table and `rating_events.source_revision`.
Existing M17 ratings receive only an explicit deterministic source-metadata
backfill. Old RatingEvent business columns and all predecessor Debt, Payment,
Audit, and Idempotency values remain unchanged; ambiguity aborts upgrade.
Downgrade is independently loss-guarded and never deletes M18 evidence.

The caller owns one transaction and uses the frozen forward graph:

```text
Shop -> ShopStaff -> User -> Customer -> ShopCustomer -> IdempotencyKey
-> Debt -> Payment -> PaymentVoid -> RatingEvent -> AuditLog
```

Customer is never re-locked. Repositories borrow the Session and do not commit,
roll back, close, retry, or use post-commit hooks.

The append sequence is key, Debt, PaymentVoid, exact compensation, optional
overdue event, canonical SYSTEM overdue/clawback audits, `payment.voided`, and
only-on-status-change `debt.reopened_after_payment_void`. Shop reads receive a
localized closed reason label; Customer reads receive only voided state/time.
Neither receives actor, raw rating/cause, key/hash, PaymentVoid identifier, or
other-Shop fact. Raw score, delta, event/count, PII, and internal locator data
are likewise absent from body, query, flash, error, log, repr, and audit
payload. Payment identifiers occur only in the existing authorized receipt and
void paths; inherited Debt locators stay on their existing authorized paths and
PaymentVoid identifiers have no presentation path.

## Scope boundary

IN is latest-only append-only void, current/as-of ledger money, lawful reopen,
canonical paid-after-due effects, source-linked compensation/re-earn, one
migration child, idempotency/audit, hard-block/disclosure composition, exactly
two SSR routes, and focused/static/real-PostgreSQL/browser evidence.

OUT remains refund/payout/chargeback, unvoid, edit/delete/correction,
forgiveness, write-off reversal/reason edit, `-15/-40` compensation, score
override/settings, scheduler/worker/CLI/retry, notification/outbox,
report/export, Customer/admin/bulk/search/API/JSON/HTMX void routes, cached
money/rating/block, and M19 code.

## Controlled local Chrome/PostgreSQL acceptance

Chrome 151 via local DevTools ran against an isolated synthetic PostgreSQL
database. The retained M18.42–43 evidence consists only of PASS booleans.
Temporary profiles, sessions, synthetic rows, manifests, cookies, screenshots,
keys, and helper artifacts were removed.

- Active OWNER flow: UZ/RU at 320/430 verified all five localized reason labels,
  semantic confirmation/CSRF accessibility, no horizontal overflow, empty
  browser storage, no-store, zero GET mutation, explicit POST-to-PRG, same-key
  browser replay, and no console error.
- Shop/Customer privacy flow: the Shop receipt retained only a closed reason
  label without actor or key; Customer UZ/RU receipts at 320/430 retained no
  reason, actor, key, or void action. A guessed foreign-Shop receipt was
  generic, and suspended-Shop historical receipt stayed read-only.
- The controlled real-PostgreSQL acceptance matrix covered partial same-state,
  paid-to-active, paid-to-overdue with `-15`, settlement-to-written-off,
  `+5/-5`, `+10/-10`, lawful no-compensation, preceding-stack eligibility,
  same-day no-re-earn/later-day re-earn, role/tenant/revocation/staleness/double
  denial, current/as-of receipt, immutable old versus fresh disclosure,
  void-before-due numeric state, void-after-due and settlement BLOCKED state,
  another-blocker preservation, and repeated payment/recovery cycles.

## Checkpoints

1. `74a054b4f16b44393524d6eb0c44002aa6589f7c` — scope freeze
2. `7527f38051bc204d51fed71c1431709ace446236` — void and compensation contracts
3. `d5329451fded924f4718bd929bedcd5f462ea0f7` — persistence
4. `b2785657d1eecc4cafbfe134a452486bd8ffa3bf` — atomic void producer
5. `0c37f893def9441a59bf65032163c3a7c7783472` — void-safe balances/rating
6. `e0b624e28de8435efd46f7cec06e6f3c49a8c026` — web flows
7. `1399bb15b6da91d7ffad54c85ef8b755e7ae7772` — security/concurrency hardening
8. `924f4859a68584c9e882f54142d2724c35c29732` — implementation completion;
   tree `a3c2f6e5a68409f6afc904d8705feb76d0050d96`

## M18.44 repeated local validation

The clean explicit environment used frozen dependencies, the local PostgreSQL
test database, and the existing pinned local MinIO runtime. The controlled
schema path first downgraded the empty test database to M17 and then upgraded
back to the sole M18 head.

| Check | Result |
| --- | --- |
| Frozen dependency sync | GREEN; 48 packages |
| Controlled M17 downgrade then M18 upgrade/current/single head | GREEN; `d8e9f0a1b2c3 -> e9f0a1b2c3d4` / `e9f0a1b2c3d4` |
| Ruff check / format-check | GREEN / GREEN; 695 files |
| CI-equivalent containment and private-MinIO gate | GREEN; 183 passed in 20.24s |
| Full real-PostgreSQL pytest | GREEN; 4515 passed in 326.45s (0:05:26); zero failed/skipped/xfailed/xpassed/warnings |

## Exact implementation remote evidence

The eighth implementation checkpoint was pushed normally without force,
amend, or rebase. GitHub Actions checked out the exact implementation SHA
`924f4859a68584c9e882f54142d2724c35c29732` and therefore the exact tree
`a3c2f6e5a68409f6afc904d8705feb76d0050d96`.

| Evidence | Result |
| --- | --- |
| Workflow run | GREEN; run `31767812663` |
| Job | GREEN; `dependency-sync`, job `94667196288`, 311s |
| Frozen sync / Ruff | GREEN; 48 packages / 695 files |
| M17 fixture upgrade, current, and sole head | GREEN; `d8e9f0a1b2c3 -> e9f0a1b2c3d4`; `e9f0a1b2c3d4` |
| Source metadata and preservation matrix | GREEN; deterministic four-type `source_revision` population, ambiguity rejection, and predecessor business-value preservation exercised by the real-PostgreSQL suite |
| Containment / private MinIO | GREEN; all bounded gates completed successfully |
| Full real-PostgreSQL pytest | GREEN; 4515 passed in 239.20s (0:03:59); zero failed/skipped/xfailed/xpassed/test warnings |

- Run: <https://github.com/yalgashev/nasiya/actions/runs/31767812663>
- Job: <https://github.com/yalgashev/nasiya/actions/runs/31767812663/job/94667196288>

GitHub emitted one runner-level Node.js action-runtime deprecation annotation
for the pinned checkout/Python/uv actions. It was not a pytest warning or a
repository failure; every workflow step and the job concluded successfully.
