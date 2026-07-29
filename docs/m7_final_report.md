# Nasiya M7 Final Report

Status: `M7 REMOTE GREEN — CLOSED`
Date: 2026-07-29

This report records the M7 implementation, real-device acceptance, M7.66 full
local technical validation, exact-SHA remote CI success, and formal milestone
closure.

## Baseline And Authority

- M6 implementation baseline:
  `54df18846663f9eb19ce21a131f796a5b3178bf5`; exact-SHA CI run
  `30383047949` succeeded.
- M6 docs-only closeout:
  `9bf0056fd010d7424f3b577b937ce5204476bcf8`; current-HEAD CI run
  `30411142510` succeeded.
- M6 status: `M6 REMOTE GREEN — CLOSED`; migration head
  `d4e5f6a7b8c9`; full suite `1373 passed` with no skip or xfail.
- Eight M7 checkpoint commits are descendants of both M6 baselines:
  `f2a7fac04061e4fa035f751ff85b35b91669445f`,
  `f70c81a79bf1300874bfc9d8eb14a7db7f0b2248`,
  `6c57da15f6eb01f526a2a86335c1186d20f1647b`,
  `87828b053815d48e1fecf6f0bb2699f590bee2b8`,
  `3d5456055569a07d1349cee66e8ac0789a7f5a3c`,
  `fe4c679d7dcde03aa9e52a722ea73df23e395565`, and
  `05deba9f4faaf8e87371525821f47e629b2874be`, followed by the immutable
  implementation baseline
  `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3`.
- The implementation baseline was pushed from a clean `main` with
  `HEAD == origin/main` and divergence `0 0`.
- GitHub Actions run `30453909901`, workflow/job `CI / dependency-sync`,
  concluded `success` for that exact implementation SHA.
- The docs-only closeout commit is intentionally not self-referenced in this
  report; its SHA is taken from Git history after commit creation.
- Scope and decision authority:
  `docs/m7_scope_contract.md`, `docs/m7_decisions.md`,
  `docs/m7_repository_map.md`, and `docs/m7_known_limitations.md`.
- The tracked TT file remains byte-identical to the M6 docs closeout version.

## Delivered Capability

- Eligibility is limited to an existing active user with an active Telegram
  link; the only purpose is `LOGIN`.
- The OTP is exactly six ASCII digits, generated with a cryptographically
  secure random source, including leading zeroes.
- Only an HMAC-SHA-256 code MAC is persisted. Verification uses constant-time
  comparison and a dedicated `OTP_HMAC_KEY`.
- Partial unique indexes enforce one outstanding challenge per user/purpose
  and per browser/purpose.
- Browser binding derives from the anonymous session and CSRF secret. Another
  browser or session cannot consume the code.
- Active lifetime is `180s`, with at most `5` failed verification attempts.
- New-code cooldown is `60s`. The issuance window is `900s`, with limits of
  `3` per phone, `3` per user, and `20` per IP.
- A newer request supersedes prior outstanding work. Unlink or successful
  relink invalidates challenges bound to the previous link generation.
- Successful OTP login rotates the authenticated session and CSRF state.
  Existing password login remains available.

## Schema

Migration `e7f8a9b0c1d2`, whose exact parent is `d4e5f6a7b8c9`, creates exactly
the four M7 tables approved by the scope freeze:

| Table | Purpose |
|---|---|
| `otp_challenges` | Challenge identity, binding, code MAC, attempts, and lifecycle |
| `otp_dispatches` | Durable send intent, claim, and provider outcome |
| `otp_challenge_events` | Sanitized, allowlisted lifecycle evidence |
| `otp_dispatcher_state` | Singleton readiness and heartbeat |

Challenge statuses are `PENDING_DISPATCH`, `ACTIVE`, `CONSUMED`,
`SUPERSEDED`, `EXPIRED`, `BURNED`, and `INVALIDATED`.

Dispatch statuses are `PENDING`, `PREPARED`, `SENT`, `FAILED`, `UNKNOWN`, and
`CANCELLED`.

Event actions are `ISSUED`, `DISPATCH_PREPARED`, `DISPATCH_RESULT`,
`VERIFY_FAILED`, `CONSUMED`, `SUPERSEDED`, `EXPIRED`, `BURNED`, and
`INVALIDATED_BY_LINK_CHANGE`.

Alembic reports one head and the local Docker database is current at
`e7f8a9b0c1d2`.

## Runtime And Process

- Web writes a challenge plus dispatch intent and performs no Telegram network
  call.
- A separate `python -m app.otp.dispatcher run` process handles pending
  dispatches.
- The M6 inbound `python -m app.telegram.worker run` process is unchanged.
- The bot token is absent from web. OTP-enabled web and the dispatcher share
  the dedicated OTP key; the dispatcher also receives the bot token and exact
  username.
- Dispatcher startup validates bot identity, exact username, inactive webhook,
  and singleton advisory-lock ownership. Health uses readiness plus a durable
  heartbeat.
- Compose uses one replica, `restart: unless-stopped`, and a `45s` graceful
  stop period. Pending work recovers after crash/restart.
- `SENT` records Bot API acceptance, not device delivery or read status.
  Definite failures become `FAILED`; uncertain timeout/post-send outcomes
  become `UNKNOWN` and never trigger automatic same-code resend.
- Web `/health` and password login remain green without Telegram credentials,
  OTP key, or a running dispatcher. OTP routes fail closed without the OTP key.

## Routes And UI

