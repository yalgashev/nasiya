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

### Exact Schema Appendix

M7 database checks use a hard `failed_attempts` cap of `0..10`. Runtime
configuration must stay inside that range; the approved default service cap is
`5`, so the service can burn a challenge before the database hard cap.

#### `otp_challenges` Exact Schema

Columns:

```text
id UUID PRIMARY KEY
user_id UUID NULL
purpose VARCHAR(16) NOT NULL
telegram_link_id UUID NULL
telegram_linked_at TIMESTAMPTZ NULL
browser_binding_digest VARCHAR(64) NOT NULL
code_mac VARCHAR(64) NULL
status VARCHAR(32) NOT NULL
failed_attempts INTEGER NOT NULL DEFAULT 0
created_at TIMESTAMPTZ NOT NULL
activated_at TIMESTAMPTZ NULL
expires_at TIMESTAMPTZ NULL
consumed_at TIMESTAMPTZ NULL
terminal_at TIMESTAMPTZ NULL
updated_at TIMESTAMPTZ NOT NULL
```

Foreign keys:

```text
fk_otp_challenges_user_id_users_id:
  user_id -> users.id ON DELETE RESTRICT
fk_otp_challenges_telegram_link_id_telegram_links_id:
  telegram_link_id -> telegram_links.id ON DELETE RESTRICT
```

Checks:

```text
ck_otp_challenges_purpose_login:
  purpose = 'LOGIN'
ck_otp_challenges_browser_binding_digest_hmac_sha256_hex:
  browser_binding_digest ~ '^[0-9a-f]{64}$'
ck_otp_challenges_code_mac_hmac_sha256_hex:
  code_mac IS NULL OR code_mac ~ '^[0-9a-f]{64}$'
ck_otp_challenges_status_allowed:
  status IN (
    'PENDING_DISPATCH', 'ACTIVE', 'CONSUMED', 'SUPERSEDED',
    'EXPIRED', 'BURNED', 'INVALIDATED'
  )
ck_otp_challenges_failed_attempts_cap:
  failed_attempts BETWEEN 0 AND 10
ck_otp_challenges_real_identity_consistent:
  (user_id IS NULL AND telegram_link_id IS NULL AND telegram_linked_at IS NULL)
  OR (user_id IS NOT NULL AND telegram_link_id IS NOT NULL AND telegram_linked_at IS NOT NULL)
ck_otp_challenges_pending_dispatch_state:
  status != 'PENDING_DISPATCH'
  OR (
    code_mac IS NULL AND activated_at IS NULL AND expires_at IS NULL
    AND consumed_at IS NULL AND terminal_at IS NULL
  )
ck_otp_challenges_active_state:
  status != 'ACTIVE'
  OR (
    user_id IS NOT NULL AND telegram_link_id IS NOT NULL
    AND telegram_linked_at IS NOT NULL AND code_mac IS NOT NULL
    AND activated_at IS NOT NULL AND expires_at IS NOT NULL
    AND expires_at > activated_at AND consumed_at IS NULL
    AND terminal_at IS NULL
  )
ck_otp_challenges_terminal_state:
  (
    status IN ('PENDING_DISPATCH', 'ACTIVE')
    AND terminal_at IS NULL AND consumed_at IS NULL
  )
  OR (
    status = 'CONSUMED'
    AND terminal_at IS NOT NULL AND consumed_at IS NOT NULL
  )
  OR (
    status IN ('SUPERSEDED', 'EXPIRED', 'BURNED', 'INVALIDATED')
    AND terminal_at IS NOT NULL AND consumed_at IS NULL
  )
ck_otp_challenges_timestamp_order:
  updated_at >= created_at
  AND (activated_at IS NULL OR activated_at >= created_at)
  AND (expires_at IS NULL OR activated_at IS NOT NULL)
  AND (consumed_at IS NULL OR activated_at IS NOT NULL)
  AND (terminal_at IS NULL OR terminal_at >= created_at)
```

Indexes:

