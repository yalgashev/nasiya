# Nasiya M15 Known Limitations

M15 is an overdue/clawback/late-payment and debt-derived hard-block foundation,
not a collections, accounting, or automation platform. These boundaries are
intentional and require a new authorised milestone to change.

1. No scheduler or automatic materialization exists. The bounded batch is
   callable only through application service/test harness; no cron, worker,
   queue, CLI, retry system, job run, admin trigger, or timeliness SLA exists.
2. Hard block is a private Debt-derived boolean, not a rating, score, whitelist,
   blacklist, Customer flag, cross-Shop list, or disclosure mechanism.
3. Payments are immutable. There is no void, refund, reversal, correction,
   import, reconciliation, gateway, bank reference, fiscal receipt, PDF, or
   external settlement integration.
4. Only whole UZS and the existing four staff-recorded method labels exist. No
   interest, fee, penalty, instalment schedule, tax, exchange rate, or
   multicurrency calculation is introduced.
5. There is no written-off/settled lifecycle wiring, notification/outbox,
   report/export, public API, Customer self-pay, platform-admin payment console,
   global search, or cross-tenant support view.
6. M15 does not rewrite historical Payments or backfill existing active rows.
   Effective overdue remains a server-derived read until a lawful mutation or
   bounded service/harness materializes it.
7. Correctness evidence is PostgreSQL-specific: named constraints, row locks,
   idempotency uniqueness, and deterministic barriers are not a SQLite promise.
8. Browser pages are SSR presentation only. They do not persist financial state
   in local/session/Cache Storage, calculate money/time, or support offline
   payment operation.
9. Manual Chrome evidence is synthetic local acceptance evidence only. It does
   not contain production identifiers or replace a future exact-SHA remote CI
   closure.
