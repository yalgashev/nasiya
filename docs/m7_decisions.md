# Nasiya M7 Decisions

Status: authoritative M7 repository decision log.
Source authority: `/home/yalgashev/projects/nasiya_m7_00_final_scope_freeze.md`.
Product Owner disposition: `21/21 FINAL APPROVED`.

## Baseline

M7 starts from M6 docs-only closeout
`9bf0056fd010d7424f3b577b937ce5204476bcf8`, with implementation baseline
`54df18846663f9eb19ce21a131f796a5b3178bf5`, CI runs `30383047949` and
`30411142510` green, Alembic parent `d4e5f6a7b8c9`, and M6 closed as
`M6 REMOTE GREEN - CLOSED`.

## Decision Table

| ID | Final decision | Implementation consequence |
|---|---|---|
| PO-M7-1 | Eligibility is existing, auth-active user with active Telegram link. Role and shop context are not criteria. | Pre-auth lookup returns a real challenge only for eligible users; public response stays uniform. |
| PO-M7-2 | `purpose` is mandatory, server-side, and only `LOGIN` in M7. | No client purpose input and no generic step-up OTP. |
| PO-M7-3 | OTP format is six ASCII digits, string, leading zeros preserved, CSPRNG. | Use a typed code value and reject non-exact submissions. |
| PO-M7-4 | Dedicated `OTP_HMAC_KEY`, HMAC-SHA-256, versioned canonical binding, constant-time compare. | Persist MAC only; key rotation invalidates outstanding challenges. |
| PO-M7-5 | At most one outstanding challenge per `user_id + purpose`; new request atomically supersedes old. | Enforce partial unique index and locked supersession. |
| PO-M7-6 | Cooldown 60 seconds; 900-second issuance window with phone/user/IP `3/3/20`. | Reuse `auth_rate_limits` with OTP-specific scopes. |
| PO-M7-7 | TTL 180 seconds; new-code creates a new challenge and new code. | No same-code resend; old challenge becomes unusable. |
| PO-M7-8 | Five wrong attempts burns the challenge; no account lockout. | Attempts are counted under lock with `BURNED` terminal state. |
| PO-M7-9 | Enumeration safety through uniform public response, no web network call, unknown-path limiter, dummy HMAC work. | Unknown/inactive/unlinked paths do not reveal account state. |
| PO-M7-10 | Challenge binds to Telegram link row ID and `linked_at`. | Verification rechecks current link generation. |
| PO-M7-11 | Unlink and successful relink invalidate outstanding challenges in the same transaction. | Add OTP invalidation call to existing link mutation services. |
| PO-M7-12 | Delivery uses separate narrow durable OTP dispatcher; M6 inbound worker stays unchanged. | Add `otp_dispatches`, provider boundary, and `otp-dispatcher` command. |
| PO-M7-13 | Delivery states are `PENDING/PREPARED/SENT/FAILED/UNKNOWN/CANCELLED`; no `DELIVERED`; no duplicate auto retry. | Stale prepared rows become `UNKNOWN`; new-code is user-triggered. |
| PO-M7-14 | Telegram unavailable UX stays generic; password login and `/health` stay green. | Dispatcher outage must not break web or password auth. |
| PO-M7-15 | Reuse anonymous session; successful OTP login rotates session and CSRF; preserve other sessions; do not select shop. | Use existing `rotate_session` and avoid `active_shop_id`. |
| PO-M7-16 | Challenge binds to the anonymous browser session digest. | Code from another browser does not verify. |
| PO-M7-17 | Language is uz-Latn/ru with uz-Latn fallback; bot message contains only code, TTL, and warning. | No phone, name, shop, account, link, or token in Telegram text. |
| PO-M7-18 | Account recovery is out of M7. | No password reset, phone change, or recovery fallback. |
| PO-M7-19 | Password login remains available and default. | OTP is an additional login option only. |
| PO-M7-20 | Narrow append-only OTP event journal; not a generic audit system. | Add `otp_challenge_events` with safe action codes only. |
| PO-M7-21 | Existing anonymous server-side session CSRF is reused; OTP POST is not CSRF-exempt. | All OTP mutation routes use existing CSRF dependency or equivalent. |

## TT Change Requests

