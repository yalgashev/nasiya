# M6 Scope Contract

Status: PO-M6 DEFAULT DECISIONS CLOSED
Date: 2026-07-27

This document is the executable M6 implementation contract after PO approval
in chat: `defaul qarorlar bilan yopilsin`, followed by authorization to execute
the v0.9 guide from M6.06 onward. The single M6 capability is Production
Telegram Linking for an existing authenticated account. This freeze does not
change the TT text.

## 1. Baseline

| Item | Value |
| --- | --- |
| M5 closure SHA | `c6812d456602a3c6ab1d1bde2fa2ab4967b212df` |
| M5 remote closeout | `m5-result.md`, GitHub Actions run `30281678432`, workflow `CI`, job `dependency-sync`, conclusion `success` |
| M5 implementation evidence | `docs/m5_final_report.md` |
| Parent Alembic head | `a6b4c2d8e9f1` |
| Local M5 baseline | `1113 passed, 0 skip, 0 xfail, 1 existing Starlette/httpx warning` |

`docs/m5_final_report.md` is implementation evidence. `m5-result.md` is
remote-status evidence and supersedes the pre-push `REMOTE CI PENDING` line in
the report.

## 2. Inherited Decisions

| ID | Source | Decision | M6 impact |
| --- | --- | --- | --- |
| M4-I01 | `app/telegram/service.py` | Telegram link lifecycle is server-side domain logic: issue, consume, status, unlink, relink. | M6 must reuse caller-owned transaction services and must not add real Bot API transport to domain code. |
| M4-I02 | `app/telegram/inbound.py` | Inbound identity is a typed verified private chat id; fake adapter is zero-network. | Automated tests use fake transport. Only the explicit M6.72 acceptance may use a real dev bot; CI never does. |
| M4-I03 | `app/telegram/client_ip.py` | Client IP is `ResolvedClientIp`, redacted and canonicalized. | Login and Telegram issuance adapters use one fail-closed `direct | trusted_proxy` resolver without storing raw IP. |
| M4-I04 | `app/telegram/rate_limit.py` and settings | Telegram issuance rate limits are 900 seconds: 3 user attempts, 3 phone attempts, 20 IP attempts. | M6 reuses these exact attempt limits. |
| M5-I01 | `app/db.py` and `app/cli.py` | Request and non-request DB work have caller-owned transaction boundaries. | M6 services do not commit, rollback, or close sessions. |
| M5-I02 | `app/auth/models.py` | `sessions.active_shop_id` exists for shop context only. | `/auth/telegram/*` must not depend on selected shop state. |
| M5-I03 | `.github/workflows/ci.yml` | CI is one job, with Alembic migration, head assertion, Ruff, M5 containment guard, and full pytest. | M6 keeps the single-job shape and only updates the hardcoded head when a new migration exists. |

Earlier debt/payment and shop-settings decisions remain recorded in
`docs/m6_change_requests.md` for a later milestone. They do not expand or
replace the current Production Telegram Linking capability.

## 3. New PO-M6 Decisions

