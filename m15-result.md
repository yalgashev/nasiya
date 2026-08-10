# M15 Result

Local M15 result: deterministic overdue, clawback, late payment, marker-based
receipt history, and cross-Shop debt-derived hard block are complete through
the eight-checkpoint implementation sequence. The first seven checkpoint SHAs
and the exact eighth subject are recorded in the final report; the eighth SHA
is intentionally not self-referenced by its own commit.

- Routes: the existing six payment routes only; no scheduler, trigger, admin,
  API, self-pay, void, reversal, rating, notification, or report surface.
- Schema: Alembic `b6c7d8e9f0a1`, with `overdue_at`, `overdue_revision`, checks,
  one candidate index, and guarded downgrade.
- Manual synthetic Chrome/PostgreSQL checklists passed for Shop flows,
  Customer/privacy flows, cross-Shop block/unblock, responsive UZ/RU, no-store,
  browser financial storage absence, and console cleanliness.
- Final repeated local validation evidence is recorded in
  [`docs/m15_final_report.md`](docs/m15_final_report.md).
