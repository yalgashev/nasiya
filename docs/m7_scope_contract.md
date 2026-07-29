# Nasiya M7 Scope Contract

Status: authoritative repository contract for M7 implementation.
Milestone: M7 - Telegram OTP Authentication.
Source authority: `/home/yalgashev/projects/nasiya_m7_00_final_scope_freeze.md`.
This file narrows repository execution only; it does not replace
`docs/tt_nasiya_web_v1.md`.

## Baseline

| Evidence | Value |
|---|---|
| Repository | `/home/yalgashev/projects/nasiya` |
| Branch | `main` |
| M6 implementation baseline | `54df18846663f9eb19ce21a131f796a5b3178bf5` |
| M6 implementation CI | run `30383047949`, success |
| M6 docs-only closeout | `9bf0056fd010d7424f3b577b937ce5204476bcf8` |
| Current HEAD / origin/main at M7 start | `9bf0056fd010d7424f3b577b937ce5204476bcf8` |
| Current-HEAD CI | run `30411142510`, success |
| Alembic parent head | `d4e5f6a7b8c9` |
| M6 test suite | `1373 passed`, `0 failed`, no skip/xfail reported |
| M6 status | `M6 REMOTE GREEN - CLOSED` |

M7.01 rechecked this baseline read-only: clean synced `main`, both M6 SHAs in
HEAD ancestry, exact-SHA CI green, and one Alembic head.

## One Capability

M7 implements exactly one capability:

Existing auth-active users with an active Telegram link can enter their phone
number and sign in with a six-digit OTP delivered by the Telegram bot.

Password login remains the default and stays available. M7 does not create or
activate users, link Telegram from a public phone-entry flow, recover accounts,
select a shop, or change debt/payment/rating behavior.

## Exact In Scope

- Pre-auth phone entry for existing active users with active Telegram links.
- Uniform public behavior for unknown, inactive, and unlinked phones.
- Anonymous server-side session reuse for CSRF and browser binding.
- Six ASCII digit OTP generated with a cryptographic generator.
- Dedicated `OTP_HMAC_KEY`, HMAC-SHA-256, canonical versioned MAC input, and
  constant-time verification.
- One outstanding challenge per `user_id + purpose` and one per
  `browser_binding_digest + purpose`.
- Purpose is server-side and M7-only: `LOGIN`.
- Challenge binds to the anonymous browser session digest, `telegram_link_id`,
  and `telegram_linked_at`.
- Unlink and successful relink invalidate outstanding login challenges in the
  same caller-owned transaction.
- Default TTL `180` seconds, max failed verifies `5`, new-code cooldown `60`
  seconds, issuance window `900` seconds with phone/user/IP attempts `3/3/20`.
- Narrow durable OTP dispatcher with `otp_dispatches`; no web Telegram network
  call.
- Separate `otp-dispatcher` process/container using the same image and one
  replica.
- Fake Telegram transport in automated tests; real Telegram only in M7.64.
- Minimal append-only OTP event journal and internal purge primitive.

## Exact Out Scope

- Registration, public new user creation, customer activation, recovery,
  password reset, phone change, admin-assisted recovery.
- New PII or customer document fields, object storage, offers, acceptance,
  debt, payment, rating, disclosure, reporting.
- Generic notification table/framework, generic outbox, message bus, reusable
  scheduler, `job_run`, Redis, Celery/RQ, webhook, WebSocket, public JSON API.
- Public phone-entry Telegram linking or redirecting unlinked users to linking.
- SMS OTP or additional OTP purposes.
- M6 inbound worker role changes.
- Raw OTP persistence, reversible OTP encryption, automatic same-code retry,
  public delivery status endpoint, delivered/read receipt claim.

`otp_dispatches` is only the M7 login OTP delivery queue. It is not a generic
outbox.

## Inherited Contracts