| ID | Final disposition |
|---|---|
| CR-M7-01 | `TELEGRAM_NOT_LINKED` remains in authenticated context; pre-auth OTP maps it to uniform public behavior. |
| CR-M7-02 | Public phone-entry linking is deferred to a later registration/linking milestone. |
| CR-M7-03 | Generic notification framework is deferred; M7 delivery state stays inside OTP domain tables. |
| CR-M7-04 | OTP internal/public outcome catalog is stabilized in M7 docs and code. |
| CR-M7-05 | Profile language persistence is deferred; M7 uses request-language cookie with uz-Latn fallback. |

## Superseded Pre-Gate Notes

The advisory pre-gate review is not final authority. The following notes are
superseded by the final scope freeze:

- M6 remote CI pending status;
- PO-M7 decisions not final;
- synchronous web OTP delivery;
- resending the same OTP code;
- binding only to `telegram_links.id` without `linked_at`.

## Approved Minimal New Primitives

M7 may add these primitives because they are required by final scope and absent
from the M6 baseline:

- `OTP_HMAC_KEY` and OTP numeric settings in `app/settings.py`.
- Typed OTP package/module, using only Python stdlib `secrets`, `hmac`, and
  `hashlib` for code generation/MAC.
- Four ORM models and one Alembic revision for `otp_challenges`,
  `otp_dispatches`, `otp_challenge_events`, and `otp_dispatcher_state`.
- OTP challenge repository with locks, outstanding lookup, supersession,
  terminalization, and purge.
- OTP dispatch repository with claim, prepare, result, stale recovery, and
  heartbeat.
- `OtpDeliveryProvider` protocol and `TelegramOtpProvider`.
- Send-specific Bot API timeout support for OTP sends.
- Separate `python -m app.otp.dispatcher run|healthcheck` command.
- OTP route family `/auth/otp/*` and templates.
- Link-change invalidation hook in existing Telegram unlink/successful relink
  services.

These primitives do not authorize generic notification/outbox/scheduler,
Redis/Celery, webhook, SMS, public status API, recovery, registration, or PII
storage.

## Runtime Defaults

| Setting | Default |
|---|---:|
| `OTP_LOGIN_TTL_SECONDS` | `180` |
| `OTP_LOGIN_MAX_VERIFY_ATTEMPTS` | `5` |
| `OTP_LOGIN_RESEND_COOLDOWN_SECONDS` | `60` |
| `OTP_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `900` |
| `OTP_LOGIN_RATE_LIMIT_PHONE_ATTEMPTS` | `3` |
| `OTP_LOGIN_RATE_LIMIT_USER_ATTEMPTS` | `3` |
| `OTP_LOGIN_RATE_LIMIT_IP_ATTEMPTS` | `20` |
| `OTP_DISPATCH_POLL_SECONDS` | `1` |
| `OTP_DISPATCH_BATCH_SIZE` | `20` |
| `OTP_DISPATCH_CLAIM_STALE_SECONDS` | `60` |
| `OTP_DISPATCH_HEARTBEAT_SECONDS` | `10` |
| `OTP_DISPATCH_STALE_SECONDS` | `60` |
| `OTP_SEND_TIMEOUT_SECONDS` | `5` |
| `OTP_TERMINAL_RETENTION_DAYS` | `30` |
| `OTP_EVENT_RETENTION_DAYS` | `90` |

Invalid config fails fast for OTP issuance/dispatcher. Web startup remains
independent of Telegram bot token; OTP routes fail closed/degraded without the
OTP secret.

## Stop Conditions

M7 stops before commit/push if any of these become true:

- M6 closure ancestry or exact-SHA CI evidence fails.
- Branch/repo sync is unexpected.
- TT or final scope is changed.
- Current Alembic head is unexpected.
- Raw OTP persistence is required.
- Sync web Telegram send is reintroduced.
- M6 inbound worker becomes the OTP dispatcher.
- Telegram token is required by web process.
- Generic outbox/notification/scheduler/Redis is required.
- Unknown/unlinked public response differs from eligible response.
- Public delivery status creates an enumeration oracle.
- Challenge is not bound to browser and link generation.
- Unlink/relink cannot safely invalidate in the same transaction.
- Real PostgreSQL concurrency cannot be deterministically tested.
- Session rotation cannot be reused independently of password verification.
- Tests require skip/xfail, assertion weakening, SQLite, or create_all.
- Secret/OTP/phone/chat/session identifier leakage is found.
- New business/security semantics not approved here are required.
