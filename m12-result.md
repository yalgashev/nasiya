# M12 Result

Status: `M12 LOCAL GREEN — REMOTE PENDING`

Date: 2026-08-08

## Milestone

M12 — Tenant-Scoped Shop Customer Foundation

## Final-Local Evidence

| Evidence | Result |
|---|---|
| Current hardened checkpoint | `5ba01e18d668d6249a8c330ad7a1d93194ba9cac` |
| Implementation checkpoints | `7/8`; exact ancestry intact |
| Alembic head/current | `e3f4a5b6c7d8` / `e3f4a5b6c7d8` |
| Full local PostgreSQL pytest | `3472 passed` |
| Local outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Repeated focused M12/inherited suite | `289 passed` |
| Real Chrome/PostgreSQL M12.54 | GREEN with approved unreachable-state correction |
| Real Chrome/PostgreSQL M12.55 | GREEN |
| Working tree before final-doc draft | clean |

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

## Pending Remote Closure

The eighth checkpoint subject is `M12: complete shop customer foundation`.
Its exact SHA does not exist until M12.57. Push occurs only at M12.59 after the
M12.58 clean eight-checkpoint audit. Exact pushed SHA, GitHub Actions run/job,
remote counts, and docs-only closeout SHA/CI outcome are therefore `pending`.

Final success remains reserved for M12.60:

`M12 REMOTE GREEN — CLOSED`