```text
uq_otp_challenges_one_outstanding_per_user_purpose:
  UNIQUE (user_id, purpose)
  WHERE status IN ('PENDING_DISPATCH', 'ACTIVE') AND user_id IS NOT NULL
uq_otp_challenges_one_outstanding_per_browser_purpose:
  UNIQUE (browser_binding_digest, purpose)
  WHERE status IN ('PENDING_DISPATCH', 'ACTIVE')
ix_otp_challenges_terminal_at:
  terminal_at
```

Forbidden columns: raw OTP, phone, IP, Telegram chat ID, message text, session
cookie, arbitrary JSON/metadata, deleted marker, and client-visible challenge
secret.

#### `otp_dispatches` Exact Schema

Columns:

```text
id UUID PRIMARY KEY
challenge_id UUID NOT NULL UNIQUE
status VARCHAR(32) NOT NULL
locale VARCHAR(16) NOT NULL
claimed_at TIMESTAMPTZ NULL
prepared_at TIMESTAMPTZ NULL
sent_at TIMESTAMPTZ NULL
terminal_at TIMESTAMPTZ NULL
failure_code VARCHAR(64) NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Foreign key:

```text
fk_otp_dispatches_challenge_id_otp_challenges_id:
  challenge_id -> otp_challenges.id ON DELETE RESTRICT
```

Unique constraint:

```text
uq_otp_dispatches_challenge_id:
  UNIQUE (challenge_id)
```

Checks:

```text
ck_otp_dispatches_status_allowed:
  status IN ('PENDING', 'PREPARED', 'SENT', 'FAILED', 'UNKNOWN', 'CANCELLED')
ck_otp_dispatches_locale_allowed:
  locale IN ('uz-Latn', 'ru')
ck_otp_dispatches_failure_code_format:
  failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
ck_otp_dispatches_state_consistent:
  (
    status = 'PENDING'
    AND prepared_at IS NULL AND sent_at IS NULL
    AND terminal_at IS NULL AND failure_code IS NULL
  )
  OR (
    status = 'PREPARED'
    AND claimed_at IS NOT NULL AND prepared_at IS NOT NULL
    AND sent_at IS NULL AND terminal_at IS NULL
    AND failure_code IS NULL
  )
  OR (
    status = 'SENT'
    AND prepared_at IS NOT NULL AND sent_at IS NOT NULL
    AND terminal_at IS NOT NULL AND failure_code IS NULL
  )
  OR (
    status IN ('FAILED', 'UNKNOWN')
    AND prepared_at IS NOT NULL AND terminal_at IS NOT NULL
    AND sent_at IS NULL AND failure_code IS NOT NULL
  )
  OR (
    status = 'CANCELLED'
    AND terminal_at IS NOT NULL AND sent_at IS NULL
    AND failure_code IS NULL
  )
ck_otp_dispatches_timestamp_order:
  updated_at >= created_at
  AND (claimed_at IS NULL OR claimed_at >= created_at)
  AND (prepared_at IS NULL OR claimed_at IS NOT NULL)
  AND (sent_at IS NULL OR prepared_at IS NOT NULL)
  AND (terminal_at IS NULL OR terminal_at >= created_at)
```

Indexes:

```text
ix_otp_dispatches_status_created_at:
  status, created_at
ix_otp_dispatches_terminal_at:
  terminal_at
```

Forbidden columns: raw OTP, message text/payload, phone, IP, Telegram chat ID,
bot token, arbitrary JSON, outbox/job/scheduler fields.

#### `otp_challenge_events` Exact Schema

Columns:

```text
id UUID PRIMARY KEY
challenge_id UUID NOT NULL
user_id UUID NULL
action VARCHAR(40) NOT NULL
occurred_at TIMESTAMPTZ NOT NULL
safe_code VARCHAR(64) NULL
```

Foreign keys:

```text
fk_otp_challenge_events_user_id_users_id:
  user_id -> users.id ON DELETE RESTRICT
