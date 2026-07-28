# M6 Known Limitations

Status: STARTED
Date: 2026-07-27

## KL-M6-01

Title: Real-bot acceptance requires separate PRE-PRODUCTION credentials and
operational verification.

Decision:

- M6 implements the production Telegram Bot API adapter, long-polling worker,
  persisted cursor/quarantine, account web flow, local QR, and protected
  unlink/relink.
- All automated tests and CI use injected fake transport with no Telegram
  credential or real network.
- A real dev/test bot token and matching strict bot username are accepted only
  from untracked runtime secret configuration for the explicit M6.72
  acceptance task.
- Web startup and `/health` never require the bot token. Worker startup fails
  closed when the token is missing or empty.

Impact:

- M6.01-M6.71 and CI are not blocked by an unavailable real bot.
- M6.72 must report `BLOCKED: REAL TELEGRAM BOT NOT READY` unless the separate
  dev bot, username, secret handling, webhook-off state, private `/start`
  round trip, and no-leak checks can be verified honestly.
- `M6 TECHNICAL GREEN` may be established with fake transport; production
  rollout remains gated by this acceptance and the final remote closeout.

Mitigation:

- Keep the bot token outside tracked files, docs, fixtures, logs, URLs, and
  exception text.
- Run M6.72 only with an explicitly prepared dev bot; never improvise a token
  or convert fake transport evidence into a real acceptance PASS.
- Keep web health independent so a stopped/unhealthy worker does not break
  login or M2-M5 web flows.
