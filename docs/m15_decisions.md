# Nasiya M15 Decisions

This is the final decision record for M15. It implements the approved product
gate and final scope freeze without broadening either document.

## Baseline decision

M15 starts at documentation-closeout HEAD
`881413608f16db054078448676d6fae71afe6221`, Alembic head `a5b6c7d8e9f0`, and
the green M14 evidence recorded in
[`m15_scope_contract.md`](m15_scope_contract.md). M15.06 creates repository
authority only; it does not claim product implementation.

## Product Owner decisions — final

1. M15 is the overdue/clawback/late-payment/debt-derived-hard-block foundation,
   not the full collections lifecycle.
2. The only newly persisted Debt status is `overdue`; written-off states remain
   runtime OUT.
3. Overdue means `tashkent_business_date(now) > due_date`; due date is inclusive.
   Payment time is captured once after Debt lock, while proposal/acceptance
   hard-block time is captured once after Customer lock. Pre-lock request or
   command datetime is not authority.
4. There is no grace period, daily penalty, automatic write-off, or client-time
   authority.
5. `active -> overdue` consumes exactly one revision and writes `overdue_at` and
   `overdue_revision` once.
6. `overdue_revision` is a lifecycle/history marker, not a balance or clawback
   cache.
7. On-time remaining is `max(discounted - posted, 0)`; overdue remaining is
   `max(original - posted, 0)`.
8. Clawback is exact-once with rollover; `debt.overdue` and
   `debt.clawback_applied` SYSTEM audits share its transaction.
9. Zero discount still produces the transition and both audits; balance increase
   may be zero.
10. Successful inline payment of active past-due first rolls over, then pays;
    those transitions consume separate revisions.
11. Partial persisted-overdue payment stays `overdue`; exact original remaining
    becomes `paid`.
12. M14 amount, method, idempotency, role, and tenant rules remain unchanged for
    late payment.
13. Forms send stale-check tokens `expected_revision` and
    `expected_balance_basis=discounted|original`; neither is authority.
14. If basis changes between render and POST, return `DEBT_CHANGED`; never turn
    an old discounted form silently into original-basis payment.
15. Same-key/same-hash replay repeats no rollover, Payment, or audit; the v2 hash
    binds expected basis.
16. Pre-overdue receipt history is discounted and post-overdue history original;
    `overdue_revision` is the exact separator.
17. Current active-past-due views show effective overdue/original basis without a
    GET mutation.
18. Exposure for pending/active/overdue is `max(original - posted, 0)` (pending
    has zero posted); paid exposure is zero.
19. Open count includes `pending`, `active`, and `overdue`, excluding paid and
    earlier terminal states.
20. Persisted overdue or unmaterialized active past-due globally blocks proposal
    and acceptance across Shops.
21. Hard block is not a score: it creates no rating data and reuses the safe
    `CUSTOMER_RATING_BLOCKED` error.
22. Repayment is not gated by Customer, Telegram, list, or rating state; full
    late payoff atomically clears the block when no other overdue Debt exists.
23. Batch uses bounded non-locking discovery and one forward-locked transaction
    per candidate; overlapping runs are exact-once/no-op.
24. M15 creates no cron, worker, scheduler, `job_run`, retry, admin trigger, or
    midnight materialization SLA.
25. Shop suspension does not stop time-driven batch or historical reads, but it
    denies creation of a late payment.
26. Migration does not time-based bulk-update existing active rows; effective
    policy blocks them and batch may materialize them.
27. Debt gains only `overdue_at`, `overdue_revision`, exact check/status/index
    extensions; there is no new business table or cached financial column.
28. CR-M6-03 clawback reversal remains valid but is not pre-built as an M15
    route, field, or service.
29. Rating `+5/-15`, farming/backfill, notification, void, write-off, settlement,
    reports, and admin-global actions are deferred.
30. Delivery uses one linear Alembic revision, real PostgreSQL barriers, eight
    checkpoints, exact-SHA CI, and docs-only closeout.

## Implementation decisions fixed by M15.01–05

- Missing basis is only an M14-v1 completed-key replay candidate. Without a
  matching completed v1 row it is a zero-write denial; new mutations are typed
  v2 commands.
- V2 permits a +2 inline rollover/payment revision and a +1 already-overdue
  payment revision.
- The fixed forward lock order and ascending same-class UUID rule remain
  normative. Batch includes Customer before ShopCustomer and Debt.
- Hard block is Debt-derived, never a Customer flag/cache, and `app.debt`
  remains import-free of `app.payment`.
- Existing form/detail plumbing emits hidden basis atomically with production v2
  enforcement. SSR, CSRF, and PRG remain mandatory.
- `paid_at` may move null-to-timestamp once at lawful full payoff. Original
  money/due/acceptance and populated terminal metadata stay immutable.
- Static OUT guards are source-scoped: inherited enum vocabulary is allowlisted,
  while new runtime/persistence/orchestration wiring is denied.

## Stable outcome decision

Lifecycle, formulas, IN/OUT, migration constraints, and evidence are normative
in [`m15_scope_contract.md`](m15_scope_contract.md). Symbol placement, locking,
and test mapping are normative in
[`m15_repository_map.md`](m15_repository_map.md). Exact persistence and
downgrade constraints are normative in
[`m15_persistence_plan.md`](m15_persistence_plan.md).

## Closure decision

M15.06 authorises only the later bounded implementation described by these
documents. It neither executes M15 work nor relaxes M1–M14 contracts.
