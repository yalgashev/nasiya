# Nasiya M11 Scope Contract

Status: authoritative M11 repository scope, pre-implementation.
Capability: `Registration OTP & Existing-Customer Activation Foundation`.
Product Owner disposition: `PO-M11-01..25 — 25/25 FINAL APPROVED`.
Correction: `CR-M11-01 — FINAL APPROVED` replaces the inherited lock-order
assumption with the executable global order below.

This document, `docs/m11_decisions.md`, and `docs/m11_repository_map.md` are
the executable authority for every task after M11.08. A required deviation is
a stop condition; it is not permission to infer another capability.

## Authority And Exact Baseline

Conflicts are resolved in this order:

1. `docs/tt_nasiya_web_v1.md`;
2. `/home/yalgashev/projects/nasiya_m11_00_final_scope_freeze.md`;
3. `/home/yalgashev/projects/nasiya_m11_cr_01_global_lock_order.md`;
4. this contract, `docs/m11_decisions.md`, and
   `docs/m11_repository_map.md`;
5. inherited M2/M3/M4/M6/M7/M9/M10 closed contracts;
6. repository implementation and tests as integration evidence.

| Evidence | Exact value |
|---|---|
| M10 docs-only closeout and M11 baseline | `17ebbe166d63a32e3b7eaa3eb3838f578d9b7780` (`docs: close M10 remote evidence`) |
| M10 implementation | `b79250858a3f6a63908a288f891d5dad1126dd48` |
| M10 remote CI | GitHub Actions `30705134413`, success |
| M10 full baseline | `2735 passed`; zero failed/skipped/xfailed/xpassed |
| M10 implementation checkpoints | `8/8` |
| Alembic single head and M11 parent | `b0c1d2e3f4a5` |
| TT Git blob | `d77c0f0f330a1330155a4aee3c46b05d97cf5561` |
| M11 freeze SHA-256 | `48de725166daaa07e2a0998bca1e907caedc6050cd3ad8740b8a34d3d79ce8e0` |
| CR-M11-01 SHA-256 | `08668a326d682a175cc62366b1ca7092963f02457c3cb6f876cfab08f812a526` |

M11.01 verified clean/synced `main`, divergence `0 0`, implementation ancestry,
the docs-only delta, single Alembic head, unchanged TT, and no M11 product
code/schema/migration. M11.02–07 are read-only readiness evidence.

## CR-M11-01 Global Lock Order

Authentication/session-touch and rate-limit check-and-record are independent
pre-phases. Each commits and closes before a domain transaction begins. The
single forward domain order is:

```text
OtpDispatch
-> OtpChallenge
-> User
-> TelegramLink
-> Customer
-> OfferVersion
-> OfferAcceptance
-> CustomerIdentity
-> ObjectFile
-> CustomerDocument
-> AuthSession
```

Rows of one class are locked in UUID ascending order. Non-locking candidate
discovery may find trusted IDs, but every locked set is revalidated. A missing
dispatch may be skipped; no dispatch may be acquired after its challenge.
`AuthSession` is always last. Retry, sleep, lock timeout, or advisory locks are
not correctness mechanisms.

Link-token consumption may retain its one-time `TelegramLinkToken` pre-anchor.
Any affected dispatches/challenges are then locked before User/TelegramLink/
Customer. Existing M9 `OfferVersion -> OfferAcceptance`, M10
`Customer -> CustomerIdentity`, and `Customer -> ObjectFile ->
CustomerDocument` orders are preserved. Telegram external I/O never overlaps
an SQLAlchemy Session or transaction.

## One Capability

An auth-active authenticated user with an own existing customer may request a
server-selected `REGISTRATION` OTP through the existing durable Telegram OTP
dispatcher. Exact current offer acceptance, complete M10 identity, current
available M10 document, browser binding, and active Telegram-link generation
are snapshotted and rechecked. One correct verification atomically consumes
the OTP, changes `draft -> active`, writes the OTP event and central audit, and
rotates only the current session/CSRF while preserving safe session context.

## Exact IN Scope

- Existing authenticated account and server-resolved own existing customer.
- Exact OTP purpose set `LOGIN | REGISTRATION`; LOGIN semantics remain stable.
- Existing six-ASCII-digit generator, M7 MAC formula, browser binding,
  lifecycle, event journal, durable dispatch and Telegram provider.
