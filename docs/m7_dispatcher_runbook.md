# M7 Telegram OTP Dispatcher Runbook

Status: ACTIVE
Date: 2026-07-29

## Process Boundary

- The web process creates an OTP challenge and a durable dispatch intent in
  PostgreSQL. It does not call the Telegram network.
- The M6 inbound Telegram worker remains a separate, unchanged long-polling
  process for account linking.
- The M7 `otp-dispatcher` is a separate process that claims and sends pending
  OTP dispatches.
- `TELEGRAM_BOT_TOKEN` is never provided to the web process. The OTP-enabled
  web process and dispatcher must use the same dedicated `OTP_HMAC_KEY`; web
  needs it for browser binding and verification and otherwise keeps OTP routes
  fail-closed.
- The dispatcher receives `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, and
  `OTP_HMAC_KEY`. The M6 inbound worker receives its Telegram credential and
  username but does not receive `OTP_HMAC_KEY`.
- Web `/health` and password login remain available when the dispatcher is
  stopped or Telegram credentials are absent. OTP issuance and verification
  are unavailable when the OTP key is absent.

## Runtime Settings

Required names for a real dev/test or production dispatcher:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_BOT_USERNAME`
- `OTP_HMAC_KEY`

Generate `OTP_HMAC_KEY` from at least 256 bits of entropy and encode it as at
least 32 characters. It must differ from the bot token and
`RATE_LIMIT_HMAC_KEY`. Keep all credential values in the runtime secret
mechanism, never in tracked files, CI, command output, screenshots, or reports.

The web and dispatcher must receive the same OTP key. Rotating that key
invalidates every outstanding challenge created with the old key. Coordinate
rotation with an approved service or expiry procedure; do not repair product
state with manual SQL.

Operational defaults:

- OTP lifetime: `180s`
- Verification attempts: `5`
- New-code cooldown: `60s`
- Dispatcher poll interval: `1s`
- Batch size: `20`
- Prepared-claim stale threshold: `60s`
- Heartbeat interval: `10s`
- Health stale threshold: `60s`
- Telegram send timeout: `5s`

## Deployment And Startup

Migration `e7f8a9b0c1d2` must be current before web, the M6 worker, or the M7
dispatcher starts. Compose waits for the one-shot `migrate` service and runs
one dispatcher replica with `restart: unless-stopped` and a `45s` stop grace
period.

For a local real-bot acceptance environment, first verify the secret file
without reading its contents:

```bash
git check-ignore -q .env.local
test "$(stat -c '%a' .env.local)" = "600"
docker compose --env-file .env.local config --quiet
```

Then start the stack:

```bash
docker compose --env-file .env.local up -d db migrate web
docker compose --env-file .env.local up -d telegram-worker otp-dispatcher
docker compose --env-file .env.local ps -a
```

The dispatcher command outside Compose is:

```bash
python -m app.otp.dispatcher run
```

Startup acquires the PostgreSQL advisory lock and performs strict Telegram
preflight: valid bot identity via `getMe`, exact configured username, and an
inactive webhook. A missing key, missing credential, identity mismatch, active
webhook, or lock conflict fails closed with a sanitized operational code.

Do not use `docker compose config` without `--quiet` when real credentials are
loaded. Do not use shell tracing, `env`, `printenv`, or commands that print the
secret file.

## Health And Recovery

Run the dispatcher healthcheck:

```bash
docker compose --env-file .env.local exec otp-dispatcher \
  python -m app.otp.dispatcher healthcheck
```

Compose checks health every `15s`, with timeout `5s`, three retries, and a
`20s` start period. Missing readiness, a heartbeat older than `60s`, or a
database failure is unhealthy. A lock-unavailable result means another
dispatcher owns the singleton advisory lock; confirm the intended replica and
stop the unintended process instead of bypassing the lock.

SIGTERM marks readiness false, stops claiming work, closes the Telegram client
and database engine, and releases the advisory lock within the `45s` grace
period. A dispatch still in `PENDING` survives a crash and is recovered after
restart. A stale `PREPARED` dispatch becomes `UNKNOWN`; it is not sent again
automatically.

Web independence checks:

```bash
curl --fail --silent --show-error http://localhost:8000/health
```

