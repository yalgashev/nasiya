# Nasiya M7 Repository Map

Status: repository-aware map for M7 implementation.
Sources: M7.01 baseline audit, M7.02 contract reconciliation, M7.03 primitive
map, and M7.04 feasibility audit.

## M7.02 Contract Reconciliation

| Contract / decision | Authoritative source | Inherited or M7-new | Repository evidence | M7 impact | Finding |
|---|---|---|---|---|---|
| Caller-owned transaction | M4/M6 contracts; M7 freeze section 8 | Inherited | `app/db.py:22`, `app/telegram/service.py:151`, `app/telegram/service.py:357` | OTP repositories/services use caller transaction only. | OK |
| External send outside DB transaction | M6 PO-M6-5; M7 freeze sections 8 and 10.2 | Inherited | `app/telegram/update_processing.py:128`, `app/telegram/update_processing.py:269`, `app/telegram/bot_reply.py:73` | Dispatcher uses TX-D1, send, TX-D2. | OK |
| M6 inbound worker role unchanged | M6 scope; M7 PO-M7-12 | Inherited | `app/telegram/worker.py:125`, `app/telegram/worker.py:268` | Add separate `otp-dispatcher`; do not modify getUpdates role. | OK |
| Server-side session and CSRF rotation | TT 8; M7 PO-M7-15/21 | Inherited | `app/auth/sessions.py:98`, `app/auth/sessions.py:132`, `app/auth/csrf.py:34`, `app/auth/router.py:120` | OTP GET/POST reuse anonymous session and CSRF; success rotates. | OK |
| Trusted client-IP resolver | M4-I03/M6; M7 scope 4.1 | Inherited | `app/request_client_ip.py:19`, `app/settings.py:15`, `app/settings.py:118` | OTP limits use resolver result only. | OK |
| `active_shop_id` isolation | M5/M6; M7 PO-M7-1/15 | Inherited | `app/auth/sessions.py:208`, `app/shop/dependencies.py:1`, `tests/test_shop_containment_guard.py:50` | OTP login does not select shop or require staff context. | OK |
| No raw secret/identifier leakage | TT 8; M6; M7 scope | Inherited and M7-new | `app/auth/sessions.py:24`, `app/auth/csrf.py:12`, `app/telegram/bot_api.py:82`, `app/telegram/bot_api.py:147` | OTP values need redacted value objects and leakage tests. | OK |
| Single-job CI and containment | M6 decisions; M7 validation | Inherited | `.github/workflows/ci.yml:12`, `.github/workflows/ci.yml:65`, `.github/workflows/ci.yml:76`, `.github/workflows/ci.yml:81`, `.github/workflows/ci.yml:84` | Update exact Alembic head only after M7 migration; preserve job shape. | OK |
| PO-M7 21/21 decisions | M7 freeze section 6 | M7-new | `docs/m7_decisions.md` | Implementation follows frozen decisions only. | OK |
| Four-table boundary | M7 freeze section 9 | M7-new | `alembic/versions/e7f8a9b0c1d2_create_m7_otp_persistence_tables.py` creates `otp_challenges`, `otp_dispatches`, `otp_challenge_events`, `otp_dispatcher_state` | Exactly four M7 tables added on M6 parent. | OK |
| Durable narrow dispatcher | M7 PO-M7-12/13 | M7-new | `compose.yaml:45`, `app/telegram/worker.py:125`, `app/telegram/worker_lock.py:8` patterns | Add OTP-specific command, state, lock, provider; no generic queue. | OK |
| Sync web send superseded | M7 freeze superseded material and PO-M7-12 | M7-new | `app/auth/router.py:357` current web only issues link token; no OTP route yet | Web creates dispatch only; no Telegram network in request. | OK |
| Same-code resend superseded | M7 PO-M7-7/13 | M7-new | No current OTP code exists | New-code creates new challenge; stale prepared no resend. | OK |

Unresolved contradiction: none.

## M7.03 Primitive Map

