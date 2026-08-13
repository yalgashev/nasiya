# M17 result — LOCAL GREEN DRAFT

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

This is local technical evidence only. It does not claim a remote push or
GitHub Actions result. See [final report](docs/m17_final_report.md) and [known
limitations](docs/m17_known_limitations.md).
