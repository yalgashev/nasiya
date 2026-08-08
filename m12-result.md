# M12 Result

Status: `M12 REMOTE GREEN — CLOSED`

Date: 2026-08-08

## Milestone

M12 — Tenant-Scoped Shop Customer Foundation

## Final Evidence

| Evidence | Result |
|---|---|
| Eighth implementation checkpoint | `4a36e96c887c5bda51317a80a13d5aeda9384278` |
| Implementation checkpoints | `8/8`; exact linear ancestry intact |
| Exact pushed tree | `d9c80a272a1df86c5aefa1a7e0ff81e68e65c13d` |
| Remote Actions run / job | `31238158808` / `93054450292` |
| Remote full PostgreSQL pytest | `3472 passed`; zero nonpass outcomes |
| Alembic head/current | `e3f4a5b6c7d8` / `e3f4a5b6c7d8` |
| Full local PostgreSQL pytest | `3472 passed` |
| Local outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Repeated focused M12/inherited suite | `289 passed` |
| Real Chrome/PostgreSQL M12.54 | GREEN with approved unreachable-state correction |
| Real Chrome/PostgreSQL M12.55 | GREEN |
| Exact pushed-tree remote CI | GREEN |

## Accepted Boundary

- Existing active customers are linked to the current authorized shop by exact
  phone with one row and one link audit per pair.
- OWNER/MANAGER/CASHIER can read and link; OWNER manages prospective defaults;
  OWNER/MANAGER manage tenant-scoped per-link policy; CASHIER cannot.
- Default snapshots are coherent and prospective. Policy/default stale forms
  fail safely; no-op and replay do not add audit or revision.
- Roster disclosure is masked phone only. Customer own view is linked shop
  names only. Platform-admin has no shop authority without membership.
- M12 introduces no customer bootstrap, activation, Telegram lifecycle, PII
  decrypt, debt/payment, notification, storage, CRM, or global-admin surface.

## Remote Closure

The eighth checkpoint subject is `M12: complete shop customer foundation` at
`4a36e96c887c5bda51317a80a13d5aeda9384278`. The exact pushed tree includes
only one approved docs-only baseline-evidence correction after that checkpoint.
GitHub Actions run `31238158808`, job `93054450292`, checked out the exact pushed
tree and completed every required step successfully, including 3472 PostgreSQL
tests with zero skipped, xfailed, or xpassed outcomes.

The docs-only closeout subject is `docs: close M12 remote evidence`.

`M12 REMOTE GREEN — CLOSED`