### Auth / Session

| Item | File / symbol | Status |
|---|---|---|
| Anonymous server-side session creation | `app/auth/sessions.py:98` `create_anonymous_session`; `app/auth/router.py:672` `_get_or_create_anonymous_session` | REUSE |
| Login GET CSRF | `app/auth/router.py:82` `login_page`; `app/auth/template_context.py:1` `with_csrf_context`; `app/auth/csrf.py:30` `get_csrf_token` | REUSE |
| Session rotation and CSRF rotation | `app/auth/sessions.py:132` `rotate_session`; `app/auth/sessions.py:81` token generation; `app/auth/csrf.py:30` token derivation from new secret | REUSE |
| Active user eligibility | `app/auth/sessions.py:170` active session user check; `app/auth/service.py:1` password auth service | REUSE |
| Safe relative next | `app/auth/router.py:220` uses `_get_safe_redirect_target`; helper below same file | REUSE |
| Cookie helpers | `app/auth/cookies.py:13` `set_session_cookie`; `app/auth/cookies.py:29` `delete_session_cookie` | REUSE |
| Security/no-store helpers | `app/security_headers.py:20`, `app/security_headers.py:29`, `app/security_headers.py:53`, `app/security_headers.py:62` | REUSE |

### Phone, Rate Limit, IP

| Item | File / symbol | Status |
|---|---|---|
| Canonical phone normalizer | `app/auth/phone.py:18` `normalize_uzbekistan_phone` | REUSE |
| Arbitrary scoped HMAC limiter | `app/auth/rate_limit.py:32` `AuthRateLimiter`; `app/auth/rate_limit.py:77` `check`; `app/auth/rate_limit.py:114` `record_failure`; `app/auth/rate_limit.py:183` `hash_rate_limit_key` | REUSE |
| Unknown phone path | `app/auth/login_rate_limit.py:86` normalizer returns no phone bucket for invalid login; M7 unknown-existing path needs explicit phone/IP limit | MINIMAL NEW PRIMITIVE |
| Trusted client-IP resolver | `app/request_client_ip.py:19` `resolve_client_ip`; `app/settings.py:15` `ClientIpMode` | REUSE |
| Raw key digest storage | `app/auth/models.py` `AuthRateLimit.key_hash`; `alembic/versions/6f8e2c0b9d71_create_auth_rate_limits_table.py:1` | REUSE |

### Telegram

| Item | File / symbol | Status |
|---|---|---|
| Telegram link model, ID, generation | `app/telegram/models.py:25` `TelegramLink`; `id` at `app/telegram/models.py:49`; `linked_at` at `app/telegram/models.py:60`; active-state rule at `app/telegram/models.py:29` | REUSE |
| Active link lookup | `app/telegram/repository.py:38` `has_active_telegram_link`; `app/telegram/repository.py:51` `get_telegram_link_by_user`; `app/telegram/repository.py:59` lock | REUSE |
| Unlink caller-owned service | `app/telegram/service.py:357` `unlink`; mutation at `app/telegram/repository.py:142` | REUSE with M7 invalidation hook |
| Successful relink service boundary | `app/telegram/service.py:272` `consume_start_token`; `app/telegram/repository.py:119` `relink_verified_private_chat` | REUSE with M7 invalidation hook |
| Bot API send_message | `app/telegram/bot_api.py:231` `TelegramBotApiClient.send_message` | REUSE behind provider |
| Transport injection | `app/telegram/bot_api.py:294` `create_telegram_http_client`; `app/telegram/worker.py:150` `transport` argument | REUSE |
| Timeout/error classes | `app/telegram/bot_api.py:15`, `app/telegram/bot_api.py:45`, `app/telegram/bot_api.py:348` | REUSE; send-specific timeout is missing |
| M6 worker entrypoint | `app/telegram/worker.py:125` parser; `compose.yaml:45` service | REUSE pattern only |
| Worker advisory lock | `app/telegram/worker_lock.py:8` key; `app/telegram/worker_lock.py:59` acquire function | REUSE pattern only |
| Worker heartbeat/health | `app/telegram/worker.py:314`, `app/telegram/worker.py:334`, `app/telegram/polling_repository.py:87` | REUSE pattern only |
| Settings SecretStr/redaction | `app/settings.py:6`, `app/settings.py:25`, `app/settings.py:100`, `app/settings.py:188` | REUSE |

