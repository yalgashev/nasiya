# M18 result — LOCAL FINAL DRAFT

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

This is a local final draft, not a remote-GREEN claim. See [final
report](docs/m18_final_report.md) and [known
limitations](docs/m18_known_limitations.md).