Password login must also remain functional while `otp-dispatcher` is stopped.

## Delivery Semantics

- `PENDING`: durable intent exists and has not been claimed.
- `PREPARED`: the dispatcher committed TX-D1, including the code MAC and claim,
  before the external send.
- `SENT`: Telegram Bot API accepted the send request. This is not proof of
  end-device delivery or a read receipt.
- `FAILED`: a definite provider, credential, protocol, rate, or server failure
  was recorded with a sanitized failure code.
- `UNKNOWN`: the outcome is uncertain, including timeout or a stale
  post-TX-D1 claim. The same code is never automatically resent.
- `CANCELLED`: the dispatch became ineligible before send.

Exactly one dispatcher owns a PostgreSQL advisory lock. It claims rows with
row locking and `SKIP LOCKED`, commits TX-D1 before Telegram I/O, then records
the provider result in TX-D2. This boundary prevents a database rollback from
silently repeating an external send.

For `UNKNOWN`, inspect only status, timestamps, and allowlisted operational
codes. Never infer delivery. After the normal cooldown, the user can request a
fresh challenge and fresh code through the product flow.

## Operational Procedures

Run the fake-transport smoke without a real credential or network:

```bash
uv run pytest -q \
  tests/test_otp_dispatcher.py \
  tests/test_otp_concurrency_containment_matrix.py \
  tests/test_otp_enumeration_matrix.py \
  tests/test_otp_sensitive_data_audit.py
```

Real dev/test preflight:

1. Confirm `.env.local` is ignored and mode `600` without printing it.
2. Start the M6 worker and verify its health.
3. Start the dispatcher; its strict startup preflight verifies token,
   username, bot identity, and inactive webhook.
4. Verify dispatcher health, web `/health`, and password login.
5. Use only an existing active user with an active Telegram link.

```bash
docker compose --env-file .env.local exec telegram-worker \
  python -m app.telegram.worker healthcheck
docker compose --env-file .env.local exec otp-dispatcher \
  python -m app.otp.dispatcher healthcheck
```

Stop or restart the dispatcher:

```bash
docker compose --env-file .env.local stop -t 45 otp-dispatcher
docker compose --env-file .env.local up -d --force-recreate otp-dispatcher
docker compose --env-file .env.local exec otp-dispatcher \
  python -m app.otp.dispatcher healthcheck
```

For credential rotation, stop the affected process, rotate the runtime secret,
invalidate or allow expiry of affected outstanding work through approved
product semantics, then force-recreate and healthcheck the process. Recreate
both web and dispatcher when rotating `OTP_HMAC_KEY`; recreate the M6 worker
and dispatcher when rotating the shared bot credential. Verify the exact bot
username and inactive webhook again.

Troubleshooting evidence is restricted to process health, status names,
timestamps, counts, and allowlisted safe codes. Never log or report a raw OTP,
bot token, OTP key, Telegram chat ID, private phone, message payload, browser
cookie, or session identifier.

## M7.64 Real Acceptance

The following checks used a real dev/test Telegram network and device. Evidence
was recorded without raw identity, credential, code, message, or screenshot.

| # | Sanitized check | Result |
|---:|---|---|
| 1 | M6 inbound worker linking remained healthy | PASS |
| 2 | OTP dispatcher strict preflight and health | PASS |
| 3 | Existing active linked-user phone request | PASS |
| 4 | Exactly one private-chat OTP message | PASS |
| 5 | Uzbek message presentation | PASS |
| 6 | Russian message presentation | PASS |
| 7 | Same browser/session verification | PASS |
| 8 | Consumed-code replay rejection | PASS |
| 9 | Other browser/session rejection | PASS |
| 10 | Cooldown, fresh code, and old-code rejection | PASS |
| 11 | Unlink-before-verification invalidation | PASS |
| 12 | Relink invalidation of old-chat code | PASS |
| 13 | Pending dispatch recovery after restart | PASS |
| 14 | Induced timeout became `UNKNOWN` without duplicate auto-send | PASS |
| 15 | Password login and web health with dispatcher stopped | PASS |
| 16 | Database, log, and HTML leakage audit | PASS |

Acceptance result: `M7 ACCEPTANCE GREEN`.
