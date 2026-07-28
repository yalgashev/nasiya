# M6 Final Report

Status: **M6 TECHNICAL GREEN - REAL BOT ACCEPTANCE BLOCKED; REMOTE CI PENDING**

Date: 2026-07-28

## Baseline And Scope

- M5 remote closure baseline:
  `c6812d456602a3c6ab1d1bde2fa2ab4967b212df`.
- M5 implementation evidence: `docs/m5_final_report.md`.
- M5 exact-SHA remote-status evidence: `m5-result.md`, CI run `30281678432`.
- M6 head: `d4e5f6a7b8c9`; exact parent: `a6b4c2d8e9f1`.
- Authoritative M6 contract: `docs/m6_scope_contract.md`; decisions:
  `docs/m6_decisions.md`.
- `/auth/telegram/*` is authenticated account scope. It does not depend on
  `sessions.active_shop_id`, shop selection, membership, status, or
  `require_shop_staff`.
- No webhook/public callback, OTP provider, public registration, customer
  activation, debt/payment, notification platform, or generic scheduler was
  added.

## Delivered Runtime

Schema:

- Existing M4 `telegram_links`, `telegram_link_tokens`, and
  `telegram_link_events` remain the linking domain source of truth.
- M6 adds only `telegram_polling_state` and `telegram_update_failures`.
- Raw link tokens remain response-only and hash-only in PostgreSQL. Raw
  Telegram update payloads are not persisted.

Account routes:

| Method | Route | Contract |
| --- | --- | --- |
| GET | `/auth/telegram` | Account page; authenticated, no-store. |
| GET | `/auth/telegram/status` | Current account link status fragment. |
| POST | `/auth/telegram/link-token` | CSRF + HTMX one-time reveal. |
| POST | `/auth/telegram/relink-token` | Current-password protected reveal. |
| GET | `/auth/telegram/attempts/{attempt_id}/status` | Caller-owned attempt polling. |
| POST | `/auth/telegram/unlink` | Current-password protected, safe repeated unlink. |

Integration:

- `app/telegram/bot_api.py`: direct `httpx` Bot API client, strict preflight,
  25s long poll, 35s read timeout, bounded 429/backoff, injected fake
  transport.
- `app/telegram/worker.py`: dedicated command/healthcheck, one advisory-lock
  owner, persisted cursor, 10s heartbeat, 60s stale threshold, cooperative
  SIGTERM.
- `app/telegram/update_processing.py`: TX-A domain/cursor owner, fresh TX-B
  poison ledger owner, post-commit bot reply boundary.
- `app/auth/router.py`: account-scoped status, one-time reveal, QR,
  unlink/relink and re-auth integration.
- `app/telegram/qr.py`: local `segno` standard QR PNG, EC M, boost disabled,
  scale 5, border 4, in memory only.
- `compose.yaml`: one-shot migration, web/worker migration dependency,
  worker replica 1, restart `unless-stopped`, stop grace 45s, healthcheck
  15s/5s/3 with 20s start period.
- `Dockerfile`: shared image contract and disabled Uvicorn access log to
  prevent raw client-IP logging.

Dependencies:

- Runtime `httpx==0.28.1` under the approved `>=0.28.1,<0.29` bound.
- Runtime `segno==1.6.6` under the approved `>=1.6.6,<2` bound.
- Locally vendored `htmx 2.0.4`, checksum and license recorded in
  `docs/m6_decisions.md`; no CDN. History/cache, eval, and swapped script
  processing are disabled on the account page.

## Transaction And Failure Protocol

- Request dependency owns web commit/rollback/close. Telegram domain services
  do not commit or perform full rollback.
- Worker owns TX-A. Expected terminal outcomes advance the cursor with no
  poison count. Allowlisted transient failures roll back without cursor or
  poison mutation.
- Unknown TX-A failure rolls back, then fresh TX-B increments a durable
  sanitized failure row. Attempt 5 quarantines and advances the cursor in the
  same transaction. Unknown TX-B failure is fatal and cannot move the cursor.
- Bot reply occurs only after TX-A commit and outside every DB transaction.
  Reply failure cannot undo linking or increment poison state; web status is
  canonical.

## Local Evidence

Final code-level validation:

- `uv sync --dev --frozen`: GREEN, 39 packages checked.
- Ruff check: GREEN.
- Ruff format check: GREEN, 195 files.
- Full pytest: **1368 passed, 0 skipped, 0 xfailed** in 80.71s.
- Warning: one existing Starlette/TestClient `httpx` deprecation warning.
- Collection: 1368 tests.
- Alembic: one head/current, exact `d4e5f6a7b8c9`.
- `git diff --check`: GREEN.
- Tracked Telegram credential-shape scan: zero token-pattern hits.

Focused evidence sets below overlap the full suite and are not additive:

| Category | Result |
| --- | --- |
| Migration/polling persistence/static hardening | 26 passed |
| Fake Bot API, worker recovery, lock, TX-A/TX-B, reply | 137 passed |
| Auth/customer/shop/Telegram HTTP regression | 581 passed |
| Final scope/deployment/static regression | 14 passed |

Runtime evidence:

- Separate PostgreSQL DBs passed base-to-head, M6-to-M5-to-M6, repeat upgrade,
  M1-M5 preservation, expected M6 tables/constraints/indexes, and exact
  current-head checks. Temporary DBs were removed; no development volume was
  deleted.
- Current-source no-cache images built for migrate, web, and worker.
- Migration service ran after DB health and exited `0`; web started only after
  migration and served `/health` 200 without a bot token.
- Worker without credentials returned sanitized
  `WORKER_CREDENTIALS_MISSING` and failed closed.
- Fake runtime covered second-poller lock denial/release, restart cursor,
  duplicate replay, 429, fatal 409, fifth-attempt quarantine, TX-B fatal
  cursor safety, fresh/stale health, post-commit reply failure, and connection
  cleanup.
- Compose HTTP plus automated route tests covered login/session,
  customer draft, shop workspace/isolation, Telegram issue/status/expiry/QR,
  unlink/relink, CSRF, no-store, and security headers. Chrome 320px and 430px
  smoke found no overlap or horizontal overflow.

## TT Traceability

- TT 6.1 linking subset: one-time 10-minute token, private bot consume,
  deep-link, desktop QR, HTMX status, unlink/relink are covered. TT OTP and
  public phone-entry sequence remain outside approved M6 scope.
- TT 8: authenticated session, CSRF, no-store, CSP, same-origin script,
  hash-only token storage, no raw credential/update persistence, re-auth and
  rate limits are covered.
- TT 9: safe Uzbek Latin and Russian web/bot linking messages are covered.
  Full profile language persistence remains outside M6.
- TT 10: worker-independent web health, persisted cursor, retry/recovery,
  migration-only schema changes and no-PII operational codes are covered.
  Broader platform monitoring/backup requirements remain future scope.
- TT 11: real PostgreSQL migration, replay, expiry, concurrency, IDOR,
  leakage, browser/static and fake-transport recovery tests are covered.
  Real Telegram network/device acceptance remains open.

## Open Gates

`KL-M6-01` remains active. No dev/test bot token or matching username is
configured, so M6.72 is honestly:

**BLOCKED: REAL TELEGRAM BOT NOT READY**

The 14-step real mobile/desktop acceptance must be completed before
PRE-PRODUCTION approval. No fake-transport result is classified as real
acceptance.

GitHub Actions has not yet run for the final checkpoint SHA. Remote status is
therefore **REMOTE CI PENDING** and M6 is not remote-closed.