| Contract | Source | Repository evidence | M7 impact |
|---|---|---|---|
| Caller-owned transaction | M4/M6 contracts and M7 freeze | `app/db.py:22`, `app/telegram/service.py:151`, `app/telegram/service.py:357` | OTP repositories/services must not commit, full rollback, or close sessions. |
| External Telegram HTTP outside DB transaction | M6 PO-M6-5, M7 inherited contract | `app/telegram/update_processing.py:128`, `app/telegram/update_processing.py:269`, `app/telegram/bot_reply.py:73` | Dispatcher must commit TX-D1 before send and use TX-D2 for result. |
| M6 inbound worker unchanged | M6 scope, M7 PO-M7-12 | `app/telegram/worker.py:125`, `app/telegram/worker.py:268` | Add `otp-dispatcher`; do not add outbound dispatch to getUpdates worker. |
| Server-side session and CSRF rotation | TT 8, M6, M7 PO-M7-15/21 | `app/auth/sessions.py:98`, `app/auth/sessions.py:132`, `app/auth/csrf.py:34` | OTP GET creates/reuses anonymous session; OTP success rotates session and CSRF. |
| Trusted client-IP resolver | M4-I03/M6, M7 scope | `app/request_client_ip.py:19`, `app/settings.py:15` | OTP rate limits use `direct|trusted_proxy` resolver only. |
| `active_shop_id` isolation | M5/M6, M7 PO-M7-1/15 | `app/auth/sessions.py:208`, `app/auth/router.py:212` | OTP login must not depend on shop context or select a shop. |
| No raw secret/identifier leakage | TT 8, M6, M7 freeze | `app/auth/sessions.py:24`, `app/telegram/bot_api.py:82`, `app/telegram/client_ip.py:1` | Raw OTP, phone, IP, chat ID, session cookie, MAC, token stay out of logs/events/HTML. |
| Single-job CI and M5 containment | M6 decisions, M7 inherited contract | `.github/workflows/ci.yml:12`, `.github/workflows/ci.yml:65`, `.github/workflows/ci.yml:81` | Keep one job, Alembic, Ruff, containment guard, full pytest. |

No unresolved TT/scope contradiction was found in M7.02. The pre-gate review is
advisory only; its pending CI, sync web send, same-code resend, and ID-only link
binding notes are superseded by the final scope freeze.

## Persistence Contract

M7 adds exactly four tables in one Alembic revision whose parent is
`d4e5f6a7b8c9`.

### `otp_challenges`

Required semantic columns:

```text
id UUID PRIMARY KEY
user_id UUID NULL
purpose VARCHAR NOT NULL
telegram_link_id UUID NULL
telegram_linked_at TIMESTAMPTZ NULL
browser_binding_digest VARCHAR(64) NOT NULL
code_mac VARCHAR(64) NULL
status VARCHAR NOT NULL
failed_attempts INTEGER NOT NULL DEFAULT 0
created_at TIMESTAMPTZ NOT NULL
activated_at TIMESTAMPTZ NULL
expires_at TIMESTAMPTZ NULL
consumed_at TIMESTAMPTZ NULL
terminal_at TIMESTAMPTZ NULL
updated_at TIMESTAMPTZ NOT NULL
```

Rules:

- real challenges have non-null user, link, and link generation;
- `purpose` is `LOGIN`;
- `code_mac` is lowercase 64-hex and never raw OTP;
- `ACTIVE` requires MAC, activation time, and expiry;
- terminal states set `terminal_at`; `CONSUMED` also sets `consumed_at`;
- outstanding means `PENDING_DISPATCH` or `ACTIVE`;
- partial uniqueness enforces one outstanding per real user/purpose;
- partial uniqueness enforces one outstanding per browser binding/purpose;
- no phone, IP, raw OTP, chat ID, message, or session cookie column.

### `otp_dispatches`

Required semantic columns:

