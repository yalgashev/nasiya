# M12 Final Technical Report

Status: `M12 REMOTE GREEN — CLOSED`
Date: 2026-08-08

This report records the exact pushed implementation tree, controlled Chrome and
PostgreSQL acceptance, local validation, preserved scope, and successful remote
evidence for M12 closure.

## Baseline And Authority

- Closed parent: M11 docs-only closeout
  `7d8e14b2da2a77008cf3e999d77aabf277d72137`.
- Authority: TT, M12 Product Gate, M12 Final Scope Freeze, and the tracked M12
  scope contract, decisions, repository map, and known limitations.
- Eighth implementation checkpoint:
  `4a36e96c887c5bda51317a80a13d5aeda9384278`.
- All eight required checkpoint subjects and their linear ancestry are intact.
- Exact pushed tree: `d9c80a272a1df86c5aefa1a7e0ff81e68e65c13d`.
  Its only descendant change after checkpoint eight is the approved docs-only
  correction of the M11 closeout SHA in this report; no product code changed.

## Delivered Capability

M12 adds tenant-scoped linking of an existing auth-active User and existing
active Customer to the current live shop by exact canonical phone. The target
must retain a current self-phone-verified Telegram generation. Successful
linking snapshots one coherent pair of locked shop defaults, is idempotent per
shop/customer pair, emits one bounded central audit event, and reveals only a
masked phone in the authorized roster.

OWNER changes prospective defaults. OWNER or MANAGER changes one tenant-scoped
relationship policy with optimistic revision control; CASHIER can read and
link but cannot change policy. The customer own view returns linked shop names
only. Platform-admin never substitutes for live ShopStaff authority.

## Persistence, Locks, Privacy, And Containment

| Item | Final-local result |
|---|---|
| Alembic parent / head | `d2e3f4a5b6c7` / `e3f4a5b6c7d8` |
| Schema surface | Two Shop default columns and one `shop_customers` table |
| Money | Decimal whole UZS / `NUMERIC(18,0)`; no float |
| Transaction phases | Closed auth/CSRF, closed rate write, then domain transaction |
| Global locks | Frozen forward order; same-class UUID ascending |
| Tenant authority | Current session shop plus live membership; platform-admin no bypass |
| Target authority | Exact phone resolved server-side; no client identity UUID |
| Sensitive projection | Masked roster and linked shop names only; no M10 decrypt |
| OUT containment | No debt, payment, activation, Telegram, storage, notification, or CRM mutation |

## Local Automated Validation

| Check | Result |
|---|---|
| Frozen dependency sync | GREEN |
| Ruff check / format check | GREEN / GREEN |
| Focused M12 and inherited matrix | GREEN |
| Real-PostgreSQL correction matrix | 30 passed |
| Full PostgreSQL suite | 3472 passed; zero failed/skipped/xfailed/xpassed |
| Alembic current / single head | `e3f4a5b6c7d8` / `e3f4a5b6c7d8` |
| Diff and sensitive-containment checks | GREEN |

M12.56 repeated the migration/head checks, 289-test focused suite, and full
3472-test suite after these documents were drafted. Every repeated gate was
GREEN with zero failed, skipped, xfailed, or xpassed outcomes.

## Controlled Manual Acceptance

Only synthetic or operator-controlled accounts were used. Evidence contains
safe statuses and counts, never raw phones, UUIDs, credentials, sessions,
Telegram identifiers, PII, or internal metadata.

1. The exact checkpoint image was rebuilt/recreated without deleting volumes;
   web provenance, health, and PostgreSQL current/head were GREEN.
2. OWNER created eligible links; MANAGER and CASHIER idempotent repeats kept one
   relationship and one linked audit per target. Missing and draft targets had
   identical generic outcomes and zero domain mutation.
3. Cross-tenant locator use was denied. Suspended-shop roster read remained
   available while linking and policy controls were absent; the shop was
   restored to active through the approved local CLI.
4. Platform-admin without membership was denied in real Chrome. The approved
   M12.54 correction covers unreachable disabled and active-unverified states
   with the 30-test real-PostgreSQL matrix, without a direct DB/ORM fixture.
5. OWNER defaults were prospective: existing links stayed unchanged and a
   later link received the complete new pair. MANAGER completed
   normal-to-whitelisted-to-blacklisted policy changes; CASHIER was denied.
6. Two-tab default and policy stale submissions were rejected; replay/no-op
   created no extra audit or revision. The roster contained masked phones only.
7. The active customer's own view showed only the linked shop name. Customer,
   Telegram, OTP, activation, identity, document, storage, debt, and payment
   state remained outside M12 mutation.

## Checkpoints And Remote Evidence

The eight implementation checkpoints are:

1. `1e586da00c78b189151a1a891a4f5b89fd5ff476` — `M12: freeze shop customer scope`
2. `ef2dfbc0b128b6717149bc7c2d07401e67536bd0` — `M12: add shop customer contracts`
3. `d02005039614a9b07e9b52ad3fa8cc8b495d6d16` — `M12: add shop customer persistence`
4. `df9929477d91406ec6afa80c8436abe66aef9ea2` — `M12: add active customer linking`
5. `616fd7facda27e900b98e4d46b76bc57e545ed65` — `M12: add customer credit policy`
6. `788b48d8ac151253f9fca239329ada712115bae7` — `M12: expose shop customer web flows`
7. `5ba01e18d668d6249a8c330ad7a1d93194ba9cac` — `M12: harden tenant customer security and concurrency`
8. `4a36e96c887c5bda51317a80a13d5aeda9384278` — `M12: complete shop customer foundation`

| Remote evidence | Final value |
|---|---|
| Eighth implementation checkpoint | `4a36e96c887c5bda51317a80a13d5aeda9384278` |
| Exact pushed tree | `d9c80a272a1df86c5aefa1a7e0ff81e68e65c13d` |
| GitHub Actions run / job | `31238158808` / `93054450292` (`dependency-sync`) |
| Remote full pytest | 3472 passed; zero failed/skipped/xfailed/xpassed |
| Required remote workflow | All required steps completed successfully |
| Docs-only closeout | `docs: close M12 remote evidence` |

The docs-only closeout is a linear descendant of the exact remote-GREEN tree.
It changes only the three M12 closeout documents authorized by M12.60.