- Registration-specific settings and existing-table HMAC rate-limit scopes.
- Issue-time readiness plus exact safe challenge snapshot.
- Deterministic earliest exact-current acceptance evidence.
- Positive authenticated identity revision and exact current available image.
- Purpose-local issue, new-code, verification, invalidation, attempts and burn.
- Customer `draft | active`, nullable `activated_at`, one-way transition.
- Atomic consume/event/activation/audit/current-session rotation.
- Active-customer ordinary-unlink denial and atomic protected relink.
- Four fixed own-account activation routes, UZ-Latn/RU, CSRF, PRG, no-store,
  CSP, autoescape, mobile accessibility and safe fixed redirects.
- One linear migration, zero new tables, zero direct runtime dependencies.
- Real PostgreSQL, injected fake Telegram transport, manual Chrome/Telegram,
  full containment, exact pushed-SHA CI, and docs-only closure evidence.

## Exact OUT Scope

M11 must not implement or scaffold:

- public/anonymous registration or account/customer creation;
- public Telegram link bootstrap, customer lead, shop-assisted onboarding,
  `shop_customer`, or cross-customer activation;
- debt, payment, rating, disclosure, notification or scheduler;
- SMS, Web Push, alternate OTP transport, generic OTP/activation framework;
- another worker, dispatcher, broker, outbox, queue, table or infrastructure;
- OCR/MRZ, biometric/selfie, government registry, document approval;
- deactivation, `active -> draft`, active identity/document correction;
- re-consent, retroactive deactivation, acceptance replacement or legal-policy
  workflow after a later offer switch;
- Telegram HTTP from web requests or raw/reversible OTP persistence;
- generic session framework, global logout, shop-context reset, admin override;
- PII/secret/internal metadata in audit, logs, errors, HTML, URLs, reports,
  reprs or browser storage.

## Product Owner Decisions — 25/25 FINAL

| ID | Binding decision |
|---|---|
| PO-M11-01 | M11 is Registration OTP and existing-customer activation only. |
| PO-M11-02 | Eligibility is auth-active authenticated own existing customer; client/shop/admin selectors are not authority. |
| PO-M11-03 | Missing customer is never created; draft is candidate and active is an idempotent no-op. |
| PO-M11-04 | `REGISTRATION` is server-selected and purpose-separated from preserved `LOGIN`. |
| PO-M11-05 | Exact M7 code, HMAC, browser/link binding, lifecycle and dispatcher are reused. |
| PO-M11-06 | Registration defaults are `180/5/60/900/3/3/20` in distinct existing-table scopes. |
| PO-M11-07 | All readiness gates precede challenge/dispatch/event creation. |
| PO-M11-08 | Challenge binds customer, acceptance, identity revision and document snapshots without PII. |
| PO-M11-09 | Earliest valid current acceptance is selected by `accepted_at ASC, id ASC`, independent of UI locale. |
| PO-M11-10 | Identity completeness authenticates M10 crypto and snapshots one positive exact revision. |
| PO-M11-11 | Document snapshot is the exact CURRENT row; its object remains AVAILABLE and allowed at verify. |
| PO-M11-12 | Snapshot/link state is rechecked before candidate comparison; mismatch invalidates without an attempt. |
| PO-M11-13 | Existing dispatcher handles REGISTRATION; web performs no Telegram network call. |
| PO-M11-14 | Customer adds only `draft | active` and `activated_at`; reverse transition is absent. |
| PO-M11-15 | CR-M11-01 supplies one repository-audited global lock order. |
| PO-M11-16 | Consume, OTP event, activation, audit and current session/CSRF rotation are one transaction. |
| PO-M11-17 | Parallel correct verify has exactly one activation winner; losing paths do no duplicate mutation. |
| PO-M11-18 | Already-active request/verify is a no-op success with no OTP/audit/timestamp/rotation. |
| PO-M11-19 | First activation rotates only the current session/CSRF and preserves other sessions and `active_shop_id`. |
| PO-M11-20 | Active customer cannot ordinarily unlink Telegram; protected atomic relink is allowed. |
| PO-M11-21 | Activation uses exact-current point-in-time legal evidence; later offer switch is not retroactive. |
| PO-M11-22 | Four fixed own-account routes provide identifier-free, localized, no-store mobile UI. |
| PO-M11-23 | Stable safe errors disclose no gate, PII, OTP, link, offer, document, session or provider detail. |
| PO-M11-24 | One migration, no new table, no new direct runtime dependency. |
| PO-M11-25 | Eight checkpoints, PostgreSQL/fake/manual/containment gates, exact-SHA CI and docs closure are mandatory. |

