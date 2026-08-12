# M16 final report

## Delivered capability

M16 adds an append-only, tenant-scoped rating ledger and private risk-band
disclosures. A score begins at 60 and folds immutable events in
`(occurred_at, debt_id, event_type)` order, clamping after every delta to
`[0,100]`. No event is `NEW`; numeric values map to `GREEN`, `YELLOW`, or
`RED`. The existing M15 effective overdue read remains a hard-block overlay:
it produces `BLOCKED` before the numeric band is exposed.

Only a locked, pre-active exact full payoff earns `+5`: original amount is at
least 100000 UZS, accepted Tashkent date is before paid date, and paid date is
not after the due date. The first eligible pair/day wins; a lawful loser still
records its Payment. A materialized overdue produces `-15` at `overdue_at`.
Late payoff retains that history while removing the overlay when no overdue
remains. Reconciliation reads coherent M14/M15 source and audit facts,
orders deterministically, uses UUIDv5, and never rewrites a source row.

The migration has one child head, `c7d8e9f0a1b2`, and exactly two tables:
`rating_events` and `disclosure_view_logs`. Redundant parent uniques,
composite chain FKs, checks, indexes, source uniqueness, and guarded downgrade
protect the ledger. The operational old-writer drain is required before the
revision-local Debt table lock.

Staff have exactly these routes: POST
`/shop/customers/{shop_customer_id}/risk-band-disclosures` and GET
`/shop/risk-band-disclosures/{disclosure_view_id}`. The POST is CSRF-protected,
idempotent PRG; GET is an actor/shop-scoped immutable snapshot. Presentation,
audit, repr, errors, and browser output contain only purpose, band, and view
time—never score, event history, cause, PII, raw key, hash, or authority IDs.

## Controlled acceptance

On 2026-08-12, a temporary local PostgreSQL database and Chrome 151 were used
with synthetic staff/customer data. The sanitized M16.42 producer checklist
was PASS for NEW, below-threshold and same-day non-bonus, eligible +5,
same-pair/day lawful no-bonus, other-Shop +5, effective block, inline and
direct-harness batch -15, and late-payoff history retention. The browser
checklist was PASS for UZ 320px and RU 430px form rendering, staff POST/PRG,
band-only result, no-store, empty browser storage, and no page exception.
M16.43 checked the three closed purposes, immutable snapshots/replay/new-view
behavior, suspended/revoked/foreign denial, and locator/key privacy. Retained
evidence is boolean-only; temporary profiles, sessions, fixtures, logs, and
screenshots were removed.

## Repeated local validation

M16.44 repeated frozen dependency sync, PostgreSQL upgrade/current/heads,
Ruff check, and Ruff format check on 2026-08-12. Alembic resolved only
`c7d8e9f0a1b2`; Ruff reported all checks passed and 652 files already
formatted. The explicit-environment full real-PostgreSQL pytest run completed
as `4250 passed in 328.22s (0:05:28)`, with zero failed, skipped, xfailed,
xpassed, or warnings. This is local evidence only.

## Checkpoints and scope

Checkpoints: `a887715`, `6664ade`, `8938e75`, `d49368c`, `9c42518`,
`1461a94`, and `dba4b6f`. OUT remains excluded: rating overrides, written-off
or compensation deltas, notifications, scheduler/jobs, reports, Customer or
admin/API routes, global search, and cached score/band wiring.