| ID | Decision |
| --- | --- |
| PO-M6-1 | M6 turns the M4 Telegram-linking domain foundation into a production long-polling adapter and authenticated account web flow. `/auth/telegram/*` remains independent of `active_shop_id`, shop membership/status, `resolve_current_shop`, and `require_shop_staff`. |
| PO-M6-2 | A/B/C update policy is fixed: A = expected terminal, cursor advances and no retry; B = explicit transient infrastructure allowlist, no cursor advance and no poison count; C = unknown/unexpected TX-A handler failure, durable count, with attempt 5 quarantined and cursor advanced atomically in TX-B. Successful retry deletes a non-quarantined ledger row; unknown TX-B failure is fatal. |
| PO-M6-3 | Worker values are fixed: `getUpdates` long poll `25s`, HTTP read timeout `35s`, advisory-lock acquisition deadline `60s`, Compose `stop_grace_period: 45s`, replica `1`, `restart: unless-stopped`. Migrations run in a one-shot service before web/worker. Worker health uses PostgreSQL readiness/heartbeat plus a CLI; heartbeat interval target `10s`, stale threshold `60s`, Compose healthcheck interval `15s`, timeout `5s`, retries `3`, start period `20s`. Web `/health` is independent of worker health. |
| PO-M6-4 | The authenticated HTMX issue/relink POST may return the raw token only once in a `no-store`, `hx-history="false"` fragment without changing the top-level GET URL. Non-HTMX/JS-off POST does not mutate and redirects `303` to a safe GET. `HX-Request` is not an authorization boundary. |
| PO-M6-5 | Bot reply is sent only after TX-A commit, outside every DB transaction. Failure never rolls back domain/cursor or enters poison accounting. Web status is canonical. Safe messages are available in Uzbek Latin and Russian and reveal no account/collision/token/update detail. Runtime dependencies are `httpx>=0.28.1,<0.29` (locked `0.28.1`, BSD-3-Clause) and local QR encoder `segno>=1.6.6,<2` (locked `1.6.6`, BSD, dependency-free), producing in-memory PNG only. |

## 4. IN Scope

- Shared fail-closed `direct | trusted_proxy` client-IP resolver integrated
  with login and Telegram token issuance.
- Narrow Telegram Bot API client (`getMe`, `getWebhookInfo`, `getUpdates`, and
  `sendMessage`) and credential-safe startup preflight.
- Dedicated long-polling worker with one PostgreSQL advisory-lock owner,
  persisted monotonic cursor, heartbeat/readiness, graceful shutdown, and
  explicit retry/fatal transport classification.
- TX-A domain/cursor atomic processing and TX-B durable poison bookkeeping,
  fifth-attempt quarantine, and post-commit reply boundary.
- Account-authenticated `/auth/telegram/*` status, one-time issue/relink
  reveal, HTMX status polling, QR of the same deep-link, and current-password
  protected unlink/relink.
- CSRF, no-store, security headers, XSS-safe fragments, redacted secrets and
  identifiers, and no raw update/token/chat/IP persistence or logging.
- Existing rate limits: 600-second token TTL; 900-second issuance window;
  3 user, 3 phone, and 20 IP attempts.
- Caller-owned request, worker TX-A, and worker TX-B transactions. Services and
  repositories never commit, fully roll back, or close caller sessions.
- One-shot migration service, same-image worker command, healthcheck CLI, and
  web operation independent of worker availability.
- CI preservation: one `dependency-sync` job, PostgreSQL, Alembic
  upgrade/current and exact head assertion, Ruff, M5 containment guard, and
  full pytest without real Telegram credentials/network.

## 5. OUT Scope

- OTP challenge/delivery/hash/replay, SMS, phone+OTP login, public
  registration, new-user creation, customer activation/status changes, and
  forgotten-password/account recovery.
- F.I.Sh., JSHSHIR, passport/document PII, object storage, oferta acceptance,
  `shop_customer`, owner application, or admin approval UI.
- Debt, payment, badal, overpayment, clawback, rating, eligibility, shop
  settings, generic notification/outbox, scheduler, or generic monitoring UI.
- Webhook/public Telegram callback, Redis/queue, WebSocket, SPA framework, or
  admin-assisted Telegram unlink.
- Shop-scoped authorization for `/auth/telegram/*`; no `require_shop_staff`,
  no `active_shop_id` requirement, no shop suspend gate for account routes.
- Real Telegram credential/network in automated tests or CI. A separately
  approved dev-bot acceptance is limited to M6.72.

## 6. Route And UX Contract