## Purpose, Crypto, Settings And Rates

`OtpPurpose` contains exactly `LOGIN` and `REGISTRATION`. The existing M7
canonical MAC remains versioned and purpose-domain-separated; `LOGIN` golden
vectors do not change. Codes match `^[0-9]{6}$`, retain leading zeroes, and
are compared only through `hmac.compare_digest`. The existing `OTP_HMAC_KEY`
is reused; no fallback or second key is added.

Exact settings and defaults:

```text
OTP_REGISTRATION_TTL_SECONDS=180                 # 60..600
OTP_REGISTRATION_MAX_VERIFY_ATTEMPTS=5           # 1..10
OTP_REGISTRATION_RESEND_COOLDOWN_SECONDS=60      # positive and < TTL
OTP_REGISTRATION_RATE_LIMIT_WINDOW_SECONDS=900   # positive
OTP_REGISTRATION_RATE_LIMIT_PHONE_ATTEMPTS=3     # positive
OTP_REGISTRATION_RATE_LIMIT_USER_ATTEMPTS=3      # positive
OTP_REGISTRATION_RATE_LIMIT_IP_ATTEMPTS=20       # positive
```

Invalid operation config fails closed with redacted errors. Base startup stays
independent. Account phone and user are server-derived; IP uses the inherited
trusted-proxy resolver. The existing `auth_rate_limits` table and HMAC bucket
keying are reused under exact scope prefix `otp-registration-issue` for phone,
user, and IP. One POST records once; a blocked pre-check records nothing.

## Readiness And Snapshot

Readiness requires: auth-active user; own customer in draft; active exact link
generation; current REGISTRATION offer; at least one exact-current immutable
acceptance; authenticated complete M10 identity with positive revision; and
exactly one CURRENT document whose M8 image object is AVAILABLE and has an
allowed content type.

The server-only snapshot is:

```text
user_id
customer_id
telegram_link_id
telegram_linked_at
registration_offer_acceptance_id
customer_identity_revision
customer_document_id
browser_binding_digest
```

Its repr redacts all identifiers/digest. It contains no phone, IP, chat ID,
offer text/hash/language, PII, crypto value, object ID/metadata or URL.
Readiness GET is side-effect-free. Issue and verify use locks and revalidation.

## Schema And Migration

Reserved revision: `c1d2e3f4a5b6`, parent `b0c1d2e3f4a5`.

`customers` adds only nullable `activated_at TIMESTAMPTZ`; status is exactly
`draft | active`. Exact replacement checks are:

```text
ck_customers_onboarding_status_allowed
ck_customers_activation_state_consistent
ck_customers_timestamp_order
```

Draft requires null `activated_at`; active requires non-null. All timestamps
are ordered and aware UTC. Existing rows remain draft/null.

`otp_challenges` adds nullable `customer_id`,
`registration_offer_acceptance_id`, `customer_identity_revision`, and
`customer_document_id`. Exact new/replacement names:

```text
ck_otp_challenges_purpose_allowed
ck_otp_challenges_registration_context_matches_purpose
fk_otp_challenges_customer_id_customers_id
fk_otp_challenges_registration_acceptance_offer_acceptances
fk_otp_challenges_customer_document_id_customer_documents
```

LOGIN requires all four context columns null. REGISTRATION requires real
user/link generation plus all four context columns and positive revision.
The existing purpose-bearing partial unique indexes remain unchanged.

`ck_otp_challenge_events_action_allowed` additionally allows
`INVALIDATED_BY_REGISTRATION_STATE_CHANGE`. Existing audit checks with names
`ck_audit_log_event_type_allowed`, `ck_audit_log_object_type_allowed`,
`ck_audit_log_object_matches_event`, and
`ck_audit_log_payload_exact_shape` add only `customer.activated -> customer`
and its exact payload.

The migration adds no table, enum, sequence, trigger, function, view or data
rewrite. Downgrade first fails closed if any active customer exists; otherwise
it drops only M11 additions and restores the exact M10 checks. Empty base→head
and populated M10→M11→M10→M11 walks run against real PostgreSQL.

## Issue, New Code And Dispatcher

Auth/session-touch and CSRF/browser context resolve first. Registration rate
check-and-record completes in its own short transaction. The issue transaction
then follows CR-M11-01, requires/revalidates readiness, supersedes only prior
REGISTRATION state, creates one PENDING_DISPATCH challenge, one PENDING
dispatch and one ISSUED event, and commits before returning PRG. Failure of any
readiness gate creates none of those rows.

