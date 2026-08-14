# M18 result — REMOTE GREEN — CLOSED

M18 delivers authorized latest-only append-only Payment void with exact
current/as-of non-voided money, lawful Debt reopen, source-linked `+5/-5` and
`+10/-10` compensation, re-earn safeguards, hard-block/disclosure composition,
idempotency, audit, privacy, and exactly two Shop SSR void routes.

The sole migration head is `e9f0a1b2c3d4`. It adds one PaymentVoid ledger table
and explicitly backfills only `rating_events.source_revision`; it preserves
pre-M18 business data and denies lossy downgrade.

M18.42–43 controlled Chrome 151/PostgreSQL acceptance used synthetic local
data and retained only sanitized PASS booleans for authority, POST/PRG/replay,
state/rating/balance, cross-Shop privacy, UZ/RU 320/430, storage/no-store, and
accessibility checks. Temporary artifacts were removed.

M18.44 repeated local validation was GREEN: frozen sync (48 packages),
controlled M17-to-M18 Alembic path/current/head `e9f0a1b2c3d4`, Ruff over 695
files, containment/private-MinIO (`183 passed in 20.24s`), and full
real-PostgreSQL pytest (`4515 passed in 326.45s`) with zero nonpass outcomes.

The eighth implementation checkpoint is
`924f4859a68584c9e882f54142d2724c35c29732`, tree
`a3c2f6e5a68409f6afc904d8705feb76d0050d96`. Exact GitHub Actions run
[`31767812663`](https://github.com/yalgashev/nasiya/actions/runs/31767812663),
job
[`94667196288`](https://github.com/yalgashev/nasiya/actions/runs/31767812663/job/94667196288),
checked out that SHA/tree and completed successfully in 311s. Frozen sync,
M17-to-M18 migration/current/head, deterministic `source_revision` population
and source preservation, Ruff, containment/private-MinIO, and the full
real-PostgreSQL suite were GREEN. The full suite was `4515 passed in 239.20s`
with zero failed/skipped/xfailed/xpassed/test warnings.

GitHub also displayed one runner-level Node.js action-runtime deprecation
annotation; it was not a test warning, and no workflow step failed. See [final
report](docs/m18_final_report.md) and [known limitations](docs/m18_known_limitations.md).
