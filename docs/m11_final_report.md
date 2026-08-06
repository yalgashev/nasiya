# M11 Final Technical Report

Status: `M11 REMOTE GREEN — CLOSED`
Date: 2026-08-06

This report records the final local baseline, controlled manual acceptance,
exact pushed implementation SHA, successful remote CI evidence, and formal M11
closure.

## Baseline And Authority

- Closed parent: M10 docs-only closeout
  `17ebbe166d63a32e3b7eaa3eb3838f578d9b7780`.
- M11 authority: TT, M11 Final Scope Freeze, `CR-M11-01 — FINAL APPROVED`,
  `CR-M11-02 — FINAL APPROVED`, `CR-M11-03 — FINAL APPROVED`, and the tracked
  M11 repository contracts.
- Seven original M11 checkpoint commits remain intact. The approved recovery
  checkpoint is `6818b55cd51778270853eb72d63b8bd45f3b9884`, making the exact
  implementation checkpoint history `8/8`.
- Manual acceptance exposed a fresh-process model-metadata composition defect.
  The R12-authorized bounded descendant correction is
  `8741ffe7eeb710d05342b43473281d4a5f9c316b`; it is the exact pushed SHA and
  does not relabel or rewrite a checkpoint.

## Delivered Capability

M11 adds server-selected REGISTRATION OTP and the one-way activation of an
authenticated user's existing eligible `draft` customer. Readiness requires
the exact current registration offer acceptance, complete M10 identity,
current AVAILABLE M10 document, and an active Telegram generation whose
self-contact phone equals the canonical Nasiya user phone.

The existing M7 OTP MAC/lifecycle and durable dispatcher are reused. Web
requests perform no Telegram HTTP call. Activation atomically consumes the
challenge, records its typed event and central audit evidence, performs
`draft -> active`, and rotates only the current session/CSRF. Customer, link,
offer, identity, document, object, and session authority is server-derived.

## Migration, Locks, Privacy, And Containment

| Item | Final result |
|---|---|
| Alembic parent / recovery head | `c1d2e3f4a5b6` / `d2e3f4a5b6c7` |
| Alembic current / head | `d2e3f4a5b6c7` / `d2e3f4a5b6c7` |
| Recovery schema | Existing tables only; no new table or runtime dependency |
| Global lock order | CR-M11-01/02 token-first order, UUID ascending per class |
| External Telegram I/O | No open SQLAlchemy Session/transaction |
| Legacy links | Unverified and ineligible until approved self-contact verification |
| Contact mismatch | Generic rejection and zero link/OTP/customer/session/audit mutation |
| Sensitive evidence | No raw phone, OTP, Telegram identity, token, binding MAC, session secret, PII, or provider detail |

The final runtime composition registers transitive database model dependencies
inside `create_database_engine()`. Fresh worker and dispatcher processes can
resolve and sort every foreign-key target without direct entrypoint imports or
weakening M8 storage containment.

## Local Validation

| Check | Result |
|---|---|
| `uv sync --dev --frozen` | GREEN |
| Ruff check and format check | GREEN; format inventory 441 files |
| Focused six-file recovery/runtime suite | 154 passed |
| Targeted containment guard | 4 passed |
| Full PostgreSQL suite | 3252 passed, 0 skipped/xfailed/xpassed, 2 dependency warnings |
| Fresh worker model metadata | GREEN |
| Fresh dispatcher model metadata | GREEN |
| `git diff --check` | GREEN |

The warnings are dependency deprecation notices and are not failures, skips,
xfails, or xpasses.

## Controlled Manual Acceptance

Only synthetic identity/document data and operator-controlled development
accounts were used. Evidence contains safe outcomes and counts only.

1. A different-phone Telegram contact was rejected with zero link, OTP,
   customer, session, or audit mutation. A matching self-contact produced one
   verified link generation.
2. Current-offer switch, identity revision, current-document replacement, and
   protected relink each made the old REGISTRATION code stale before candidate
   comparison, without a failed-attempt increment or partial activation.
3. A fresh code activated exactly once. Refresh/replay created no second
   activation, audit event, or session rotation.
4. Ordinary unlink of the active customer was denied. Protected same-phone
   re-verification succeeded and the customer remained active.
5. After rebuilding from the exact pushed SHA, protected re-verification again
   succeeded with one activation audit total and unchanged session counts.
   A fresh LOGIN request was delivered by the rebuilt dispatcher with status
   `SENT`, no failure code, and zero failed attempts.

## Exact Remote Evidence

| Evidence | Exact result |
|---|---|
| Pushed implementation SHA | `8741ffe7eeb710d05342b43473281d4a5f9c316b` |
| Recovery checkpoint ancestor | `6818b55cd51778270853eb72d63b8bd45f3b9884` |
| GitHub Actions run | `31122632059` |
| Workflow / job | `CI` / `dependency-sync` |
| Job identifier | `92686276933` |
| Run and job conclusion | `success` / `success` |
| Remote full PostgreSQL pytest | 3252 passed |
| Remote outcome matrix | 0 failed, 0 skipped, 0 xfailed, 0 xpassed |
| Remote Alembic recovery head | `d2e3f4a5b6c7` |
| Frozen sync, Ruff, migration, MinIO, M5/M6/M7 containment | GREEN |

The successful run checked out the exact pushed SHA and completed frozen
dependency sync, real PostgreSQL migration/head verification, Ruff, bounded
private MinIO integration and backup/restore, inherited containment guards,
and the full zero-nonpass suite.

## Closure And Preserved Scope

M11 creates no public registration or account/customer bootstrap, public
Telegram link bootstrap, customer lead, shop-assisted onboarding,
`shop_customer`, debt, payment, rating, disclosure, notification, scheduler,
SMS/Web Push, generic OTP/activation framework, new worker/dispatcher/broker,
or M12 product capability. The docs-only closeout is not an implementation
checkpoint and does not rewrite the approved ancestry.

`M11 REMOTE GREEN — CLOSED`
