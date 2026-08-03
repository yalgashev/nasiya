# Nasiya M11 Scope Contract

Status: authoritative M11 repository scope, CR-M11-03 recovery-amended.
Capability: `Registration OTP & Existing-Customer Activation Foundation`.
Product Owner disposition: `PO-M11-01..25 — 25/25 FINAL APPROVED`.
Correction: `CR-M11-01 — FINAL APPROVED` replaces the inherited lock-order
assumption with the executable global order below.
Recovery correction: `CR-M11-02 — FINAL APPROVED` strengthens Telegram-link
trust to exact self-phone verification and authorizes one bounded recovery
migration. `CR-M11-03 — FINAL APPROVED` corrects M11 history to seven original
committed checkpoints followed by one bounded CR-M11-02/03 recovery
implementation commit; it does not alter M10's independent `8/8` evidence.

This document, `docs/m11_decisions.md`, and `docs/m11_repository_map.md` are
the executable authority for every task after M11.08. A required deviation is
a stop condition; it is not permission to infer another capability.

## Authority And Exact Baseline

Conflicts are resolved in this order:

1. `docs/tt_nasiya_web_v1.md`;
2. `/home/yalgashev/projects/nasiya_m11_cr_03_recovery_completion_correction.md`
   for its narrow checkpoint-truth and R09 recovery amendment;
3. `/home/yalgashev/projects/nasiya_m11_00_final_scope_freeze.md`;
4. `/home/yalgashev/projects/nasiya_m11_cr_01_global_lock_order.md`;
5. `/home/yalgashev/projects/nasiya_m11_cr_02_telegram_self_phone_verification.md`;
6. this contract, `docs/m11_decisions.md`, and
   `docs/m11_repository_map.md`;
7. inherited M2/M3/M4/M6/M7/M9/M10 closed contracts;
8. repository implementation and tests as integration evidence.

| Evidence | Exact value |
|---|---|
| M10 docs-only closeout and M11 baseline | `17ebbe166d63a32e3b7eaa3eb3838f578d9b7780` (`docs: close M10 remote evidence`) |
| M10 implementation | `b79250858a3f6a63908a288f891d5dad1126dd48` |
| M10 remote CI | GitHub Actions `30705134413`, success |
| M10 full baseline | `2735 passed`; zero failed/skipped/xfailed/xpassed |
| M10 implementation checkpoints | `8/8` |
| Alembic single head and M11 parent | `b0c1d2e3f4a5` |
| TT Git blob | `d77c0f0f330a1330155a4aee3c46b05d97cf5561` |
| M11 product gate SHA-256 | `17ded4cacee9f80728139feee91c67451570be66a9d76f604d0be2346f83b9f9` |
| Original M11 guide SHA-256 | `f9a7109a4439ea889cc982210e01ae069489606d3d6103cc57e1a88c1fd1f7d5` |
| M11 freeze SHA-256 | `48de725166daaa07e2a0998bca1e907caedc6050cd3ad8740b8a34d3d79ce8e0` |
| CR-M11-01 SHA-256 | `08668a326d682a175cc62366b1ca7092963f02457c3cb6f876cfab08f812a526` |
| CR-M11-02 SHA-256 | `562556c2462828db8bfff2747096dbe929ded7c499626bfc13a75ee1524395c3` |
| CR-M11-02 recovery guide SHA-256 | `68badd200a843148f48de8ffbfe0530502b2f7230210959a9cf6c4f99d0f94a7` |
| CR-M11-03 SHA-256 | `a2bf649887f7d7701a26cd518b8f8876dd0a85b0198a326f9327a0c02ff3d1e2` |

M11.01 verified clean/synced `main`, divergence `0 0`, implementation ancestry,
the docs-only delta, single Alembic head, unchanged TT, and no M11 product
code/schema/migration. M11.02–07 are read-only readiness evidence.

## CR-M11-01/02 Global Lock Order

Authentication/session-touch and rate-limit check-and-record are independent
pre-phases. Each commits and closes before a domain transaction begins. The
single forward domain order is:

