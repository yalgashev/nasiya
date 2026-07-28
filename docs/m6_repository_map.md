# M6 Repository Map

Status: M6.03-M6.04 READ-ONLY AUDIT CAPTURED
Date: 2026-07-27

This file maps the real repository primitives M6 must use. Paths and symbols
are evidence from read-only audits; `TOPILMADI` entries have approved minimal
solutions from `docs/m6_decisions.md`.

## 1. Baseline Evidence

| Item | Evidence |
| --- | --- |
| M5 closure SHA | `m5-result.md:10` `c6812d456602a3c6ab1d1bde2fa2ab4967b212df` |
| Remote closeout | `m5-result.md:11` run `30281678432`, `m5-result.md:12` workflow `CI`, `m5-result.md:13` job `dependency-sync`, `m5-result.md:14` success |
| Parent Alembic head | `m5-result.md:15` `a6b4c2d8e9f1`; `alembic/versions/a6b4c2d8e9f1_create_m5_shop_tables.py:17` |
| M5 implementation report | `docs/m5_final_report.md` |
| M5 remote-status report | `m5-result.md` |
| M5 local baseline | `m5-result.md:16` `1113 passed, 0 skip, 0 xfail, 1 existing Starlette/httpx warning` |

## 2. Account Auth And Request Primitives

| Need | File/symbol |
| --- | --- |
| Current authenticated session | `app/auth/deps.py:113` `get_current_session_context` |
| Current user dependency | `app/auth/deps.py:152` `require_user` |
| Request DB dependency | `app/auth/deps.py:103` `get_database_session` |
| Request transaction owner | `app/db.py:22` `create_database_session_dependency` |
| App wiring | `app/main.py:34` `create_app`, `app/main.py:45` `application.state.get_database_session` |
| CSRF constants | `app/auth/deps.py:34` `CSRF_FORM_FIELD_NAME`, `app/auth/deps.py:35` `CSRF_HEADER_NAME` |
| CSRF validation | `app/auth/deps.py:164` `validate_csrf`; `app/auth/csrf.py:30` `get_csrf_token`; `app/auth/csrf.py:34` `verify_csrf_token` |
| HTMX CSRF fragment pattern | `app/auth/deps.py:219` HTMX branch; `app/auth/deps.py:298` `_render_csrf_fragment` |
| No-store helper | `app/security_headers.py:61` `mark_auth_response_no_store` |
| Security headers | `app/security_headers.py:21` `SECURITY_HEADERS`; `app/security_headers.py:29` middleware |

## 3. Telegram Domain Primitives

| Need | File/symbol |
| --- | --- |
| Client IP value | `app/telegram/client_ip.py:6` `ResolvedClientIp` |
| Raw link token | `app/telegram/token.py:21` `RawTelegramLinkToken` |
| Start link builder | `app/telegram/token.py:72` `build_telegram_start_link` |
| Link token create/hash | `app/telegram/token.py:61` `create_telegram_link_token`; `app/telegram/token.py:68` `hash_telegram_link_token` |
| Fake verified private adapter | `app/telegram/inbound.py:30` `FakeVerifiedPrivateTelegramAdapter` |
| Verified private chat identity | `app/telegram/inbound.py:6` `VerifiedPrivateTelegramChatIdentity` |
| Issue link token | `app/telegram/service.py:151` `issue_link_token` |
| Issue relink token | `app/telegram/service.py:170` `issue_relink_token` |
| Consume token | `app/telegram/service.py:272` `consume_start_token` |
| Link status | `app/telegram/service.py:347` `get_link_status` |
| Unlink | `app/telegram/service.py:357` `unlink` |
| Purge terminal tokens | `app/telegram/service.py:393` `purge_terminal_link_tokens` |
| Issued result exposes DB token row | `app/telegram/service.py:65` `IssuedTelegramLinkToken`; tests use `result.token.id` at `tests/test_telegram_issue_link_token_service.py:1643` |

### M6 Operational Integration