### Transactions / Tests

| Item | File / symbol | Status |
|---|---|---|
| Request transaction owner | `app/db.py:22` `create_database_session_dependency` | REUSE |
| Non-request DB session/CLI pattern | `app/cli.py:120`, `app/telegram/worker.py:377`, `app/telegram/update_processing.py:128` | REUSE |
| Real PostgreSQL fixtures | `tests/conftest.py:15`, `tests/postgresql.py:25`, `tests/test_postgresql_guards.py:19` | REUSE |
| FOR UPDATE patterns | `app/auth/rate_limit.py:92`, `app/telegram/repository.py:59`, `app/telegram/polling_repository.py:48` | REUSE |
| Savepoint/concurrency patterns | `app/telegram/repository.py:281`, `app/telegram/service.py:451`, `tests/test_shop_service_concurrency.py:413` | REUSE |
| Alembic cleanup/head walk | `tests/postgresql.py:40` `get_alembic_head`; CI migration/current at `.github/workflows/ci.yml:65`; M7 tests in `tests/test_otp_migration.py` | REUSE; M7 head is `e7f8a9b0c1d2` |
| M5/M6 containment guards | `tests/test_shop_containment_guard.py:50`, `tests/test_telegram_scope_regression.py:216` | REUSE and update for M7 |
| Current CI job/head assertion/Ruff/full pytest | `.github/workflows/ci.yml:12`, `.github/workflows/ci.yml:71`, `.github/workflows/ci.yml:76`, `.github/workflows/ci.yml:84` | REUSE |

### Deployment

| Item | File / symbol | Status |
|---|---|---|
| Compose migration service | `compose.yaml:18` `migrate` | REUSE |
| Same-image separate-command pattern | `compose.yaml:29` web, `compose.yaml:45` telegram-worker, `Dockerfile:32` runtime image | REUSE |
| Worker healthcheck and stop grace | `compose.yaml:61`, `compose.yaml:62`, `compose.yaml:63` | REUSE pattern for OTP dispatcher |
| OTP dispatcher service | Not present in current compose | MINIMAL NEW PRIMITIVE |

## M7.04 Feasibility Audit