```text
[TelegramLinkToken]
-> OtpDispatch
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

`TelegramLinkToken` is omitted only when the transaction touches no token row.
Any transaction touching token rows plus OTP/link/customer rows locks every
required token row UUID-ascending first. It may never acquire a token row
after `OtpDispatch` or a later class. `/start` binding may lock only token rows
and stop. Any affected dispatches/challenges are then locked before
User/TelegramLink/Customer. Existing M9 `OfferVersion -> OfferAcceptance`, M10
`Customer -> CustomerIdentity`, and `Customer -> ObjectFile ->
CustomerDocument` orders are preserved. Telegram external I/O never overlaps
an SQLAlchemy Session or transaction.

## One Capability

An auth-active authenticated user with an own existing customer may request a
server-selected `REGISTRATION` OTP through the existing durable Telegram OTP
dispatcher. Exact current offer acceptance, complete M10 identity, current
available M10 document, browser binding, and active phone-verified Telegram-
link generation are snapshotted and rechecked. One correct verification
atomically consumes
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
- Private self-contact verification with a domain-separated pending binding
  MAC, no raw contact phone or pending chat/user persistence, and unchanged
  inherited verified-link `telegram_chat_id` storage.
- Legacy link denial for LOGIN/REGISTRATION OTP until successful same-phone
  verification.
- Active-customer ordinary-unlink denial and atomic protected same-phone
  relink/re-verification.
- Four fixed own-account activation routes, UZ-Latn/RU, CSRF, PRG, no-store,
  CSP, autoescape, mobile accessibility and safe fixed redirects.
- Original M11 migration plus one linear CR-M11-02 recovery child, zero new
  tables and zero direct runtime dependencies.
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
- raw Telegram contact phone or pending chat/user identity persistence;
- arbitrary caller-provided Telegram reply markup or generic keyboard API;
- generic session framework, global logout, shop-context reset, admin override;
- PII/secret/internal metadata in audit, logs, errors, HTML, URLs, reports,
  reprs or browser storage.

## Product Owner Decisions — 25/25 FINAL

| ID | Binding decision |
|---|---|
| PO-M11-01 | M11 is Registration OTP and existing-customer activation only. |
| PO-M11-02 | Eligibility is auth-active authenticated own existing customer; client/shop/admin selectors are not authority. |
| PO-M11-03 | Missing customer is never created; draft is candidate and active is an idempotent no-op. |
| PO-M11-04 | `REGISTRATION` remains server-selected and purpose-separated; LOGIN code/MAC/lifecycle semantics are preserved while its link eligibility is strengthened. |
| PO-M11-05 | Exact M7 code, HMAC, browser binding, lifecycle and dispatcher are reused; link-generation authority means a self-phone-verified generation. |
| PO-M11-06 | Registration defaults are `180/5/60/900/3/3/20` in distinct existing-table scopes. |
| PO-M11-07 | All readiness gates, including active phone-verified Telegram generation, precede challenge/dispatch/event creation. |
| PO-M11-08 | Challenge binds customer, acceptance, identity revision and document snapshots without PII. |
| PO-M11-09 | Earliest valid current acceptance is selected by `accepted_at ASC, id ASC`, independent of UI locale. |
| PO-M11-10 | Identity completeness authenticates M10 crypto and snapshots one positive exact revision. |
| PO-M11-11 | Document snapshot is the exact CURRENT row; its object remains AVAILABLE and allowed at verify. |
| PO-M11-12 | Snapshot/link state is rechecked before candidate comparison; mismatch invalidates without an attempt. |
| PO-M11-13 | Existing dispatcher handles REGISTRATION; web performs no Telegram network call. |
| PO-M11-14 | Customer adds only `draft | active` and `activated_at`; reverse transition is absent. |
| PO-M11-15 | CR-M11-01 plus CR-M11-02 supply one repository-audited order with `TelegramLinkToken` as the mandatory first class whenever token and later classes share a transaction. |
| PO-M11-16 | Consume, OTP event, activation, audit and current session/CSRF rotation are one transaction. |
| PO-M11-17 | Parallel correct verify has exactly one activation winner; losing paths do no duplicate mutation. |
| PO-M11-18 | Already-active request/verify is a no-op success with no OTP/audit/timestamp/rotation. |
| PO-M11-19 | First activation rotates only the current session/CSRF and preserves other sessions and `active_shop_id`. |
| PO-M11-20 | Active customer cannot ordinarily unlink Telegram; protected atomic same-phone relink/re-verification is allowed. |
| PO-M11-21 | Activation uses exact-current point-in-time legal evidence; later offer switch is not retroactive. |
| PO-M11-22 | Four fixed own-account routes provide identifier-free, localized, no-store mobile UI. |
| PO-M11-23 | Stable safe errors disclose no gate, PII, OTP, link, offer, document, session or provider detail. |
| PO-M11-24 | Original revision plus one bounded recovery revision; no new table or direct runtime dependency. |
| PO-M11-25 | Seven original committed checkpoints remain intact; one bounded CR-M11-02/03 recovery implementation commit completes eight total M11 implementation commits, followed by PostgreSQL/fake/manual/containment gates, exact-SHA CI and docs closure. |

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

Pending contact binding uses the existing redacted `RATE_LIMIT_HMAC_KEY` at a
narrow operation-local boundary and the standard-library HMAC-SHA-256
primitive. It adds no secret or dependency. The exact byte payload is:

```text
b"NASIYA-TELEGRAM-CONTACT-BINDING-V1\0"
+ ascii(private_chat_id)
+ b"\0"
+ ascii(sender_telegram_user_id)
```

Persistence receives only lowercase 64-hex MAC output. The contact phone is
held only by a redacted operation-local value object and is canonicalized to
exact ASCII `^\+998[0-9]{9}$`; non-ASCII decimal digits fail closed. The raw
phone, chat ID, sender ID, token, MAC and key never enter logs, errors, audit,
repr, HTML, URL, browser storage or reports.

## Readiness And Snapshot

Readiness requires: auth-active user; own customer in draft; active exact
phone-verified link generation where `phone_verified_at = linked_at`; current
REGISTRATION offer; at least one exact-current immutable
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

Original revision: `c1d2e3f4a5b6`, parent `b0c1d2e3f4a5`.

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

### CR-M11-02 Recovery Revision

Recovery revision `d2e3f4a5b6c7` is the sole child of `c1d2e3f4a5b6`. It
creates no table, enum, sequence, trigger, function or view and adds exactly
three nullable columns:

```text
telegram_link_tokens.pending_contact_binding_mac VARCHAR(64)
telegram_link_tokens.contact_requested_at        TIMESTAMPTZ
telegram_links.phone_verified_at                  TIMESTAMPTZ
```

Exact new schema objects are:

```text
ck_telegram_link_tokens_pending_contact_binding_mac_format
ck_telegram_link_tokens_pending_contact_state_consistent
ck_telegram_link_tokens_pending_contact_timestamp_order
uq_telegram_link_tokens_pending_contact_binding_mac_outstanding
ck_telegram_links_phone_verification_consistent
ck_users_phone_canonical_uz_e164
```

The pending MAC is either null or lowercase 64-hex; MAC/timestamp are both
null or both non-null; its timestamp is not before token creation; terminal
tokens have both fields null; and only one nonterminal row may carry a given
binding MAC. A verified link is active and has
`phone_verified_at = linked_at`. `users.phone` must match
`^\+998[0-9]{9}$`; upgrade reads and fails on any violation and never rewrites
a phone. Existing links remain `phone_verified_at = NULL` and are OTP-
ineligible. Downgrade fails before DDL while any pending binding or verified
link exists, then removes only CR-M11-02 objects after explicit cleanup.

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
user, active phone-verified link generation and draft customer before
REGISTRATION send. Full
offer/identity/document readiness is not moved to the dispatcher. Stale
PREPARED remains UNKNOWN and is never auto-resent.

LOGIN issue/new-code use the same closed-pre-phase rule: authentication and
session-touch close first, every `AuthRateLimit` check/record/clear closes in a
rate-only transaction, and only then may the OTP domain transaction lock its
ordered rows. No ORM instance crosses those Session boundaries. All LOGIN and
REGISTRATION issue/new-code/dispatch/verify/readiness/activation edges use a
mandatory owner-aware link policy with a server-derived expected user. An
eligible link has a chat, belongs to that expected user, is active, satisfies
`phone_verified_at = linked_at`, and, where snapshotted, matches the exact
`linked_at` generation.

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

## `/start` And Self-Contact Boundary

A private `/start <token>` transaction parses both chat and sender, computes
the binding MAC, locks affected token rows UUID-ascending, invalidates a prior
nonterminal pending row for the same binding, and writes only pending MAC plus
`contact_requested_at`. It neither consumes the token nor creates/relinks a
link. After transaction/session closure, the worker sends one fixed
`request_contact=true` keyboard; arbitrary reply markup is impossible.

A contact update is accepted only for a private chat with `message.from.id`, a
contact with `contact.user_id`, exact sender/contact user equality, one live
token bound to that chat/sender, and exact canonical ASCII equality between
the operation-local contact phone and locked `users.phone`. Candidate lookup
may be non-locking; success re-locks and revalidates under the global order.

Malformed contact or mismatch returns one generic localized outcome and makes
zero token/link/OTP/customer/session/audit/event/attempt mutation. The pending
token remains usable until its existing TTL or explicit replacement.
Successful contact atomically consumes and clears the token, creates or
protected-relinks a verified generation, invalidates outstanding LOGIN and
REGISTRATION OTP state once, and appends the existing bounded link event.
Keyboard removal/success delivery occurs only after transaction closure.

## Active Telegram Invariant

Link changes first discover and lock all affected `TelegramLinkToken` rows
UUID-ascending, then all affected dispatches and challenges UUID-ascending
before User, TelegramLink and Customer. No later token lock is permitted.
Draft or missing-customer unlink/relink behavior stays inherited. Active ordinary
unlink returns `TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER` with zero writes.
Protected atomic relink/re-verification succeeds only after matching self-
contact, rotates `linked_at`, sets the same `phone_verified_at`, and invalidates
all outstanding LOGIN and REGISTRATION challenges. A mismatch or collision
preserves the old verified link. M11 never deactivates the customer.

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

Original M11 public error codes are `OTP_INVALID`,
`REGISTRATION_OFFER_NOT_ACCEPTED`, `CUSTOMER_ACTIVATION_CHANGED`, and
`TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER`. CR-M11-02 adds exactly
`TELEGRAM_CONTACT_REQUIRED`, `TELEGRAM_PHONE_MISMATCH`, and
`TELEGRAM_PHONE_NOT_VERIFIED`. All are fixed UZ-Latn/RU, reveal no phone or
Telegram identity, and carry no dynamic detail. Internal already-active is a
success outcome.

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

The original 48-threat/16-file matrix and CR-M11-02 recovery matrix are in
`docs/m11_repository_map.md`. Integration/concurrency/migration tests use real
PostgreSQL and deterministic barriers. Telegram uses an injected fake; real
Telegram appears only in authorized manual acceptance. No SQLite,
`create_all`, manual DDL, sleep-based concurrency, skip/xfail/xpass, or weakened
assertion is permitted.

The seven original committed checkpoints are immutable ancestry. One bounded
CR-M11-02/03 recovery implementation commit makes eight total M11
implementation commits. Recovery is done only after full local tests,
controlled same-phone Chrome/Telegram acceptance,
exact recovery-SHA remote CI success, and a docs-only closeout whose CI/path-
filter result is verified. Manual evidence must include different-phone
rejection; an explicitly issued replacement token that invalidates the still-
pending mismatched attempt; matching self-contact verification by the
controlled same-phone account; four stale-generation cases; exactly-once
activation/replay; ordinary active-unlink denial; and active same-account,
same-phone protected re-verification. M11 stops if
TT/freeze/CR must change; a new product/security decision, table, dependency,
infrastructure component or OUT capability is required; the lock order cannot
be implemented without weakening M1–M10; required external evidence remains
unavailable; raw contact/Telegram identity would need persistence; a token row
would be acquired after `OtpDispatch`; or any exact gate is not GREEN.
