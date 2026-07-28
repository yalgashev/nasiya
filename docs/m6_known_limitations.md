# M6 Known Limitations

Status: STARTED
Date: 2026-07-27

## KL-M6-01

Title: Real Telegram transport, worker, and QR image generation are not approved
for current M6.

Decision:

- Current M6 may document and later implement account-scoped
  `/auth/telegram/*` web/domain integration.
- Current M6 must not add real Telegram Bot API calls, `TELEGRAM_BOT_TOKEN`,
  webhook, polling worker, scheduler, queue, Redis, external network CI, QR
  encoder dependency, or QR image output.
- The approved current reveal artifact is a plain HTTPS Telegram start link
  returned once through an HTMX no-store fragment.

Impact:

- Production bot delivery/reply is contract-only until a later PO decision.
- QR scanning UX is unavailable in current M6.
- CI remains zero-credential and zero-real-Telegram-network.

Mitigation:

- Keep domain services pure and caller-transaction-owned.
- Keep fake verified-private Telegram adapter tests.
- If real transport is later approved, update `docs/m6_decisions.md` first with
  HTTP client, QR package/format, license/maintenance, worker fail-closed token
  behavior, restart policy, stop grace period, and healthcheck.