| Need | File/symbol |
| --- | --- |
| Minimal Bot API update envelope | `app/telegram/bot_api.py` `TelegramUpdateEnvelope`, `TelegramMessageEnvelope`, `TelegramBotApiClient.get_updates` |
| Private start parser | `app/telegram/update_parser.py` `parse_telegram_update`; exact private `/start <token>` grammar with redacted typed output |
| TX-A owner | `app/telegram/update_processing.py` `process_telegram_update_tx_a`; M4 consume/event, non-quarantined cleanup, and cursor in one caller-owned transaction |
| A outcome and reply intent map | `app/telegram/update_processing.py` `TelegramUpdateOutcomeCode`, `BotReplyIntent`, `_apply_terminal_update` |
| B classifier | `app/telegram/update_processing.py` `classify_telegram_tx_failure`; exact SQLSTATE/context policy from `docs/m6_decisions.md` |
| TX-B owner | `app/telegram/update_processing.py` `record_poison_failure_tx_b`; fresh transaction, stable failure code, attempt/quarantine/cursor protocol |
| Per-update worker bridge | `app/telegram/update_processing.py` `TelegramUpdateProcessor`; default processor selected by `app/telegram/worker.py` `run_worker` |
| Post-commit reply delivery | `app/telegram/bot_reply.py` `deliver_bot_reply_best_effort`; called by the default worker processor only after TX-A returns committed |
| Narrow localization catalog | `app/telegram/bot_reply.py` `render_bot_reply`; Uzbek Latin default and Russian `ru*` mapping, with one privacy-safe failure group |
| Account web route owner | `app/auth/router.py` `telegram_page`, `issue_telegram_link_token`, `issue_telegram_relink_token`, and `telegram_attempt_status`; request-owned transaction, auth/CSRF/no-store, no shop dependency |
| Attempt presentation | `app/telegram/web_presentation.py` `get_link_attempt_presentation`; owned UUID mapping to `WAITING`, `LINKED`, `SUPERSEDED`, `EXPIRED`, or `UNAVAILABLE` |
| Web localization | `app/telegram/web_presentation.py` `resolve_telegram_web_language` and `get_telegram_web_copy`; bounded Uzbek Latin/Russian catalog |
| One-time browser reveal | `app/templates/auth/telegram.html`, `app/static/vendor/htmx-2.0.4.min.js`, and router fragment renderers; local script, `hx-history=false`, no push URL/storage/CDN |

## 4. Shop Context Boundary

| Need | File/symbol | M6 rule |
| --- | --- | --- |
| `sessions.active_shop_id` model | `app/auth/models.py:57` `Session`; `app/auth/models.py:82` `active_shop_id` | Account Telegram routes do not read or require it. |
| Active shop setter/clearer | `app/auth/sessions.py:208`, `app/auth/sessions.py:218` | Not used by `/auth/telegram/*`. |
| Shop resolver | `app/shop/context.py:25` `resolve_current_shop` | Not used by `/auth/telegram/*`. |
| Shop staff dependency | `app/shop/dependencies.py:42` `require_shop_staff`; `app/shop/dependencies.py:67` `require_shop_owner` | Not used by `/auth/telegram/*`. |
| Existing `/auth/account` independence | `app/auth/router.py:193`; test `tests/test_auth_account_get.py:306` | Use as account-route pattern. |

## 5. Rate Limit And Policy

| Need | File/symbol/value |
| --- | --- |
| Generic rate limiter | `app/auth/rate_limit.py:32` `AuthRateLimiter` |
| Login policy example | `app/auth/login_rate_limit.py:31` `LoginRateLimitPolicy` |
| Telegram rate limit settings | `app/settings.py:25` to `app/settings.py:28` |
| Telegram default limits | `README.md:47` to `README.md:52`: 900 seconds, 3 user, 3 phone, 20 IP |
| Existing no-raw key storage | `app/auth/rate_limit.py:183` `hash_rate_limit_key` |

## 6. Database And Migration

| Need | File/symbol/value |
| --- | --- |
| Alembic URL selection | `alembic/env.py:27` `_get_database_url` uses `TEST_DATABASE_URL` or `DATABASE_URL` |
| Parent revision | `alembic/versions/a6b4c2d8e9f1_create_m5_shop_tables.py:17` |
| Parent down revision | `alembic/versions/a6b4c2d8e9f1_create_m5_shop_tables.py:18` |
| Real PostgreSQL guard | `tests/postgresql.py:23` `validate_test_database_url` |
| Test DB fixture | `tests/conftest.py:15` `test_database_url`; `tests/conftest.py:35` `m2_test_database` |
| Migration validation commands | `README.md:116` to `README.md:144` |

## 7. CI And Deployment

| Need | File/symbol/value |
| --- | --- |
| Workflow name | `.github/workflows/ci.yml:1` `CI` |
| Single job | `.github/workflows/ci.yml:12` `dependency-sync` |
| PostgreSQL service | `.github/workflows/ci.yml:35` |
| CI Postgres health values | `.github/workflows/ci.yml:44` to `.github/workflows/ci.yml:48`: interval `10s`, timeout `5s`, retries `5` |
| Dependency sync | `.github/workflows/ci.yml:62` `uv sync --dev --frozen` |
| Alembic migration | `.github/workflows/ci.yml:65` `uv run alembic upgrade head` |
| Current revision display | `.github/workflows/ci.yml:68` `uv run alembic current` |
| M5 head assertion | `.github/workflows/ci.yml:71` to `.github/workflows/ci.yml:74` |
| Ruff | `.github/workflows/ci.yml:76` |
| M5 containment guard | `.github/workflows/ci.yml:81` |
| Full pytest | `.github/workflows/ci.yml:84` |
| Docker exec-form web command | `Dockerfile:39` |
| Compose db health values | `compose.yaml:12` to `compose.yaml:17`: interval `10s`, timeout `5s`, retries `5`, start_period `10s` |
| Compose service condition | `compose.yaml:28` to `compose.yaml:30` |

