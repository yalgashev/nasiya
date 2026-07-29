# Nasiya M7 Known Limitations

Status: approved known limitations for M7.
Source authority: `/home/yalgashev/projects/nasiya_m7_00_final_scope_freeze.md`
and M7.04 feasibility audit.

## KL-M7-01 - Real Bot Credential Acceptance

Automated tests and CI use fake Telegram transport only. Real Telegram OTP
delivery requires a separate dev/prod bot token and username outside tracked
files, and is accepted only in M7.64.

Impact:

- CI must not require `TELEGRAM_BOT_TOKEN`.
- Web startup and password login remain independent of bot availability.
- Manual acceptance records only safe evidence, never token, chat ID, phone,
  OTP, message payload, or session cookie.

## KL-M7-02 - Async Delivery May Be Unknown

The durable dispatcher commits the OTP MAC before Telegram send. If the process
crashes or times out after TX-D1, the dispatch result can become `UNKNOWN`.
M7 intentionally does not automatically resend the same code because that would
risk duplicate delivery.

Impact:

- Public UX remains generic and points to new-code after cooldown or password
  login.
- `UNKNOWN` is internal operational state, not a public delivery oracle.
- User-triggered new-code creates a fresh challenge and fresh OTP.

## KL-M7-03 - Browser Binding Limits Cross-Browser Completion

OTP verification is bound to the anonymous browser session that requested the
code. A code requested in one browser/device does not authenticate another
browser/device, even if the Telegram message is visible there.

Impact:

- This is intentional replay and IDOR protection.
- Users must complete verification in the browser where they requested the
  code, or request a new code in the new browser after cooldown/rate limits.
- No client-visible challenge secret or challenge UUID is introduced to relax
  this boundary.

## Operational Notes

- `OTP_HMAC_KEY` rotation invalidates outstanding challenges after process
  restart; there is no fallback key chain in M7.
- Dispatcher outage does not make `/health` or password login fail.
- M7 does not claim Telegram `DELIVERED` or `READ`; only send attempt result is
  stored.
- Retention purge is an internal primitive. There is no scheduler, admin UI, or
  public purge/status endpoint in M7.
