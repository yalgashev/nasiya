# M6 Decisions

Status: PO-M6 DEFAULT DECISIONS CLOSED
Date: 2026-07-27
Approval source: user approved `defaul qarorlar bilan yopilsin` in chat.

This file separates inherited M4/M5 decisions from new PO-M6 decisions. It is
the dependency/license/maintenance decision record for the current M6 scope.

## 1. Inherited M4/M5 Decisions

| ID | Decision | Source evidence |
| --- | --- | --- |
| M4-I01 | Telegram link token issuance, consume, status, unlink, relink are pure domain/service concerns with caller-owned DB session. | `app/telegram/service.py:151`, `app/telegram/service.py:170`, `app/telegram/service.py:272`, `app/telegram/service.py:347`, `app/telegram/service.py:357` |
| M4-I02 | Raw Telegram token, start link, client IP, and chat identity use redacted repr/str. | `app/telegram/token.py:21`, `app/telegram/token.py:43`, `app/telegram/client_ip.py:6`, `app/telegram/inbound.py:6` |
| M4-I03 | Real Telegram Bot API, webhook, polling worker, QR, OTP, and customer activation are not in the M4 runtime baseline. | `README.md:216`, `README.md:235`, `tests/test_telegram_scope_regression.py:216` |
| M5-I01 | M5 is remote green and closed at SHA `c6812d456602a3c6ab1d1bde2fa2ab4967b212df`; parent Alembic head is `a6b4c2d8e9f1`. | `m5-result.md:3`, `m5-result.md:10`, `m5-result.md:15` |
| M5-I02 | Implementation evidence is `docs/m5_final_report.md`; remote status evidence is `m5-result.md`. | `m5-result.md:18` |
| M5-I03 | Request DB dependency owns request commit/rollback/close; service code does not. | `app/db.py:22` |
| M5-I04 | `sessions.active_shop_id` and shop resolver are shop-context primitives only. | `app/auth/models.py:57`, `app/shop/context.py:25`, `app/shop/dependencies.py:42` |
| M5-I05 | CI is one `dependency-sync` job with PostgreSQL, Alembic, Ruff, M5 containment guard, and full pytest. | `.github/workflows/ci.yml:1`, `.github/workflows/ci.yml:12`, `.github/workflows/ci.yml:35`, `.github/workflows/ci.yml:65`, `.github/workflows/ci.yml:81`, `.github/workflows/ci.yml:84` |

## 2. New PO-M6 Decisions

| ID | Decision | Implementation rule |
| --- | --- | --- |
| PO-M6-1 | `/auth/telegram/*` is account-scoped. | Use `get_current_session_context`, `require_user`, CSRF, no-store, and `get_database_session`. Do not use `require_shop_staff`, `resolve_current_shop`, `active_shop_id`, shop membership, or shop status gates. |
| PO-M6-2 | A/B/C policy is fixed. | A: account web issue/reveal/status/unlink. B: domain consume/relink/unlink mutation. C: future bot reply/delivery. A and B are current M6 implementation categories; C stays contract-only until transport is approved. |
| PO-M6-3 | Exact limits/timeouts are fixed. | Telegram attempt window `900s`; attempts: user `3`, phone `3`, IP `20`. DB test lock timeout `5000ms`, statement timeout `10000ms`. Compose db health interval `10s`, timeout `5s`, retries `5`, start_period `10s`. CI Postgres health interval `10s`, timeout `5s`, retries `5`. |
| PO-M6-4 | Bot reply is post-commit and private. | If a future worker sends a bot message, it sends only after DB commit. Rollback means no success reply. Reply text is Uzbek by default and never includes token, token hash, phone, user id, shop data, debt/customer data, or raw update payload. |
| PO-M6-5 | Dependency default is no new runtime package in current M6. | Runtime HTTP client is not added. `httpx` is the only approved future HTTP client package if worker transport is approved. QR encoder and QR image format are not approved in current M6; current reveal is a plain Telegram HTTPS start link. |

## 3. A/B/C Policy Detail

| Policy | Owner | Allowed work | Disallowed work |
| --- | --- | --- | --- |
| A | Account web route | Authenticated current user status, issue link token, issue relink token, unlink, one-time HTMX reveal. | Shop context, real Bot API, QR image, current-password re-auth, raw IDs in HTML. |
| B | Domain service | Token consume, relink, unlink, expected collision recovery, event append, token invalidation. | Commit/rollback/close, HTTP calls, Telegram update parsing, raw token persistence. |
| C | Future delivery | Post-commit generic Uzbek bot reply after future transport approval. | Pre-commit reply, reply with PII, CI real Telegram network, worker without fail-closed token policy. |

## 4. SQLSTATE Allowlist

| SQLSTATE | Name | Decision |
| --- | --- | --- |
| `23505` | unique_violation | Expected collision/race category. |
| `23503` | foreign_key_violation | Constraint test category only. |
| `23514` | check_violation | Constraint test category only. |
| `40001` | serialization_failure | Safe retry or fail-closed race category. |
| `40P01` | deadlock_detected | Safe retry or fail-closed race category. |
| `55P03` | lock_not_available | Safe retry or fail-closed lock category. |
| `57014` | query_canceled | Test statement-timeout category. |

All other SQLSTATE values are unapproved for public branching.

## 5. Dependency License And Maintenance

No new runtime dependency is approved for current M6, so no new package license
or maintenance review is required for M6.05.

Future dependency rule:

- HTTP client: `httpx` only, because it already exists as a dev dependency for
  Starlette/FastAPI TestClient support. Moving it to runtime requires a lockfile
  change and this file must record the license/maintenance review in the same
  change.
- QR encoder: no encoder is approved. Any future QR work must first name the
  package, output format, license, maintenance status, and whether it requires
  image libraries such as Pillow.

## 6. CI And Deployment Decisions

- Keep `.github/workflows/ci.yml` single-job structure.
- Keep PostgreSQL service and healthcheck.
- Keep M5 containment guard.
- Keep full pytest.
- Keep Alembic head assertion, but update its expected revision only after the
  M6 migration exists.
- Do not add Telegram credentials or real network to CI.
- Do not add worker service, restart policy, stop grace period, or worker
  healthcheck until PO-M6 explicitly approves real transport.
