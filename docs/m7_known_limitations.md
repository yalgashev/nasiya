# Nasiya M7 Known Limitations

Status: approved known limitations for the closed M7 milestone.
Source authority: `/home/yalgashev/projects/nasiya_m7_00_final_scope_freeze.md`
and M7.04 feasibility audit.

## KL-M7-01 - Eligibility And Login-Only Scope

M7 Telegram OTP is available only to an existing active user with an active
Telegram link. Its only purpose is `LOGIN`.

Impact:

- Registration, public linking, activation, account recovery, password reset,
  and phone change are outside M7.
- A user who is not already eligible receives the same enumeration-safe public
  response and can continue to use the existing password flow where
  applicable.

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

## KL-M7-04 - Provider Acceptance Is Not Device Delivery

`SENT` means that Telegram Bot API accepted the request. Telegram does not
provide this flow with an end-device delivery or read receipt.

Impact:

- Product and operations must not present `SENT` as delivered or read.
- A Telegram outage or provider failure can make OTP login unavailable while
  web `/health` and password login remain available.

## KL-M7-05 - Dispatcher Is Deliberately Narrow

The dispatcher is an M7-specific Telegram OTP process, not a general delivery
platform.

Impact:

- SMS is deferred to Phase 2.
- Generic notifications, outbox infrastructure, and a purge scheduler are
  outside M7.
- Retention purge remains an internal primitive with no scheduler, admin UI,
  or public purge/status endpoint.

## KL-M7-06 - Production Provisioning Remains External

Automated tests and CI use fake Telegram transport and deterministic test-only
secrets. Production rollout still requires separate production credential
provisioning, access control, monitoring, and rotation outside tracked files.

Impact:

- `OTP_HMAC_KEY` rotation invalidates outstanding challenges; M7 has no
  fallback key chain.
- CI must not require a real Telegram credential or network.
- Real network/device acceptance is `GREEN` with `16/16 PASS`.

## Remote Closure Evidence

Remote CI:
`SATISFIED — exact M7 implementation SHA remote CI success`.

- Implementation SHA:
  `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3`.
- GitHub Actions run: `30453909901`.
- Workflow/job: `CI / dependency-sync`.
- Conclusion: `success`.
- Tests: `1666 passed`, 0 failed/skipped/xfailed/xpassed.
- Alembic head: `e7f8a9b0c1d2`.
- Ruff check and Ruff format check: PASS.
