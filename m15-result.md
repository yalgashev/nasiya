# M15 Result

M15 implementation result: deterministic overdue, clawback, late payment,
marker-based receipt history, and cross-Shop debt-derived hard block are remote
GREEN through exact eighth checkpoint
`13bda85fb5df99d1be2b1da578e0f1a256f1d336` (tree
`f99e1de25361438171ae26d1c4bc27d041d3b042`).

- Routes: the existing six payment routes only; no scheduler, trigger, admin,
  API, self-pay, void, reversal, rating, notification, or report surface.
- Schema: Alembic `b6c7d8e9f0a1`, with `overdue_at`, `overdue_revision`, checks,
  one candidate index, and guarded downgrade.
- Manual synthetic Chrome/PostgreSQL checklists passed for Shop flows,
  Customer/privacy flows, cross-Shop block/unblock, responsive UZ/RU, no-store,
  browser financial storage absence, and console cleanliness.
- Final repeated local validation evidence is recorded in
  [`docs/m15_final_report.md`](docs/m15_final_report.md).
- Exact implementation GitHub Actions run
  [`31347914959`](https://github.com/yalgashev/nasiya/actions/runs/31347914959),
  job
  [`93333216249`](https://github.com/yalgashev/nasiya/actions/runs/31347914959/job/93333216249),
  completed SUCCESS with Alembic head `b6c7d8e9f0a1`, Ruff GREEN, private MinIO
  gates GREEN, and `4090 passed in 213.63s`.