| Route family | Scope | Required behavior |
| --- | --- | --- |
| `GET /auth/telegram/status` | Account scoped | Returns current user's `LINKED` or `UNLINKED` state. No shop context. |
| `POST /auth/telegram/link-token` | Account scoped | Issues first-link token only when user is unlinked. CSRF required. Rate-limited. |
| `POST /auth/telegram/relink-token` | Account scoped | Requires current-password re-auth, then issues a one-time relink token. CSRF required and rate-limited. |
| `POST /auth/telegram/unlink` | Account scoped | Requires current-password re-auth, CSRF, and a fresh authenticated session before unlinking. |

All state-changing routes use PRG except the narrowly approved authenticated
HTMX one-time reveal response. Its top-level URL remains GET, history is
disabled, no push URL or browser storage is used, and the response is
`Cache-Control: no-store`. A non-HTMX/JS-off POST performs no mutation and
returns `303` to a safe GET. `HX-Request` is never trusted as a security
boundary.

## 7. Error And SQLSTATE Contract

Expected public error codes reuse existing stable codes where possible:
`UNAUTHORIZED`, `SESSION_EXPIRED`, `CSRF_FAILED`, `RATE_LIMITED`,
`TELEGRAM_ALREADY_LINKED`, `TELEGRAM_NOT_LINKED`,
`TELEGRAM_CHAT_ALREADY_LINKED`, `LINK_TOKEN_INVALID`, `FORBIDDEN`, and
`VALIDATION_ERROR`.

M6 transient infrastructure B-allowlist:

| SQLSTATE | Meaning | Use |
| --- | --- | --- |
| `40001` | serialization_failure | Transient, no cursor/ledger mutation. |
| `40P01` | deadlock_detected | Transient, no cursor/ledger mutation. |
| `55P03` | lock_not_available | Transient, no cursor/ledger mutation. |
| class `08` | connection exception | Transient infrastructure failure. |
| `57P01`, `57P02`, `57P03` | operator intervention | Transient infrastructure failure. |
| `53300` | too_many_connections | Transient resource failure. |
| `57014` | query_canceled | B only for known statement timeout; worker shutdown cancellation is control flow. |

`connection_invalidated` and pool-checkout timeout are B. Exception class alone
is insufficient. Unknown TX-A errors default to C; unknown TX-B failures are
fatal and never recurse into poison handling.

## 8. Migration And CI Contract

- M6 starts from Alembic head `a6b4c2d8e9f1`.
- Any M6 migration must be a single linear child of `a6b4c2d8e9f1` unless a
  later PO/engineering decision says otherwise.
- Compose runs migrations in one one-shot service after database health and
  before both web and worker. Worker uses the same image and a distinct command.
- Web starts without `TELEGRAM_BOT_TOKEN`; worker fails closed when it is
  missing/empty. Worker replica count is one, restart policy is
  `unless-stopped`, and stop grace is `45s`.
- CI keeps `dependency-sync` as the single job.
- CI keeps `uv sync --dev --frozen`, `uv run alembic upgrade head`,
  `uv run alembic current`, Ruff check/format, M5 containment guard, and full
  `uv run pytest -ra`.
- The CI hardcoded head assertion is updated minimally from `a6b4c2d8e9f1` to
  the actual M6 migration head only when that migration exists.

## 9. Green Gate

M6 documentation gate is closed when:

- `docs/m6_scope_contract.md`, `docs/m6_decisions.md`,
  `docs/m6_repository_map.md`, and `docs/m6_known_limitations.md` exist.
- Each scope, decision, baseline, and repository primitive has a concrete
  source or an explicit `TOPILMADI` minimal solution.
- No product code, dependency, migration, Compose, Dockerfile, or CI file is
  changed by M6.05.

## Appendix A: Operational Persistence Schema

This appendix is the approved M6.19 schema contract. It uses two Telegram-only
operational tables and does not create a generic job, outbox, notification, or
audit subsystem.

### A.1 `telegram_polling_state`

