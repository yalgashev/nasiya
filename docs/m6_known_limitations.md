# M6 Known Limitations

Status: PRE-PRODUCTION ACCEPTANCE CONDITION SATISFIED
Date: 2026-07-28

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
- M6.72 used an explicitly configured untracked dev/test credential and
  completed the real bot, language, expiry, mobile/desktop handoff, restart
  recovery and no-leak checks.
- The REAL-BOT gate is `M6 ACCEPTANCE GREEN`; no fake-transport result was
  classified as real acceptance.
- Remote CI gate:
  `SATISFIED — exact implementation SHA remote CI success`.
  Implementation SHA:
  `54df18846663f9eb19ce21a131f796a5b3178bf5`; GitHub Actions run:
  `30383047949`.
- Production rollout continues to require production-specific credential
  provisioning and rotation.

Mitigation:

- Keep the bot token outside tracked files, docs, fixtures, logs, URLs, and
  exception text.
- Run M6.72 only with an explicitly prepared dev bot; never improvise a token
  or convert fake transport evidence into a real acceptance PASS.
- Keep web health independent so a stopped/unhealthy worker does not break
  login or M2-M5 web flows.
- Keep the completed M6.72 report free of raw credential, token, chat
  identifier, private message, QR, deep-link and screenshot data.
