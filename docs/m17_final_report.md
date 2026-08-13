# Nasiya M17 Final Report

Status: **M17 REMOTE GREEN — CLOSED**.

## Capability delivered

M17 adds the controlled written-off Debt and recovery foundation:

```text
persisted overdue -- platform-admin write-off --> written_off
written_off -- partial original-basis Payment --> written_off
written_off -- exact full original-basis Payment --> written_off_settled
```

Only a coherent persisted overdue source can be written off. A live platform
admin supplies one of five closed reasons. The write-off atomically records its
marker/revision/actor, immutable `-40` event, USER/Debt audit, and idempotent
result. Active Shop OWNER, MANAGER, or CASHIER may record recovery Payments;
partial payment keeps the block, while exact full original-basis payment adds
the one live `+10` settlement event and marker. `paid_at` remains NULL in both
written-off states.

The rating ledger remains sequential: start at 60, order by
`(occurred_at, debt_id, event_type)`, and clamp after every event. The
unresolved-written-off hard-block overlays the numeric band across Shops;
settlement removes only that Debt's block. Existing disclosure snapshots remain
historical and band-only.

## Persistence, privacy, and routes

Alembic child `d8e9f0a1b2c3` extends Debt with six write-off/settlement fields,
named checks and actor FK, and extends rating/audit/idempotency registries. It
adds no table and no backfill: pre-M17 source rows retain their canonical M16
Debt columns and existing Payment/rating/audit rows are not rewritten.
Downgrade guards reject M17 state before schema loss.

The only new routes are:

- `GET /admin/debts/write-off-candidates`
- `GET /admin/debts/{debt_id}/write-off`
- `POST /admin/debts/{debt_id}/write-off`

They are SSR, CSRF-protected, idempotent, PRG, and `no-store`. The bounded
candidate queue has no global search. Shop and Customer views show safe status,
payment, and receipt projections only; reason, admin identity, raw score,
event history/count, causes, hashes, keys, PII, and other-Shop facts remain
absent from unauthorized surfaces.

## Scope boundary

IN is direct authorized write-off, original-basis recovery, immutable
`-40/+10` events, hard-block/disclosure composition, one migration, three SSR
routes, and PostgreSQL/privacy/concurrency evidence.

OUT remains void/refund/reversal/correction/compensation, scheduler/worker/CLI,
notifications/outbox, reports/export, override/settings, bulk/search, API or
JSON, Customer self-pay, cached score/balance, and M18 work.

## Controlled manual acceptance

Synthetic local PostgreSQL and Chrome 150 acceptance retained only sanitized
booleans. The M17.42 pass covered active platform-admin candidate/detail,
five reason labels, confirmation POST/PRG/replay, effective-only exclusion,
generic bad locator/stale outcome, suspended-Shop/inactive-Customer target,
non-admin/ShopStaff/revoked denial, UZ/RU 320/430 presentation, `no-store`,
storage absence, keyboard/accessibility, and no console errors. It confirmed
the durable `-15` then `-40` source chain and BLOCKED outcome without retaining
IDs, counts, PII, reasons, cookies, profiles, or screenshots.

The M17.43 controlled browser/service acceptance covered partial recovery,
payment replay, terminal settlement with `+10`, old snapshot immutability and
fresh numeric snapshot, Customer read-only receipt, active staff recovery
surface, suspended new-recovery denial with historical-read coverage, privacy
redaction, UZ/RU mobile layouts, and browser storage/CSP checks. Temporary
profiles and synthetic fixtures were removed. The full cross-Shop/second-block
and every negative authority combination are additionally covered by the
deterministic real-PostgreSQL and web matrices.

## Checkpoints

1. `a80989be8959f96f496356e80859b42c277b5beb` — M17 scope freeze
2. `4037e37294a02f56f2d06f70e5e3a7ddc0158039` — contracts
3. `3d5462fabdedbc66caf7618e6aff18b13d2ea8b1` — persistence
4. `3bd85e7d7d3b7ed72fc9df3e052d8efd97821cf3` — write-off transition
5. `8cbf8d53d1c4638dfa65070a74314ed55db388a4` — recovery integration
6. `8d22238c55cf80cee2b2dd81a89b7ce9b81dc2da` — web flows
7. `1884092f186d58141b2568d762c577efed724f34` — hardening
8. `5da0ee8d24e0f68f1597e4c96c66237909f8676c` — implementation completion

The eighth implementation tree is
`dbee3dfbe8663a8b1098d932b91325ae9821b35a`.

## M17.44 repeated local validation

The clean explicit environment used frozen dependencies, local PostgreSQL, and
the existing pinned local MinIO runtime.

| Check | Result |
| --- | --- |
| Frozen dependency sync | GREEN; 48 packages |
| Alembic upgrade/current/single head | GREEN; `d8e9f0a1b2c3` |
| Ruff check / format-check | GREEN / GREEN; 677 files |
| CI-equivalent MinIO and containment gates | 173 passed in 19.57s |
| Full real-PostgreSQL pytest | 4371 passed in 290.87s; zero failed/skipped/xfailed/xpassed/warnings |

Operational migration prerequisite remains explicit: drain old writers and do
not restart an old binary while applying the M17 revision; the revision takes
the documented Debt table barrier before schema work.

## Remote implementation evidence

GitHub Actions checked out implementation SHA
`5da0ee8d24e0f68f1597e4c96c66237909f8676c`, whose tree is
`dbee3dfbe8663a8b1098d932b91325ae9821b35a`. CI run
[`31688853605`](https://github.com/yalgashev/nasiya/actions/runs/31688853605),
job
[`94411245048`](https://github.com/yalgashev/nasiya/actions/runs/31688853605/job/94411245048),
completed successfully in 5m01s. Frozen sync, the M16 fixture upgrade, Alembic
current/head `d8e9f0a1b2c3`, zero-backfill/source guards, Ruff,
containment/MinIO gates, and full real-PostgreSQL pytest were GREEN. The full
suite reported **4371 passed in 226.71s**, with zero failed, skipped, xfailed,
xpassed, or pytest warning outcomes.
