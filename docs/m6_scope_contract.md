# M6 Scope Contract

Status: PO-M6 DEFAULT DECISIONS CLOSED
Date: 2026-07-27

This document is the M6 implementation contract after PO approval in chat:
`defaul qarorlar bilan yopilsin`. It supersedes unclear PRE-M6 options for
the current M6 Telegram-linking slice without changing TT text, product code,
dependencies, CI, or migrations.

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
| M4-I02 | `app/telegram/inbound.py` | Inbound identity is a typed verified private chat id; fake adapter is zero-network. | M6 tests use fake transport only; no CI Telegram credential or network. |
| M4-I03 | `app/telegram/client_ip.py` | Client IP is `ResolvedClientIp`, redacted and canonicalized. | M6 account routes must pass a resolved IP primitive without storing raw IP. |
| M4-I04 | `app/telegram/rate_limit.py` and settings | Telegram issuance rate limits are 900 seconds: 3 user attempts, 3 phone attempts, 20 IP attempts. | M6 reuses these exact attempt limits. |
| M5-I01 | `app/db.py` and `app/cli.py` | Request and non-request DB work have caller-owned transaction boundaries. | M6 services do not commit, rollback, or close sessions. |
| M5-I02 | `app/auth/models.py` | `sessions.active_shop_id` exists for shop context only. | `/auth/telegram/*` must not depend on selected shop state. |
| M5-I03 | `.github/workflows/ci.yml` | CI is one job, with Alembic migration, head assertion, Ruff, M5 containment guard, and full pytest. | M6 keeps the single-job shape and only updates the hardcoded head when a new migration exists. |

Earlier PRE-M6 debt/payment and shop-settings decisions remain recorded in
`docs/m6_change_requests.md`. They do not expand the current
`/auth/telegram/*` account-linking slice.

## 3. New PO-M6 Decisions

| ID | Decision |
| --- | --- |
| PO-M6-1 | Current M6 scope is account-scoped Telegram linking UI/foundation under `/auth/telegram/*`. It is independent of `active_shop_id`, shop membership, shop status, and `require_shop_staff`. |
| PO-M6-2 | A/B/C policy is adopted for account linking: A = account web token issue/reveal/status/unlink; B = domain consume/relink/unlink state transition; C = future bot reply/delivery. A and B can be implemented in M6; C is contract-only unless a later PO decision adds real transport. |
| PO-M6-3 | Exact concurrency/deployment values are fixed for M6 tests and docs: PostgreSQL `lock_timeout = '5000ms'`, `statement_timeout = '10000ms'`; Compose db healthcheck interval `10s`, timeout `5s`, retries `5`, start_period `10s`; CI Postgres health interval `10s`, timeout `5s`, retries `5`. |
| PO-M6-4 | Bot reply contract is post-commit, private, generic, and Uzbek by default. It must not include raw token, token hash, phone, user id, shop id/name, debt/customer data, or Telegram update payload. |
| PO-M6-5 | No new runtime HTTP client or QR encoder is added in current M6. If a future M6 worker needs HTTP, `httpx` is the only approved package and must be moved from dev to runtime with lock and license/maintenance evidence. QR encoder and image format are not approved in current M6; the approved current reveal is a plain HTTPS Telegram start link only. |

## 4. IN Scope

- Account-authenticated `/auth/telegram/*` route contract and future
  implementation map.
- Telegram link status, issue link token, issue relink token, unlink, and
  safe public error handling for the current authenticated user.
- One-time HTMX reveal for raw Telegram start link after successful issue or
  relink token creation.
- CSRF, no-store, security headers, XSS-safe fragments, and no token/PII leaks.
- Existing rate limit settings: 900-second window, 3 user attempts, 3 phone
  attempts, 20 IP attempts.
- Caller-owned transaction pattern; account routes use request-scoped DB
  dependency, while services keep commit/rollback/close outside.
- CI preservation plan: one job, PostgreSQL service, Alembic upgrade/current,
  head assertion, Ruff, M5 containment guard, full pytest.
- Documentation-only baseline and repository map in M6.05.

## 5. OUT Scope

- Product code, migration, dependency, Compose, Dockerfile, or CI edits in
  M6.05.
- Real Telegram Bot API calls, `TELEGRAM_BOT_TOKEN`, webhook, polling worker,
  scheduler, Redis/queue, external network CI, or production bot transport.
- QR image generation, QR encoder dependency, PNG/SVG QR output, or camera
  scanning assumptions.
- Debt/payment, badal, overpayment, clawback reversal implementation, customer
  activation, OTP, SMS, PWA/service worker, object storage, admin approval.
- Shop-scoped authorization for `/auth/telegram/*`; no `require_shop_staff`,
  no `active_shop_id` requirement, no shop suspend gate for account routes.
- Current-password re-auth for Telegram unlink; M6 uses current authenticated
  account session plus CSRF.

## 6. Route And UX Contract

| Route family | Scope | Required behavior |
| --- | --- | --- |
| `GET /auth/telegram/status` | Account scoped | Returns current user's `LINKED` or `UNLINKED` state. No shop context. |
| `POST /auth/telegram/link-token` | Account scoped | Issues first-link token only when user is unlinked. CSRF required. Rate-limited. |
| `POST /auth/telegram/relink-token` | Account scoped | Issues relink token only when user is linked. CSRF required. Rate-limited. |
| `POST /auth/telegram/unlink` | Account scoped | Unlinks current user's Telegram link. CSRF required. No password/current_password parameter. |

All state-changing routes use PRG except the HTMX one-time reveal response.
The HTMX exception is allowed because the raw token/start link is not stored
and cannot be safely reconstructed later. The reveal response must be
`Cache-Control: no-store` and must not include raw DB identifiers.

## 7. Error And SQLSTATE Contract

Expected public error codes reuse existing stable codes where possible:
`UNAUTHORIZED`, `SESSION_EXPIRED`, `CSRF_FAILED`, `RATE_LIMITED`,
`TELEGRAM_ALREADY_LINKED`, `TELEGRAM_NOT_LINKED`,
`TELEGRAM_CHAT_ALREADY_LINKED`, `LINK_TOKEN_INVALID`, `FORBIDDEN`, and
`VALIDATION_ERROR`.

M6 expected PostgreSQL SQLSTATE allowlist:

| SQLSTATE | Meaning | Use |
| --- | --- | --- |
| `23505` | unique_violation | Expected race/collision on one outstanding token or active chat uniqueness. |
| `23503` | foreign_key_violation | Expected only in migration/constraint tests. |
| `23514` | check_violation | Expected only in migration/constraint tests. |
| `40001` | serialization_failure | Safe retry/fail-closed race category. |
| `40P01` | deadlock_detected | Safe retry/fail-closed race category. |
| `55P03` | lock_not_available | Safe retry/fail-closed lock category. |
| `57014` | query_canceled | Statement timeout category for concurrency tests. |

Any other SQLSTATE is not an expected M6 business outcome and must become an
internal failure or test failure, not a new silent public branch.

## 8. Migration And CI Contract

- M6 starts from Alembic head `a6b4c2d8e9f1`.
- Any M6 migration must be a single linear child of `a6b4c2d8e9f1` unless a
  later PO/engineering decision says otherwise.
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
