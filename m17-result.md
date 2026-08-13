# M17 result — REMOTE GREEN — CLOSED

M17 implements controlled written-off Debt and recovery: persisted overdue may
be directly written off only by an active platform admin; active Shop staff can
recover it with immutable original-basis Payments. The event history appends
`-40` at write-off and `+10` only on exact terminal settlement, while the
unresolved written-off state remains a global hard block.

The single schema head is `d8e9f0a1b2c3`. M17 adds no table and no historical
backfill; it extends Debt metadata and closed rating/audit/idempotency
registries with guarded downgrade protection. The web surface adds exactly
three platform-admin SSR write-off routes; recovery extends existing Shop and
Customer payment/receipt pages without exposing reason, admin identity, raw
score, event history, keys, hashes, PII, or cross-Shop information.

M17.42–43 controlled Chrome/PostgreSQL acceptance used synthetic data and
retained only sanitized PASS facts: admin write-off/replay/role denial and
recovery/block/privacy/browser behavior. Temporary artifacts were removed.

M17.44 repeated local validation: frozen sync, Alembic current/head, Ruff, and
CI-equivalent MinIO/containment gates were GREEN; the full real-PostgreSQL suite
reported **4371 passed in 290.87s**, with zero failed, skipped, xfailed,
xpassed, or warning outcomes.

The eighth implementation checkpoint is
`5da0ee8d24e0f68f1597e4c96c66237909f8676c`, tree
`dbee3dfbe8663a8b1098d932b91325ae9821b35a`. GitHub Actions run
[`31688853605`](https://github.com/yalgashev/nasiya/actions/runs/31688853605),
job
[`94411245048`](https://github.com/yalgashev/nasiya/actions/runs/31688853605/job/94411245048),
checked out that exact SHA and completed successfully in 5m01s. Alembic was at
`d8e9f0a1b2c3`; the remote full real-PostgreSQL suite reported **4371 passed in
226.71s**, with zero failed, skipped, xfailed, xpassed, or pytest warning
outcomes.

See [final report](docs/m17_final_report.md) and [known
limitations](docs/m17_known_limitations.md).