```

`challenge_id` is a logical immutable reference rather than a database FK.
This keeps the approved 90-day event retention compatible with the approved
30-day terminal challenge/dispatch purge.

Checks:

```text
ck_otp_challenge_events_action_allowed:
  action IN (
    'ISSUED', 'DISPATCH_PREPARED', 'DISPATCH_RESULT', 'VERIFY_FAILED',
    'CONSUMED', 'SUPERSEDED', 'EXPIRED', 'BURNED',
    'INVALIDATED_BY_LINK_CHANGE'
  )
ck_otp_challenge_events_safe_code_format:
  safe_code IS NULL OR safe_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
```

Indexes:

```text
ix_otp_challenge_events_challenge_id_occurred_at:
  challenge_id, occurred_at
ix_otp_challenge_events_occurred_at:
  occurred_at
```

Forbidden columns: raw OTP, OTP MAC, phone, IP, Telegram chat ID, session
cookie, arbitrary JSON/metadata, message payload, bot token.

#### `otp_dispatcher_state` Exact Schema

Columns:

```text
id SMALLINT PRIMARY KEY
heartbeat_at TIMESTAMPTZ NULL
ready_at TIMESTAMPTZ NULL
updated_at TIMESTAMPTZ NOT NULL
```

Checks:

```text
ck_otp_dispatcher_state_singleton:
  id = 1
ck_otp_dispatcher_state_ready_requires_heartbeat:
  ready_at IS NULL OR heartbeat_at IS NOT NULL
ck_otp_dispatcher_state_heartbeat_not_before_ready:
  heartbeat_at IS NULL OR ready_at IS NULL OR heartbeat_at >= ready_at
