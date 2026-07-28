# M6 Telegram Worker Runbook

Status: ACTIVE
Date: 2026-07-28

## Deployment Contract

- Build `migrate`, `web`, and `telegram-worker` from the same Dockerfile.
- Start PostgreSQL first. Run `migrate` once after DB health and require exit
  code `0`; web and worker depend on `service_completed_successfully`.
- Expected head is `d4e5f6a7b8c9`, exact parent `a6b4c2d8e9f1`.
- Run exactly one worker replica with `restart: unless-stopped` and
  `stop_grace_period: 45s`.
- Web starts without Telegram credentials and `/health` is independent of the
  worker. Worker fails closed when token or strict username is absent.

```bash
docker compose build migrate web telegram-worker
docker compose up -d db migrate web
docker compose up -d telegram-worker
docker compose ps -a
```

Do not use `docker compose down -v` during deployment or validation.

## Secrets And Preflight

Provide `TELEGRAM_BOT_TOKEN` and matching `TELEGRAM_BOT_USERNAME` only through
the runtime secret mechanism. Never put either value in tracked files,
commands, screenshots, logs, reports, or CI.

Startup acquires the PostgreSQL advisory lock, then verifies `getMe`, exact
username match, bot identity, and inactive webhook. Fatal credential,
protocol, username, webhook, or `409` poller conflict exits the process with a
sanitized operational code. Transient Bot API or network failure retries with
exponential backoff from `1s` to `30s`; `429 retry_after` is capped at `60s`.

## Health And Lock

The worker writes heartbeat every `10s`; heartbeat older than `60s`, missing
readiness, or database failure is unhealthy. Compose runs:

```bash
docker compose exec telegram-worker \
  python -m app.telegram.worker healthcheck
```

The healthcheck interval is `15s`, timeout `5s`, retries `3`, start period
`20s`. `WORKER_LOCK_UNAVAILABLE` means another poller owns the advisory lock.
Confirm the intended replica count, stop the unintended process, and allow up
to the approved `60s` lock acquisition deadline. Do not bypass the lock.

## Update Failures

Expected terminal outcomes advance the cursor without retry. Allowlisted
transient DB/API failures do not advance the cursor or poison count. Unknown
TX-A failures increment `telegram_update_failures` in fresh TX-B; attempt `5`
quarantines and advances the cursor atomically. Unknown TX-B failure is fatal.

`TELEGRAM_UPDATE_QUARANTINED` is the operator signal to investigate code and
the matching sanitized failure code. Do not dump raw Telegram updates, chat
identifiers, SQL parameters, phone numbers, IP addresses, or link tokens.
`TELEGRAM_REPLY_DELIVERY_FAILED` does not undo a committed link; web status is
canonical.

## Rotation And Shutdown

For token rotation, stop the worker, rotate the runtime secret, verify the
strict username still matches, then recreate the worker. No web restart is
required unless the displayed bot username also changes.

```bash
docker compose stop -t 45 telegram-worker
docker compose up -d --force-recreate telegram-worker
docker compose exec telegram-worker \
  python -m app.telegram.worker healthcheck
```

SIGTERM requests cooperative shutdown, marks readiness false, closes the HTTP
client and DB engine, and releases the advisory lock within the `45s` grace
window. Never kill the process merely to clear a healthy lock.

## Validation

Automated worker and recovery tests use injected fake transport:

```bash
uv run pytest -q \
  tests/test_telegram_bot_api.py \
  tests/test_telegram_worker.py \
  tests/test_telegram_worker_lock_postgresql.py \
  tests/test_telegram_update_processing_postgresql.py \
  tests/test_telegram_post_commit_reply.py
```

Real-bot acceptance is a separate PRE-PRODUCTION gate. It must verify preflight,
private mobile/desktop linking, replay, expiry, QR, Uzbek/Russian replies,
unlink/relink, restart recovery, and no secret/identifier leakage. Without the
prepared dev/test bot, record `BLOCKED: REAL TELEGRAM BOT NOT READY`.