## 8. Dependency Map

| Need | Evidence | M6 rule |
| --- | --- | --- |
| Runtime dependencies | `pyproject.toml:7`; `uv.lock:429` | M6.12 promotes approved `httpx>=0.28.1,<0.29`; M6.56 adds approved `segno>=1.6.6,<2`. |
| Dev `httpx` | `pyproject.toml:21`; `uv.lock:306` exact `0.28.1` | Existing TestClient/dev presence is not runtime approval by itself; `docs/m6_decisions.md` is approval. |
| Starlette/httpx warning | `docs/m5_final_report.md:127` | Existing warning, not application runtime client approval. |
| QR encoder | `TOPILMADI`; existing M4 containment test forbids unapproved `qrcode` | Add approved `segno` only at M6.56 and update the M4 containment expectation narrowly for M6 production scope. |
| Real Telegram credential | `.env.example` has an empty operator-supplied placeholder; `compose.yaml` passes it only to `telegram-worker`; CI has no bot token. | Web remains credential-free; deployed worker secret value is untracked; no real credential/network in CI. |

## 9. TOPILMADI Minimal Solutions

| Primitive | Audit result | Approved minimal solution |
| --- | --- | --- |
| Current-password re-auth | `TOPILMADI`; existing verifier is `app/auth/password_service.py:21` `verify_password` | Add a minimal current-user password verification dependency/service and session-freshness gate; no generic recovery framework. |
| Runtime external HTTP client | `TOPILMADI` | Promote approved locked `httpx 0.28.1` to runtime at M6.12; use a narrow injected async transport adapter. |
| QR encoder | `TOPILMADI` | Add approved `segno 1.6.6` at M6.56 and render in-memory PNG of the same one-time deep-link. |
| i18n framework | No generic framework was needed; implemented narrow immutable catalog in `app/telegram/bot_reply.py`. | Uzbek Latin and Russian bot replies only; no Babel/gettext subsystem. |
| HTMX polling | `app/static/vendor/htmx-2.0.4.min.js`; `app/auth/router.py` `telegram_attempt_status`; contract route `GET /auth/telegram/attempts/{attempt_id}/status` with a `3s` interval. | Account-owned status fragment polls only in `WAITING`, terminal responses retarget the reveal contents, and no WebSocket/SPA is used. |
| Worker service | Implemented in `app/telegram/worker.py`: `main`, `run_worker`, `run_polling_loop`, and `ShutdownController`; `compose.yaml` service `telegram-worker` reuses the web image. | Dedicated command, one replica, process-owned engine/client, per-operation sessions, signal-aware cleanup, and no web-process thread. |
| Worker healthcheck/restart policy | Implemented by `worker_health_is_fresh`, `run_healthcheck_command`, polling heartbeat task, and `compose.yaml` worker lifecycle values. | PostgreSQL readiness/heartbeat CLI; `unless-stopped`, stop grace `45s`, heartbeat `10s`, freshness `60s`. |
| Advisory lock | Implemented in `app/telegram/worker_lock.py`: `TELEGRAM_POLLER_ADVISORY_LOCK_KEY`, `TelegramPollingLock`, and `acquire_telegram_polling_lock`. | Stable non-secret 64-bit key, dedicated connection, bounded `pg_try_advisory_lock` for `60s` at `1s` intervals. |
| Explicit downgrade/revision-by-revision migration walk | Implemented in `tests/test_telegram_polling_migration.py`. | Real PostgreSQL exact M5 parent downgrade and M6 head re-upgrade. |

## 10. M6 Test Map

When implementation starts, tests should cover:

- trusted/direct client-IP settings, resolver, login and issuance integration;
- fake Bot API transport, preflight, exact timeout/error/backoff semantics;
- real-PostgreSQL cursor/heartbeat/failure ledger, advisory lock, TX-A/TX-B,
  quarantine, restart, ordering, and signal cleanup;
- account route auth/session/CSRF/no-store and independence from shop context;
- one-time HTMX reveal/polling, same-link QR, password-protected unlink/relink,
  and no raw token/identity in URL, logs, DB, or later GET;
- Compose migration ordering, worker health, web independence, and CI
  compatibility with the M5 containment guard and full pytest.
