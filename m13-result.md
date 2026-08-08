# M13 Result

Status: `M13 REMOTE GREEN — CLOSED`

Date: 2026-08-08

## Milestone

M13 — Pending Debt Foundation

## Current Evidence

| Evidence | Result |
|---|---|
| M13 parent | `7eb138571b1e990b87b6810a05524ad32986bbab` |
| Implementation checkpoints | `8/8`; exact linear ancestry intact |
| Exact pushed implementation tree | `c6b5eb0aed9fcf0b87dd1aabbc5816957e25b840` |
| Eighth checkpoint | `c6b5eb0aed9fcf0b87dd1aabbc5816957e25b840` — `M13: complete pending debt foundation` |
| Alembic head/current | `f4a5b6c7d8e` / `f4a5b6c7d8e` |
| New persistence | `debts`, `idempotency_keys`, debt-scoped `offer_acceptances` extension |
| Exact web routes / audits | 9 / 5 |
| Full local PostgreSQL pytest | 3643 passed; zero nonpass outcomes |
| Focused corrected regression matrix | 53 passed |
| Real Chrome 150 + PostgreSQL shop matrix | GREEN |
| Real Chrome 150 + PostgreSQL customer matrix | GREEN |
| M13.69 repeated full validation | 3643 passed in 331.65s; zero nonpass outcomes |
| Remote Actions run / job | `31261604184` / `93113180042` |
| Remote checkout SHA | `c6b5eb0aed9fcf0b87dd1aabbc5816957e25b840` |
| Remote full PostgreSQL pytest | 3643 passed in 179.29s; zero nonpass outcomes |
| Remote dependency/Ruff/Alembic/MinIO gates | GREEN |

## Accepted Capability

- Active OWNER/MANAGER/CASHIER may create a tenant-owned, policy-gated,
  idempotent pending Debt against the complete current debt-acceptance offer.
- Own customers may accept or reject; active same-shop staff may cancel with a
  required private reason; exact expiry wins at `now >= pending_expires_at`.
- Legal acceptance, five central audits, expected-revision decisions, safe
  projections, CSRF/PRG/no-store, and deterministic PostgreSQL concurrency are
  implemented without locator-derived authority.
- Chrome acceptance verified replay/conflict, policy gates, tenant/customer
  IDOR, suspension/revocation, offer switch, terminal one-winner, and expiry.

## Boundary And Remote Closure

Payment, balance, rating production, notification/outbox, scheduler process,
reporting, public onboarding, PII/storage expansion, platform-admin debt
actions, debt editing, and generic infrastructure remain OUT.

GitHub Actions run `31261604184`, job `93113180042`, checked out exact eighth
checkpoint `c6b5eb0aed9fcf0b87dd1aabbc5816957e25b840` and completed every required
step successfully. The docs-only closeout subject is
`docs: close M13 remote evidence`.

`M13 REMOTE GREEN — CLOSED`