```text
id UUID PRIMARY KEY
challenge_id UUID NOT NULL UNIQUE
status VARCHAR NOT NULL
locale VARCHAR NOT NULL
claimed_at TIMESTAMPTZ NULL
prepared_at TIMESTAMPTZ NULL
sent_at TIMESTAMPTZ NULL
terminal_at TIMESTAMPTZ NULL
failure_code VARCHAR NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Rules:

- statuses are `PENDING`, `PREPARED`, `SENT`, `FAILED`, `UNKNOWN`,
  `CANCELLED`;
- no `DELIVERED` or `READ`;
- one dispatch per challenge;
- no raw code, message, phone, or chat ID;
- sanitized failure code only;
- stale `PREPARED` becomes `UNKNOWN`; automatic same-code resend is forbidden.

### `otp_challenge_events`

Required semantic columns:

```text
id UUID PRIMARY KEY
challenge_id UUID NOT NULL
user_id UUID NULL
action VARCHAR NOT NULL
occurred_at TIMESTAMPTZ NOT NULL
safe_code VARCHAR NULL
```

Allowed actions:

```text
ISSUED
DISPATCH_PREPARED
DISPATCH_RESULT
VERIFY_FAILED
CONSUMED
SUPERSEDED
EXPIRED
BURNED
INVALIDATED_BY_LINK_CHANGE
```

No raw OTP, OTP MAC, phone, IP, chat ID, session cookie, or arbitrary JSON.
Repository API is append-only.

### `otp_dispatcher_state`

Required semantic columns:

```text
id SMALLINT PRIMARY KEY
heartbeat_at TIMESTAMPTZ NULL
ready_at TIMESTAMPTZ NULL
updated_at TIMESTAMPTZ NOT NULL
```

Singleton state only. No cursor, secret, token, or worker identity.

## Challenge Lifecycle

```text
none              -> PENDING_DISPATCH
PENDING_DISPATCH -> ACTIVE
PENDING_DISPATCH -> SUPERSEDED
PENDING_DISPATCH -> INVALIDATED
PENDING_DISPATCH -> EXPIRED
ACTIVE           -> CONSUMED
ACTIVE           -> BURNED
ACTIVE           -> SUPERSEDED
ACTIVE           -> INVALIDATED
ACTIVE           -> EXPIRED
```

Terminal challenges never reactivate. New-code always creates a new challenge
row and supersedes the previous outstanding challenge in the same transaction.

## Issuance Protocol

1. Resolve/reuse anonymous session, CSRF, and trusted IP.
2. Normalize phone.
3. Apply phone and IP limiter for every path, including unknown phones.
4. Lookup eligible active user and active Telegram link.
5. If eligible, apply user limiter.
6. Serialize `user_id + purpose` and browser binding/purpose.
7. Supersede existing outstanding challenge and cancel its dispatch.
8. Insert `PENDING_DISPATCH` challenge.
9. Insert `PENDING` dispatch.
10. Append `ISSUED` event.
11. Outer request commits.
12. Return one generic verify-page redirect.

Unknown, inactive, and unlinked paths do not create real challenge rows but must
perform equivalent rate-limit and dummy HMAC work and return the same public
mapping.

## Dispatcher Protocol

TX-D1:

1. Claim one pending dispatch with a lock.
2. Verify challenge is pending and link generation is still current.
3. Generate raw six-digit OTP in memory.
4. Compute versioned MAC.
5. Activate challenge with `activated_at` and `expires_at`.
6. Set dispatch `PREPARED`.
7. Append events.
8. Commit.

External boundary:

1. Resolve active Telegram target after commit.
2. Render localized OTP message.
3. Call `TelegramOtpProvider.send_otp`.
4. No DB transaction is open during the HTTP call.

TX-D2:

1. Store `SENT`, `FAILED`, or `UNKNOWN`.
2. Append `DISPATCH_RESULT`.
3. Commit.

Crash after TX-D1 never triggers automatic duplicate resend. Stale `PREPARED`
is marked `UNKNOWN`; the challenge may still verify until TTL if the user
received the code.

## Verification Protocol

1. Compute current anonymous session binding digest.
2. Lock current active challenge by browser binding and purpose, not by client
   challenge ID.
3. Revalidate user is active.
4. Revalidate current active Telegram link ID and `linked_at`.
5. Enforce `now < expires_at`.
6. Enforce attempts below max.
7. Validate the submitted code is exactly six ASCII digits.
8. Compute submitted-code MAC and use constant-time compare.
9. Wrong code increments failed attempts and appends `VERIFY_FAILED`; fifth
   wrong attempt burns the challenge.
10. Correct code atomically consumes the challenge, appends `CONSUMED`, rotates
    session/CSRF, and redirects to the safe target/default account route.

Parallel correct verifies produce exactly one successful session.

## Outcome Contract

Internal outcomes may distinguish:

```text
OTP_NOT_ELIGIBLE
OTP_PENDING
OTP_INVALID
OTP_EXPIRED
OTP_SUPERSEDED
OTP_BURNED
OTP_LINK_CHANGED
OTP_CONSUMED
OTP_DELIVERY_FAILED
OTP_DELIVERY_UNKNOWN
OTP_CONFIGURATION_UNAVAILABLE
RATE_LIMITED
CSRF_FAILED
```

Public mapping:

- request endpoint always gives the generic accepted/verify-page flow;
- malformed, unknown, wrong, expired, superseded, burned, and link-changed
  verify outcomes share one generic invalid-code response;
- rate-limit can use existing localized `RATE_LIMITED`, but must not reveal the
  limiting dimension or account existence;
- provider failure details stay internal;
- no public delivery status endpoint exists.

## Settings Contract

Required for OTP issuance/verification/dispatcher:

```text
OTP_HMAC_KEY
```

Defaults:

```text
OTP_LOGIN_TTL_SECONDS=180
OTP_LOGIN_MAX_VERIFY_ATTEMPTS=5
OTP_LOGIN_RESEND_COOLDOWN_SECONDS=60
OTP_LOGIN_RATE_LIMIT_WINDOW_SECONDS=900
OTP_LOGIN_RATE_LIMIT_PHONE_ATTEMPTS=3
OTP_LOGIN_RATE_LIMIT_USER_ATTEMPTS=3
OTP_LOGIN_RATE_LIMIT_IP_ATTEMPTS=20
OTP_DISPATCH_POLL_SECONDS=1
OTP_DISPATCH_BATCH_SIZE=20
OTP_DISPATCH_CLAIM_STALE_SECONDS=60
OTP_DISPATCH_HEARTBEAT_SECONDS=10
OTP_DISPATCH_STALE_SECONDS=60
OTP_SEND_TIMEOUT_SECONDS=5
OTP_TERMINAL_RETENTION_DAYS=30
OTP_EVENT_RETENTION_DAYS=90
```

Validation:

- integer settings are positive;
- batch is `1..100`;
- max verify attempts is `1..10`;
- TTL is `60..600`;
- cooldown is lower than TTL;
- stale threshold is greater than heartbeat;
- send timeout is `1..15`;
- secret values are redacted in repr/errors;
- no secret hot reload; rotation is restart plus outstanding challenge
  invalidation.

Repository startup decision: web must continue to start without Telegram bot
token. OTP routes fail closed or degrade when `OTP_HMAC_KEY` is absent;
dispatcher fails closed without OTP key or bot credentials.

## Route Contract

| Method | Route | Semantics |
|---|---|---|
| GET | `/auth/otp` | phone input; anonymous session and CSRF |
| POST | `/auth/otp/request` | generic issue request; PRG |
| GET | `/auth/otp/verify` | generic code page |
| POST | `/auth/otp/verify` | verify/consume; PRG |
| POST | `/auth/otp/new-code` | cooldown/rate-limit; new challenge; PRG |

Rules:

- authenticated users redirect to the default account route;
- all GETs are side-effect-free except safe anonymous-session creation where
  already used by auth forms;
- all POSTs require CSRF;
- `next` uses the existing safe-relative redirect policy;
- no challenge UUID, phone, delivery status, or provider detail in URLs;
- every response is no-store and uses existing security headers.

## Dispatcher Deployment

- Same Docker image/codebase.
- Separate command `python -m app.otp.dispatcher run`.
- One replica for M7.
- Stable PostgreSQL advisory lock or equivalent single-owner guard.
- One-shot migration dependency before web and dispatcher.
- `restart: unless-stopped`, graceful stop, heartbeat healthcheck CLI.
- Bot token is available only to M6 worker and M7 OTP dispatcher.
- Web process has no bot token dependency.
- No Redis, generic queue broker, extra HTTP server, webhook, or scheduler.
- Dispatcher outage leaves `/health` and password login green; OTP request
  remains generic and real pending challenges expire or are superseded.

## Retention

- Terminal challenge/dispatch rows: 30 days from terminal time.
- Events: 90 days from occurrence.
- Purge is internal, idempotent, batched, and has no scheduler/route/admin UI.
- Outstanding/active rows and singleton dispatcher state are not purged.

## Validation Contract

Every checkpoint keeps:

- Ruff check and format check;
- relevant targeted tests;
- real PostgreSQL tests, no SQLite/create_all;
- no skip/xfail or softened assertions;
- Alembic single linear head;
- M5/M6 containment regressions;
- secret/PII leakage audit;
- `docs/tt_nasiya_web_v1.md` unchanged.

Final M7 closeout additionally requires full Alembic walk, Docker no-cache
runtime, fake dispatcher recovery, real Telegram OTP manual acceptance, exact
pushed-SHA remote CI success, `m7-result.md`, synced clean `main`, and no M8
work started.
