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
| PO-M6-2 | Poison update A/B/C policy is fixed. | A expected terminal: cursor advance/no retry. B explicit transient allowlist: no cursor advance/no poison count. C unknown TX-A handler failure: durable count; attempt 5 quarantines and advances cursor atomically in TX-B. Successful retry deletes a non-quarantined row. Unknown TX-B failure is fatal. |
| PO-M6-3 | Worker lifecycle/deployment values are fixed. | Long poll `25s`; HTTP read timeout `35s`; advisory lock deadline `60s`; stop grace `45s`; replica `1`; restart `unless-stopped`. Migrate one-shot before web/worker. PostgreSQL heartbeat target `10s`, stale `60s`; healthcheck interval `15s`, timeout `5s`, retries `3`, start period `20s`. Web `/health` stays worker-independent. |
| PO-M6-4 | One-time reveal is a narrow PRG exception. | Authenticated HTMX POST returns raw deep-link once in a `no-store`, `hx-history=false` fragment; no push URL/storage. Non-HTMX/JS-off POST does not mutate and returns `303` to safe GET. `HX-Request` is not a security boundary. |
| PO-M6-5 | Bot reply, language, privacy, and dependencies are fixed. | Reply only after TX-A commit and outside DB transactions; failure does not roll back or poison-count. Web status is canonical. Uzbek Latin and Russian safe text reveals no account/collision detail. Approve runtime `httpx>=0.28.1,<0.29` and local QR `segno>=1.6.6,<2`, in-memory PNG. |

## 3. A/B/C Policy Detail

| Policy | Meaning | Cursor/ledger behavior | Examples |
| --- | --- | --- | --- |
| A | Expected terminal update/domain outcome. | TX-A advances cursor; no retry or poison count. | Irrelevant/non-private/malformed start, invalid/expired/replayed token, idempotent same chat, collision/no takeover. |
| B | Explicit transient infrastructure failure. | Roll back; cursor and failure ledger unchanged; retry with bounded policy. | Approved SQLSTATEs, connection invalidation, pool timeout, transport 429/5xx/network timeout. |
| C | Unknown or unexpected TX-A handler failure. | TX-A rolls back; fresh TX-B increments durable count. Attempt 5 writes quarantine and advances cursor atomically. | Unknown application/handler defect with sanitized stable failure code. |

## 4. SQLSTATE Allowlist

| SQLSTATE | Name | Decision |
| --- | --- | --- |
| `40001` | serialization_failure | B transient. |
| `40P01` | deadlock_detected | B transient. |
| `55P03` | lock_not_available | B transient. |
| class `08` | connection exception | B transient. |
| `57P01`, `57P02`, `57P03` | operator intervention | B transient. |
| `53300` | too_many_connections | B transient. |
| `57014` | query_canceled | B only for known statement timeout; controlled shutdown is not A/B/C. |

`connection_invalidated` and pool checkout timeout are also B. Exception class
alone is not enough. Unknown TX-A failure defaults to C; unknown TX-B failure
is fatal.

## 5. Dependency License And Maintenance

Approved production dependencies:

- HTTP client: `httpx>=0.28.1,<0.29`; exact current lock `0.28.1`. It is
  BSD-3-Clause, maintained by Encode, supports async clients and injected
  transports, and is already locked for dev TestClient usage. M6.12 promotes
  it to a direct runtime dependency; the M5 deprecation warning is not the
  approval evidence.
- QR encoder: `segno>=1.6.6,<2`; approved lock target `1.6.6`. It is
  production/stable, BSD licensed, pure Python, dependency-free, and supports
  local in-memory PNG generation. M6 uses PNG only, with no external QR API,
  temp file, Pillow, SVG active content, or JavaScript encoder.

## 6. CI And Deployment Decisions

- Keep `.github/workflows/ci.yml` single-job structure.
- Keep PostgreSQL service and healthcheck.
- Keep M5 containment guard.
- Keep full pytest.
- Keep Alembic head assertion, but update its expected revision only after the
  M6 migration exists.
- Do not add Telegram credentials or real network to CI.
- `getUpdates` long poll: `25s`; HTTP read timeout: `35s`.
- PostgreSQL advisory lock acquisition deadline: `60s`.
- Worker replica: `1`; restart: `unless-stopped`; stop grace: `45s`.
- Migration ownership: one-shot Compose service after DB health; web and worker
  start only after successful migration.
- Worker heartbeat target: `10s`; stale threshold: `60s`. Healthcheck CLI
  interval `15s`, timeout `5s`, retries `3`, start period `20s`.
- Web `/health` remains independent of worker health and web starts without the
  bot token. Worker fails closed when the token is absent or empty.