Singleton key strategy: exactly one row with `id = 1`. The migration creates
the empty table; `load_or_create` inserts the singleton deterministically.
Absence and first creation both mean `next_offset = 0`, so startup never drops
pending updates.

| Column | PostgreSQL type | Null/default | Contract |
| --- | --- | --- | --- |
| `id` | `SMALLINT` | not null, PK | Fixed singleton value `1`; CHECK `id = 1`. |
| `next_offset` | `BIGINT` | not null, default `0` | CHECK `next_offset >= 0`; repository locks the row and rejects regression. |
| `heartbeat_at` | `TIMESTAMPTZ` | nullable | Last worker liveness write; independent of cursor movement. |
| `ready_at` | `TIMESTAMPTZ` | nullable | Set only after startup preflight/state initialization; null means not ready. |
| `updated_at` | `TIMESTAMPTZ` | not null, `CURRENT_TIMESTAMP` | Last repository mutation time. |

Named constraints:

- `pk_telegram_polling_state`;
- `ck_telegram_polling_state_singleton`;
- `ck_telegram_polling_state_next_offset_nonnegative`;
- `ck_telegram_polling_state_ready_requires_heartbeat`;
- `ck_telegram_polling_state_heartbeat_not_before_ready`.

Readiness invariant: `ready_at` requires `heartbeat_at`; when both exist,
`heartbeat_at >= ready_at`. Marking not-ready clears `ready_at` but may keep a
fresh heartbeat for diagnostics. Health is ready only when `ready_at` exists
and `heartbeat_at` is no older than the approved `60s` threshold.

### A.2 `telegram_update_failures`

One row exists per failed Telegram `update_id`. There is no FK because no user,
chat, token, or domain identity is persisted.

| Column | PostgreSQL type | Null/default | Contract |
| --- | --- | --- | --- |
| `update_id` | `BIGINT` | not null, PK | Telegram operational sequence id; CHECK `update_id >= 0`. |
| `attempt_count` | `SMALLINT` | not null | CHECK `1 <= attempt_count <= 5`. |
| `failure_code` | `VARCHAR(64)` | not null | Sanitized stable uppercase code; CHECK `^[A-Z][A-Z0-9_]{0,63}$`. |
| `first_failed_at` | `TIMESTAMPTZ` | not null | First durable C-failure time. |
| `last_failed_at` | `TIMESTAMPTZ` | not null | Latest durable C-failure time; not before first. |
| `quarantined_at` | `TIMESTAMPTZ` | nullable | Null for attempts 1–4; non-null exactly at attempt 5. |

Named constraints/indexes:

- `pk_telegram_update_failures`;
- `ck_telegram_update_failures_update_id_nonnegative`;
- `ck_telegram_update_failures_attempt_count`;
- `ck_telegram_update_failures_code_format`;
- `ck_telegram_update_failures_time_order`;
- `ck_telegram_update_failures_quarantine_state`;
- `ck_telegram_update_failures_quarantine_time`;
- non-unique `ix_telegram_update_failures_quarantined_at`.

Atomic protocol:

- C-failure uses PostgreSQL UPSERT to increment without lost updates.
- Attempts 1–4 do not advance the cursor.
- Attempt 5 sets `quarantined_at` and advances `next_offset` to
  `update_id + 1` in the same caller-owned fresh TX-B.
- Cursor failure rolls back both quarantine and attempt increment.
- A later successful retry deletes a non-quarantined row in TX-A.
- Ordinary success cleanup never deletes a quarantined row.

Retention: non-quarantined rows are deleted on success. Quarantined rows are
retained without automatic deletion during M6 for operator investigation; M6
adds no scheduler or generic retention job.

Forbidden columns/data in both tables: raw update/message/JSON, token or token
hash, Telegram chat/user id, Nasiya user/shop/customer id, phone, client IP,
bot credential, username, message text, exception text/traceback, HTTP body,
SQL text/parameters, and arbitrary metadata.