New-code is server-resolved by browser binding plus REGISTRATION, observes the
60-second cooldown, and uses the same readiness/snapshot pipeline. LOGIN and
REGISTRATION coexist and never supersede, verify, burn or rate-limit each
other.

The existing dispatcher remains TX-D1 prepare/commit, session-free external
Telegram send, TX-D2 result/event/commit. It derives purpose from persistence,
uses purpose-specific TTL and a bounded registration message containing only
code, TTL, warning and ignore-if-not-requested text. It rechecks auth-active
user, link generation and draft customer before REGISTRATION send. Full
offer/identity/document readiness is not moved to the dispatcher. Stale
PREPARED remains UNKNOWN and is never auto-resent.

## Verification, Activation And Session Rotation

The server resolves a candidate using browser binding and REGISTRATION, never
a client UUID. It locks dispatch before challenge, then the remainder of the
global order. Snapshot/live state mismatch is checked before OTP comparison,
invalidates the challenge with the registration-state event, increments no
attempt, and performs no customer/audit/session change.

Malformed/wrong codes take a dummy/constant-form MAC path, then increment the
locked challenge and append VERIFY_FAILED. The fifth failure burns it and
appends BURNED. Correct verification atomically:

1. consumes the challenge and appends CONSUMED;
2. changes customer `draft -> active` and sets
   `activated_at = updated_at = now`;
3. appends exact central `customer.activated` audit;
4. locks/revokes only the current authenticated AuthSession last;
5. creates a replacement random session/CSRF while copying safe user-agent
   context and `active_shop_id`.

Other sessions remain. Replay/already-active does not rotate again. The route
prepares the cookie-bearing response inside the outer transaction; function-
scoped dependency teardown commits before response bytes are sent. Commit or
cookie preparation failure rolls back all DB changes and releases no response.

## Active Telegram Invariant

Link changes discover trusted affected OTP rows, then lock all dispatches and
challenges UUID-ascending before User, TelegramLink and Customer. Draft or
missing-customer unlink/relink behavior stays inherited. Active ordinary
unlink returns `TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER` with zero writes.
Protected atomic relink may change the link generation and invalidates all
outstanding LOGIN and REGISTRATION challenges. A collision preserves the old
active link. M11 never deactivates the customer.

## Audit, Errors And Routes

Exact central event/object pair:

```text
customer.activated -> customer
```

Exact payload:

```json
{"from_status":"draft","to_status":"active","activation_method":"TELEGRAM_REGISTRATION_OTP"}
```

No additional payload keys are accepted. Audit failure rolls back the entire
activation; no logger fallback exists.

New public error codes are exactly `OTP_INVALID`,
`REGISTRATION_OFFER_NOT_ACCEPTED`, `CUSTOMER_ACTIVATION_CHANGED`, and
`TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER`. Existing safe prerequisite codes are
reused. Internal already-active is a success outcome.

Exact routes:

```text
GET  /customer/activation
POST /customer/activation/otp/request
POST /customer/activation/otp/verify
POST /customer/activation/otp/new-code
```

All are authenticated own-user routes without authority IDs. Unsafe routes
require CSRF and use PRG. Every response is no-store and CSP/autoescape safe;
URLs/flash/HTML contain no forbidden values. UZ-Latn and RU have matching
feature-local keys with UZ-Latn fallback. OTP input is text with
`inputmode="numeric"`, `autocomplete="one-time-code"`, `maxlength="6"`.

## Tests, Definition Of Done And Stops

The exact 48-threat/16-file matrix is in
`docs/m11_repository_map.md`. Integration/concurrency/migration tests use real
PostgreSQL and deterministic barriers. Telegram uses an injected fake; real
Telegram appears only in authorized manual acceptance. No SQLite,
`create_all`, manual DDL, sleep-based concurrency, skip/xfail/xpass, or weakened
assertion is permitted.

M11 is done only after eight clean checkpoint commits, full local tests, manual
Chrome/Telegram acceptance, exact implementation-SHA remote CI success, and a
docs-only closeout whose CI/path-filter result is verified. M11 stops if
TT/freeze/CR must change; a new product/security decision, table, dependency,
infrastructure component or OUT capability is required; the lock order cannot
be implemented without weakening M1–M10; required external evidence remains
unavailable; or any exact gate is not GREEN.