```

No migration seed is required; repository primitives use an idempotent fixed-key
insert for `id = 1`. Dispatcher state is never deleted by retention purge.

Forbidden columns: cursor, raw OTP, message payload, phone, IP, Telegram chat
ID, token/secret, worker identity, arbitrary JSON/metadata.

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

### Dispatcher Exact Processing Appendix

Command/module:

```text
python -m app.otp.dispatcher run
python -m app.otp.dispatcher healthcheck
compose service: otp-dispatcher
```

Single-owner strategy:

- the dispatcher uses a stable PostgreSQL advisory lock key dedicated to OTP;
- the lock key is a source constant, not Python `hash()` and not secret-derived;
- lock acquisition is bounded and cancelled on SIGTERM;
- the lock connection is dedicated to ownership only;
- item processing uses short-lived sessions from the normal engine/session
  factory, never the lock connection.

Polling:

```text
OTP_DISPATCH_POLL_SECONDS=1
OTP_DISPATCH_BATCH_SIZE=20
OTP_DISPATCH_CLAIM_STALE_SECONDS=60
OTP_DISPATCH_HEARTBEAT_SECONDS=10
OTP_DISPATCH_STALE_SECONDS=60
```

Claim order:

1. claim `PENDING` rows ordered by `created_at ASC, id ASC`;
2. row-lock with `FOR UPDATE SKIP LOCKED`;
3. set `claimed_at` using the current dispatcher clock;
4. reclaim `PENDING` only if `claimed_at` is null or older than the claim
   stale threshold;
5. never claim `PREPARED` for send.

TX-D1:

1. lock the claimed dispatch and its challenge;
2. lock the user and the current Telegram link row;
3. require challenge `PENDING_DISPATCH`;
4. require user auth-active;
5. require link ID equals `telegram_link_id`, link is active, and `linked_at`
   equals the challenge snapshot;
6. if any validation fails, set dispatch `CANCELLED`, terminalize challenge
   where appropriate, append a safe event, and commit no send envelope;
7. generate the six-digit OTP only in memory;
8. compute the HMAC MAC;
9. set challenge `ACTIVE`, `failed_attempts=0`, `activated_at=now`,
   `expires_at=now+OTP_LOGIN_TTL_SECONDS`;
10. set dispatch `PREPARED`;
11. append `DISPATCH_PREPARED`;
12. commit and return an in-memory prepared envelope.

Send boundary:

1. send happens only after TX-D1 commit succeeds;
2. no SQLAlchemy transaction or session is open during the HTTP call;
3. target resolution uses the current locked Telegram link data captured in
   the prepared envelope;
4. provider is Telegram-only in M7 and sends exactly once;
5. raw OTP lifetime is the method scope around TX-D1/send only;
6. no automatic retry and no public delivery status.

TX-D2:

1. open a fresh transaction;
2. lock the dispatch;
3. `PREPARED -> SENT`, `FAILED`, or `UNKNOWN`;
4. set `sent_at` for `SENT`, `terminal_at` for all terminal results, and a
   sanitized failure code for `FAILED`/`UNKNOWN`;
5. append `DISPATCH_RESULT`;
6. commit;
7. TX-D2 failure never rolls back the already-active challenge and never
   triggers automatic resend.

Stale recovery:

- `PREPARED` older than `OTP_DISPATCH_STALE_SECONDS` becomes `UNKNOWN`;
- recovery appends `DISPATCH_RESULT` with a sanitized stale code;
- recovery never reconstructs or resends the OTP;
- `SENT`, `FAILED`, `UNKNOWN`, and `CANCELLED` are terminal.

Heartbeat and health:

- dispatcher writes heartbeat to `otp_dispatcher_state`;
- ready requires heartbeat;
- healthcheck returns OK only when ready and fresh under
  `OTP_DISPATCH_STALE_SECONDS`;
- web `/health` remains independent of dispatcher state.

SIGTERM behavior:

- request shutdown;
- finish no new claim after shutdown request;
- cancel heartbeat task;
- release advisory lock;
- dispose engine/client resources.

Forbidden:

- no changes to M6 getUpdates worker role;
- no web startup background task;
- no raw code/message/chat persistence;
- no generic queue/outbox/scheduler/Redis;
- no SMS provider/registry;
- no public delivery status endpoint.

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

1. Use the prepared envelope target only after TX-D1 commit succeeds.
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

### Verification Exact API/Order Appendix

Approved repository boundary for M7.44-M7.50:

```text
app.otp.verification.verify_login_otp(
    session,
    *,
    settings,
    browser_binding_digest,
    candidate_code_input,
    now,
)
```

Inputs and ownership:

- `session` is the caller-owned SQLAlchemy session and remains first
  positional;
- `settings` supplies `OTP_HMAC_KEY`, TTL/attempt policy, and no route-selected
  secret;
- `browser_binding_digest` is derived by the route from the current anonymous
  server-side session with `derive_browser_binding_digest`;
- `candidate_code_input` is the only user code input and is parsed to `OtpCode`
  inside the verification boundary;
- `now` is injected by `app.auth.deps.get_current_time`;
- the client never submits phone, user ID, challenge ID, dispatch ID, Telegram
  link ID, chat ID, attempt count, purpose, or delivery status.

Lookup and locking order:

1. Load the outstanding LOGIN candidate by browser binding with
   `load_verification_candidate_by_browser_for_update`.
2. If no candidate exists, run the dummy MAC/constant-time path and return the
   generic invalid internal outcome.
3. Require `ACTIVE`; terminal statuses and `PENDING_DISPATCH` map to the same
   generic invalid public response.
4. If `expires_at <= now`, mark `EXPIRED`, append `EXPIRED`, run the dummy
   compare-equivalent path, and return generic invalid.
5. If `failed_attempts >= settings.otp_login_max_verify_attempts`, mark or keep
   `BURNED`, append only the transition event that actually occurred, and
   return generic invalid.
6. Lock `users.id = challenge.user_id`.
7. Lock `telegram_links.id = challenge.telegram_link_id`.
8. Require user auth-active.
9. Require Telegram link active, same ID, non-null private chat, and
   `linked_at == challenge.telegram_linked_at`.
10. On user/link mismatch, mark `INVALIDATED`, cancel any open dispatch only if
    still non-terminal, append `INVALIDATED_BY_LINK_CHANGE` for link-generation
    mismatch, and return generic invalid.

Code and attempt order:

1. Parse the candidate with `OtpCode.from_user_input`, which trims only outer
   whitespace and accepts exactly six ASCII digits.
2. Malformed input still runs dummy HMAC/`compare_digest` work and returns the
   same generic invalid mapping.
3. Valid input computes the versioned MAC with `compute_otp_code_mac` using
   challenge UUID, user UUID, purpose LOGIN, and the candidate code.
4. Compare only with `verify_otp_code_mac`, which uses `hmac.compare_digest`.
5. Wrong code increments `failed_attempts`, appends `VERIFY_FAILED`, and when
   the configured maximum is reached marks `BURNED` and appends `BURNED` in
   that order.
6. Correct code performs guarded `CONSUMED`, sets `consumed_at`/`terminal_at`,
   appends `CONSUMED`, and returns the consumed user ID for the route adapter.
7. Correct code after consumed/burned/expired/superseded/invalidated state is
   a replay and maps to generic invalid.

Session rotation and redirect semantics:

- Domain verification does not create sessions and does not set cookies.
- `POST /auth/otp/verify` calls `verify_login_otp`; only an
  `OTP_CONSUMED` result proceeds to session login.
- The route then calls `app.auth.sessions.rotate_session` in the same request
  transaction. Existing semantics are reused: revoke current anonymous session
  via `revoke_session`, create a new authenticated session via
  `create_authenticated_session`, and therefore rotate both session token and
  CSRF secret.
- The response sets the new cookie with `app.auth.cookies.set_session_cookie`.
- Redirect target uses the existing safe-relative helper
  `app.auth.redirects.get_safe_redirect_target`; unsafe absolute or
  protocol-relative values fall back to `/auth/account`.
- `active_shop_id`, shop staff resolution, and customer activation are not read
  or mutated by OTP verification.

Failure and public mapping:

- repository/service primitives never call `commit()`, full `rollback()`, or
  `close()`;
- event insert failure rolls back the state transition;
- session rotation or cookie preparation failure rolls back consume because the
  request transaction has not committed yet;
- consume succeeds only after the outer request transaction commits;
- malformed, missing, wrong, expired, superseded, burned, link-changed, inactive
  user, and replay cases all map to the same localized invalid-code response;
- no route exposes delivery status, challenge status, attempt count, provider
  detail, Telegram chat identity, raw candidate code, session token, or
  `OTP_HMAC_KEY`;
- no Telegram network call occurs during verification.

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

M7 pre-auth OTP web flow uses only this route family:

| Method | Route | Template | Anonymous behavior | Authenticated behavior | Transaction owner | PRG / response |
|---|---|---|---|---|---|---|
| GET | `/auth/otp` | `auth/otp_request.html` | Reuse or create the existing anonymous server-side session, expose a CSRF token, and render the phone entry form. | `303` to `/auth/account`; no OTP mutation. | Route dependency owns the request SQLAlchemy session; only anonymous-session creation may commit. | `200` HTML, no-store. |
| POST | `/auth/otp/request` | none | Require current anonymous session and CSRF, resolve trusted client IP, derive browser binding from the anonymous session, call `request_login_otp`, and perform no Telegram network call. | With valid CSRF, `303` to `/auth/account`; no OTP mutation. | Route owns the outer transaction; OTP services never call `commit()`, full `rollback()`, or `close()`. | Always PRG to `/auth/otp/verify` for accepted, unknown, inactive, unlinked, unavailable, and provider-internal paths; response remains generic. |
| GET | `/auth/otp/verify` | `auth/otp_verify.html` | Reuse or create the existing anonymous server-side session, expose a CSRF token, and render the generic code entry/new-code page without looking up challenge status for presentation. | `303` to `/auth/account`; no OTP mutation. | Route dependency owns the request SQLAlchemy session; only anonymous-session creation may commit. | `200` HTML, no-store. |
| POST | `/auth/otp/verify` | none | Require current anonymous session and CSRF, derive browser binding, call `verify_login_otp`, and authenticate only an `OTP_CONSUMED` result through `rotate_session_after_otp_consume`. | With valid CSRF, `303` to `/auth/account`; no OTP mutation. | Route owns the outer transaction so consume, event insert, session revoke/create, cookie preparation, and commit are all-or-nothing. | Success `303` to safe `next` or `/auth/account`; invalid/replay/expired/burned/link-changed/missing/malformed responses PRG back to `/auth/otp/verify` with the same generic invalid message. |
| POST | `/auth/otp/new-code` | none | Require current anonymous session and CSRF, resolve trusted client IP, derive browser binding, call `request_new_login_code`, and perform no Telegram network call. | With valid CSRF, `303` to `/auth/account`; no OTP mutation. | Route owns the outer transaction; allowed new-code supersedes the old outstanding challenge in that transaction. | Always PRG to `/auth/otp/verify`; cooldown, missing challenge, terminal challenge, unavailable, unknown, and allowed paths do not reveal account or delivery state. |

Shared route rules:

- all OTP route responses, including redirects and CSRF failures, use
  `mark_auth_response_no_store`; the existing middleware supplies CSP and the
  other security headers;
- all mutation routes use the existing session-bound CSRF dependency; there is
  no OTP POST exemption;
- `next` is accepted only as an optional form/query value and is normalized by
  `app.auth.redirects.get_safe_redirect_target`; unsafe absolute,
  protocol-relative, non-root-relative, or empty values fall back to
  `/auth/account`;
- safe `next` may be carried between OTP pages only as a hidden form field or
  query value after validation; phone, user ID, challenge ID, dispatch ID,
  Telegram link ID, chat ID, delivery status, provider detail, and attempt
  count never appear in URL, HTML, flash/session data, logs, or response
  headers;
- pre-auth locale is bounded to `uz` and `ru`; a valid pre-auth locale cookie
  wins, otherwise the existing request-language resolver may choose `uz` or
  `ru`, and the final fallback is `uz`;
- all public copy is selected from the bounded locale catalog and rendered with
  Jinja autoescape; OTP templates do not use inline script or the `|safe`
  filter;
- request responses for eligible, unknown, inactive, unlinked, dispatcher
  unavailable, and configuration-unavailable cases are indistinguishable except
  for caller-abuse rate-limit copy allowed by the Outcome Contract;
- verify responses for malformed, missing, wrong, expired, superseded, burned,
  invalidated/link-changed, inactive user, and replay cases use one localized
  invalid-code message;
- no route queries or renders public delivery status; `/auth/otp/new-code` is
  the only user recovery action for lost or unknown delivery;
- `active_shop_id`, `require_shop_staff`, `resolve_current_shop`, shop
  membership, customer activation, and role-specific routing are outside this
  flow and are not read or mutated.

Template context contract:

| Template | Required context | Forbidden context/output |
|---|---|---|
| `auth/otp_request.html` | `csrf_token`, `page_language`, optional safe `next_url`, localized title/label/help/error strings, and password-login fallback URL. | Raw phone after PRG, canonical phone, account existence, Telegram link status, challenge/dispatch IDs, delivery status, provider details, chat ID, raw OTP, session token, shop/customer state. |
| `auth/otp_verify.html` | `csrf_token`, `page_language`, optional safe `next_url`, one generic request notice, one generic invalid-code/cooldown message slot, six-digit code input metadata, new-code form metadata, and password-login fallback URL. | Phone, account existence, challenge status, attempt count, remaining TTL as an oracle, delivery status, provider details, Telegram identity, raw OTP, session token, shop/customer state. |

The fixed public verify text remains:

```text
Agar kiritilgan telefon mos hisobga tegishli bo'lsa, kod Telegramga yuboriladi. Kod kelmasa, 60 soniyadan keyin yangi kod so'rang yoki parol bilan kiring.
```

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