| Requirement | Result | Evidence / minimal solution |
|---|---|---|
| Crypto without new dependency | REUSE | `pyproject.toml:7` has no crypto dependency need; stdlib `secrets`, `hmac`, `hashlib` already used in `app/auth/sessions.py:1` and `app/auth/csrf.py:1`. |
| `OTP_HMAC_KEY` SecretStr/settings boundary | MINIMAL NEW PRIMITIVE | Add `otp_hmac_key: SecretStr | None`, validation, redaction, and OTP require helper in `app/settings.py`. |
| Web startup without OTP secret or bot token | REUSE plus MINIMAL NEW PRIMITIVE | Web currently has no bot token env in `compose.yaml:33`; M7 keeps web bot-token-free and makes OTP routes fail closed/degraded without OTP key. |
| Dispatcher secret wiring | MINIMAL NEW PRIMITIVE | Add `otp-dispatcher` compose service with `OTP_HMAC_KEY`, `TELEGRAM_BOT_USERNAME`, and `TELEGRAM_BOT_TOKEN`; reuse worker credential helper pattern. |
| Send-specific `<=5s` timeout | TOPILMADI / MINIMAL NEW PRIMITIVE | `app/telegram/bot_api.py:257` uses shared 35s read timeout. Add per-call timeout support for OTP provider without changing long-poll timeout. |
| Separate command/process one replica | MINIMAL NEW PRIMITIVE | Reuse `app/telegram/worker.py:125` parser shape and `compose.yaml:45` service shape for `app.otp.dispatcher`. |
| Single-owner guard | MINIMAL NEW PRIMITIVE | Reuse advisory-lock pattern from `app/telegram/worker_lock.py:8`, with a distinct OTP lock key. |
| Heartbeat healthcheck CLI | MINIMAL NEW PRIMITIVE | Reuse `app/telegram/worker.py:314` and `app/telegram/polling_repository.py:87` pattern with `otp_dispatcher_state`. |
| Four new tables migration | REUSE feasible | Alembic parent is `d4e5f6a7b8c9`; add one M7 revision. |
| Link-change challenge invalidation | MINIMAL NEW PRIMITIVE | Hook into `app/telegram/service.py:357` unlink and successful relink path in `app/telegram/service.py:272`. |
| Raw OTP absent from DB/message/outbox | MINIMAL NEW PRIMITIVE | Dispatcher generates code in memory in TX-D1, stores MAC only, sends after commit, records sanitized result in TX-D2. |
| Stale `PREPARED` recovery | MINIMAL NEW PRIMITIVE | Add dispatch repository transition `PREPARED -> UNKNOWN` by configured stale threshold. |
| No automatic same-code retry | MINIMAL NEW PRIMITIVE | Claim only `PENDING` rows; stale prepared becomes `UNKNOWN`; user new-code creates new challenge. |
| CI fake transport without real credentials | REUSE plus MINIMAL NEW PRIMITIVE | Current CI has no Telegram credentials in `.github/workflows/ci.yml:14`; add fake provider/transport tests only. |

Feasibility result: raw OTP persistence is not required, a generic queue is not
required, and no blocker was found.

## Implementation Notes For M7.07+

- `app/otp` now contains typed crypto/contracts plus persistence models and
  repository primitives. Dispatcher, provider, and routes are still later M7
  steps.
- M7 models are imported in `alembic/env.py`; M7 migration head is
  `e7f8a9b0c1d2` with parent `d4e5f6a7b8c9`.
- `tests/postgresql.py` cleanup allowlist includes OTP tables in child-first
  order.
- `.github/workflows/ci.yml` keeps the one-job shape and now verifies the M7
  Alembic head.
- Extend M6 scope regression tests so M7 allows only `app.otp`,
  `/auth/otp/*`, and the four approved tables, not generic notification,
  outbox, scheduler, webhook, Redis, SMS, or recovery.

## M7.14-M7.20 Persistence Map

| Primitive | File / symbol | Status |
|---|---|---|
| Exact four-table appendix | `docs/m7_scope_contract.md` `Exact Schema Appendix` | IMPLEMENTED |
| ORM metadata | `app/otp/models.py` `OtpChallenge`, `OtpDispatch`, `OtpChallengeEvent`, `OtpDispatcherState` | IMPLEMENTED |
| Linear Alembic revision | `alembic/versions/e7f8a9b0c1d2_create_m7_otp_persistence_tables.py` | IMPLEMENTED |
| Challenge repository | `app/otp/repository.py` `load_outstanding_*`, `create_pending_challenge`, `activate_challenge`, terminal transitions, attempts | IMPLEMENTED |
| Dispatch repository | `app/otp/repository.py` `create_pending_dispatch`, `claim_next_pending_dispatch_for_update`, prepare/result/stale transitions | IMPLEMENTED |
| Append-only events | `app/otp/repository.py` `append_challenge_event` | IMPLEMENTED |
| Retention purge | `app/otp/repository.py` `purge_terminal_otp_records` | IMPLEMENTED |
| Dispatcher singleton state | `app/otp/repository.py` `get_or_create_dispatcher_state_for_update`, `mark_dispatcher_heartbeat` | IMPLEMENTED |
| Persistence tests | `tests/test_otp_model_metadata.py`, `tests/test_otp_migration.py`, `tests/test_otp_repository_postgresql.py` | IMPLEMENTED |