The repository implements these exact routes:

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/auth/otp` | Phone request page |
| `POST` | `/auth/otp/request` | Enumeration-safe challenge request |
| `GET` | `/auth/otp/verify` | Same-session verification page |
| `POST` | `/auth/otp/new-code` | Cooldown/rate-limited fresh challenge |
| `POST` | `/auth/otp/verify` | OTP verification and session login |

UI templates are `auth/otp_request.html` and `auth/otp_verify.html`. Existing
password login remains at `/auth/login`.

## Security Properties

- Public request and verification outcomes do not reveal user existence,
  linking state, dispatch state, or internal challenge status.
- Mutating routes require CSRF protection. OTP pages and responses use
  no-store and the repository's existing security-header policy.
- Raw OTP values are not persisted or included in events/logs. Events contain
  allowlisted actions and safe codes only.
- The OTP HMAC key is separate from the rate-limit key and bot token.
- Verification uses constant-time MAC comparison.
- Successful authentication rotates session and CSRF state.
- Browser/session binding prevents cross-browser IDOR and replay.
- Challenge eligibility is bound to both Telegram link row identity and its
  `linked_at` generation.
- Error handling and operator output are sanitized against credential, phone,
  chat, code, private-message, cookie, and session leakage.

## Automated Evidence

Earlier focused evidence:

| Validation | Result |
|---|---|
| M7.60/M7.61 targeted set | `22 passed` |
| M7.62 targeted set | `45 passed` |
| M7.63 targeted set | `43 passed` |

M7.66 exact local evidence:

| Validation | Result |
|---|---|
| `uv sync --dev --frozen` | GREEN; `39` packages checked |
| Ruff check | GREEN |
| Ruff format check | GREEN; `237` files already formatted |
| Git diff check | GREEN |
| Alembic heads/current | Single/current `e7f8a9b0c1d2`; parent `d4e5f6a7b8c9` |
| Fresh migration walk | Empty DB upgrade, parent downgrade, four-table removal/restore, index/constraint restore, repeat upgrade: GREEN |
| M5 containment | `5 passed` |
| Historical M5 regressions | `24 passed` |
| M6 Telegram fake-runtime regressions | `83 passed` |
| CI M7 focused guard | `21 passed` |
| Expanded M7 runtime/security set | `33 passed` |
| Sensitive-data audit | `8 passed` |
| Dispatcher/worker fake runtime, lock, and recovery set | `46 passed` |
| Full `pytest -q` | `1666 passed`, 0 failed/skipped/xfailed/xpassed |
| Full `pytest -q --durations=10` | `1666 passed`, 0 failed/skipped/xfailed/xpassed |
| Durations review | Slowest test `1.19s`; no dispatcher polling/timeout/recovery hang |
| Warning review | One pre-existing Starlette test-client deprecation warning |
| Docker no-cache build | `migrate`, `web`, `telegram-worker`, `otp-dispatcher`: GREEN |
| Credential-free runtime | Migration, web health/password login, OTP fail-closed, worker/dispatcher fail-closed: GREEN |
| Scope and source-graph containment | GREEN |
| Tracked diff and docs sensitive-data audit | GREEN |

The focused sets overlap each other and the full suite. Their counts are gate
evidence and are not added together as an artificial total.

M7.68 exact-SHA remote evidence:

| Validation | Result |
|---|---|
| Implementation SHA | `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3` |
| GitHub Actions run | `30453909901` |
| Workflow / job | `CI / dependency-sync` |
| Conclusion | `success` |
| Alembic head | `e7f8a9b0c1d2` |
| Ruff check | PASS |
| Ruff format check | PASS |
| Full pytest | `1666 passed` |
| Failed / skipped / xfailed / xpassed | `0/0/0/0` |
| Validated implementation sync | `HEAD == origin/main`, divergence `0 0`, clean worktree |

The successful CI job used PostgreSQL, frozen dependency sync, an exact-head
migration assertion, Ruff, M5 containment and historical regressions, M6
fake-worker coverage, M7 containment/fake/security coverage, and the full
M1-M7 suite. It used no real Telegram credential or network.

## Real Telegram Acceptance

M7.64 used a real dev/test Telegram network and device. No raw OTP, private
phone, chat ID, token, private message, session value, or screenshot is
recorded here.

| # | Sanitized acceptance item | Result |
|---:|---|---|
| 1 | M6 inbound worker linking remained healthy | PASS |
| 2 | Dispatcher strict preflight and health | PASS |
| 3 | Existing active linked-user request | PASS |
| 4 | Exactly one private-chat message | PASS |
| 5 | Uzbek message presentation | PASS |
| 6 | Russian message presentation | PASS |
| 7 | Same browser/session verification | PASS |
| 8 | Consumed-code replay rejection | PASS |
| 9 | Other browser/session rejection | PASS |
| 10 | Cooldown, fresh code, and old-code rejection | PASS |
| 11 | Unlink-before-verification invalidation | PASS |
| 12 | Relink invalidation of old-chat code | PASS |
| 13 | Pending dispatch restart recovery | PASS |
| 14 | Timeout to `UNKNOWN` with no duplicate auto-send | PASS |
| 15 | Password login and web health while dispatcher stopped | PASS |
| 16 | Database, log, and HTML leakage audit | PASS |

Acceptance status: `M7 ACCEPTANCE GREEN`.

## Out Of Scope

- Registration or new-user creation
- Public Telegram linking and customer activation
- Account recovery, password reset, or phone change
- New PII storage or offer flows
- SMS delivery
- Generic notification, outbox, or scheduler infrastructure
- Debt, payment, or rating features

## Closure Summary

| Gate | Status |
|---|---|
| Technical | GREEN |
| Real Telegram network/device acceptance | GREEN |
| Local final technical validation | GREEN |
| Remote CI | GREEN |
| M7 milestone | CLOSED |

GitHub Actions run `30453909901` validated the exact immutable implementation
SHA `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3`. M7 formal closure is recorded
in `m7-result.md`; the subsequent docs-only closeout does not alter the
validated implementation baseline.
