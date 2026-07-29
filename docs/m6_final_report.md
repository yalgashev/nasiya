# M6 Final Report

Status: **M6 REMOTE GREEN — CLOSED**

Date: 2026-07-28

## Closure Status Summary

- Technical: GREEN.
- Real-bot acceptance: GREEN.
- Remote CI: GREEN.
- Milestone: CLOSED.

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
  processing are disabled on the account page. Public 4xx account-action
  fragments are explicitly swapped so approved rate-limit and re-auth errors
  remain visible without weakening their HTTP status.

## Corrective Acceptance Fixes

Real-browser acceptance found and closed three account-page presentation
defects without changing the M4 token domain, schema, 600-second TTL, worker
TX-A/TX-B, QR dependency, authorization, or supersession semantics:

- A pending relink attempt now owns its reveal and polls its exact
  `telegram_link_tokens.id` attempt route. The existing account link remains
  active until successful consumption; account-level LINKED state cannot
  overwrite WAITING.
- The Telegram deep-link is inert when inserted. It becomes navigable only
  after a trusted explicit pointer or keyboard action on
  `Telegramda ochish`; form submission, synthetic click, polling, and HTMX
  swap cannot launch Telegram.
- An exhausted approved issuance window returns a visible 429 alert. The
  transient `Holat tekshirilmoqda` indicator is no longer mistaken for a
  disappearing initial attempt. Within the approved limit, unlink to initial
  link remains WAITING through polling and reaches LINKED after consumption.

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
- Full pytest after corrective fixes and real-bot acceptance:
  **1373 passed, 0 skipped, 0 xfailed** in 147.43s.
- Independent `pytest -q --durations=10` run:
  **1373 passed, 0 skipped, 0 xfailed** in 146.06s; no pathological
  long-running test was found.
- Warning: one existing Starlette/TestClient `httpx` deprecation warning.
- Collection: 1373 tests.
- Alembic: one head/current, exact `d4e5f6a7b8c9`.
- `git diff --check`: GREEN.
- Tracked secret/PII audit: no Telegram credential shape, acceptance
  deep-link payload, private chat identifier, or phone number was found in
  tracked/report changes.

Focused evidence sets below overlap the full suite and are not additive:

| Category | Result |
| --- | --- |
| Migration/polling persistence/static hardening | 26 passed |
| Fake Bot API, worker recovery, lock, TX-A/TX-B, reply | 156 passed |
| Corrective Telegram/auth regression matrix | 127 passed |
| Sensitive-data and scope leakage matrix | 107 passed |
| Existing auth/customer/shop/Telegram HTTP regression | 244 passed |

Runtime evidence:

- A temporary PostgreSQL database passed base-to-head, downgrade to exact M5
  `a6b4c2d8e9f1`, re-upgrade to exact M6 `d4e5f6a7b8c9`, and repeat upgrade.
  The two M6 operational tables disappeared at M5 and returned at M6 while
  the inherited user/session/customer/shop/Telegram-link tables remained.
  The focused migration suite passed 3 tests; the temporary database was
  removed and no development volume was deleted.
- Current-source no-cache images built for migrate, web, and worker.
- Migration service ran after DB health and exited `0`; web started only after
  migration and served `/health` 200 without a bot token.
- Worker without credentials returned sanitized
  `WORKER_CREDENTIALS_MISSING`, exited nonzero, and was stopped rather than
  left in a restart loop. No real credential was persisted for this smoke.
- Fake runtime covered second-poller lock denial/release, restart cursor,
  duplicate replay, 429, fatal 409, fifth-attempt quarantine, TX-B fatal
  cursor safety, fresh/stale health, post-commit reply failure, and connection
  cleanup.
- Compose HTTP plus automated route tests covered login/session,
  customer draft, shop workspace/isolation, Telegram issue/status/expiry/QR,
  unlink/relink, CSRF, no-store, and security headers. Chrome 320px and 430px
  smoke found no overlap or horizontal overflow.
- Corrective-fix Chrome runs covered relink and initial WAITING persistence,
  explicit-only Telegram handoff, two-tab supersession, and visible 429
  presentation with no top-level external navigation.

## M6.74 Checkpoint

| Required check | Result |
| --- | --- |
| Frozen development install | GREEN; 39 packages checked |
| Ruff check / format check | GREEN / GREEN |
| Full pytest / durations run | GREEN; 1373 tests, 0 skip/xfail |
| Alembic heads/current | GREEN; exact single `d4e5f6a7b8c9` |
| Diff and tracked secret/PII audit | GREEN |
| Full migration walk | GREEN |
| Docker no-cache and runtime smoke | GREEN |
| Fake runtime recovery | GREEN; 156 tests |
| Existing HTTP regression | GREEN; 244 tests |
| Real-bot acceptance | `M6 ACCEPTANCE GREEN` |
| Report/evidence consistency | GREEN |

## Real-Bot Acceptance

M6.72 used the configured untracked dev/test credential and real Telegram
network. No credential, raw token, deep-link, QR, chat identifier, private
message, or screenshot data is recorded here.

| Acceptance area | Result |
| --- | --- |
| Strict preflight, username match, webhook inactive | GREEN |
| Worker lock, heartbeat and health | GREEN |
| Authenticated initial link, private `/start`, web LINKED | GREEN |
| Phone handoff and desktop QR | GREEN |
| Replay protection | GREEN |
| Wrong/correct-password unlink and fresh linking | GREEN |
| Relink WAITING persistence and two-tab supersession | GREEN |
| Explicit-only Telegram handoff and stale-flash removal | GREEN |
| Real 600-second expiry while the existing link stayed active | GREEN |
| Uzbek success/failure bot replies | GREEN |
| Russian success/failure bot replies | GREEN |
| Graceful worker restart and persisted cursor recovery | GREEN |
| Live DB/log secret, token, update and identifier leakage audit | GREEN |

The expiry candidate remained unconsumed and non-invalidated through the
deadline, presented EXPIRED, removed its reveal, and left the existing active
link unchanged. Worker recovery preserved the cursor across restart, advanced
it after a real post-restart terminal update, then preserved the advanced
cursor across a second restart. Heartbeat returned fresh and no poison row was
created.

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
  Real Telegram network/device acceptance is GREEN.

## Gates

### KL-M6-01

Status: **SATISFIED FOR M6.72**

The separate untracked dev/test credential, strict username, webhook-off
state, private round trip, mobile/desktop handoff, restart recovery, language,
expiry and no-leak conditions were verified. The continuing operational
constraint remains: real credentials stay outside tracked files, docs,
fixtures, logs and CI.

### REAL-BOT Gate

Status: **M6 ACCEPTANCE GREEN**

All M6.72 real-bot acceptance areas are complete. Fake transport evidence was
not substituted for the real network/device checks.

### REMOTE-CI Gate

Status: **GREEN — CLOSED**

- Implementation SHA:
  `54df18846663f9eb19ce21a131f796a5b3178bf5`.
- GitHub Actions run: `30383047949`.
- Workflow/job: `CI / dependency-sync`.
- Conclusion: `success`.
- Alembic head: `d4e5f6a7b8c9`.
- Full pytest:
  `1373 passed, 1 warning, 0 failed, 0 skipped, 0 xfail`.
- Validated baseline sync:
  `HEAD == origin/main`, divergence `0 0`, clean worktree.

Historical note: this report was initially prepared before GitHub Actions ran
for the final implementation checkpoint. Run `30383047949` subsequently
validated that exact SHA and closed the remote gate.
