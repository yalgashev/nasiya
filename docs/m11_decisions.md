# Nasiya M11 Decisions

Status: authoritative M11 repository decision log, CR-M11-03 recovery-amended.
Authority: TT, M11 Final Scope Freeze, `CR-M11-01 — FINAL APPROVED`, then
`CR-M11-02 — FINAL APPROVED`, then `CR-M11-03 — FINAL APPROVED` for the
narrow checkpoint-truth and bounded R09 correction.
Product Owner disposition: `PO-M11-01..25 — 25/25 FINAL APPROVED`.

These decisions are closed. Each later micro-task implements only its assigned
slice and may not reopen product, transaction, security, privacy or test scope.

## Baseline Decision

M11 starts at clean/synced M10 docs-only closeout
`17ebbe166d63a32e3b7eaa3eb3838f578d9b7780`. M10 implementation
`b79250858a3f6a63908a288f891d5dad1126dd48` is its direct parent; Actions run
`30705134413` succeeded with `2735 passed` and zero non-pass outcomes. The
single Alembic parent is `b0c1d2e3f4a5`. Protected TT blob is
`d77c0f0f330a1330155a4aee3c46b05d97cf5561`; protected freeze and CR SHA-256
values are recorded in `docs/m11_scope_contract.md`.

## CR-M11-01 Decision

The pre-CR M11.03 audit found inherited `Dispatch -> Challenge` versus
`Challenge -> Dispatch` and `Link -> Challenge` inversions. Those findings are
the reason for CR-M11-01 and are not blockers after correction.

Auth/session-touch and rate-limit pre-phases close independently. Every domain
transaction follows:

```text
OtpDispatch -> OtpChallenge -> User -> TelegramLink -> Customer
-> OfferVersion -> OfferAcceptance -> CustomerIdentity
-> ObjectFile -> CustomerDocument -> AuthSession
```

Same-class locks are UUID ascending; trusted non-locking discovery is always
followed by locked revalidation. CR-M11-01 permitted a link-token pre-anchor;
CR-M11-02 below makes it mandatory for every shared token/later-class
transaction. Missing rows may be skipped without later reverse acquisition.
No retry/sleep/timeout, advisory framework, second dispatcher, or semantic
weakening is allowed.

## CR-M11-02 Decision

M11.R01 proved that current chat-control linking does not prove equality with
`users.phone`, and that consume took `TelegramLinkToken -> OtpDispatch` while
unlink could take `OtpDispatch -> ... -> TelegramLinkToken`. CR-M11-02 makes
the token pre-anchor mandatory whenever token and later classes share a
transaction:

```text
TelegramLinkToken -> OtpDispatch -> OtpChallenge -> User -> TelegramLink
-> Customer -> OfferVersion -> OfferAcceptance -> CustomerIdentity
-> ObjectFile -> CustomerDocument -> AuthSession
```

Same-class rows are UUID ascending. `/start` may lock token rows and stop.
Every OTP-capable link must be active and satisfy
`phone_verified_at IS NOT NULL AND phone_verified_at = linked_at`. Legacy
links are visible but unverified. Self-contact is private, sender-owned, bound
to one live token, and canonical-phone equal. Raw contact phone and pending
Telegram identities are never persisted. Mismatch is generic zero-write;
success is atomic; fixed bot replies are delivered only after Session closure.

## CR-M11-03 Decision

The repository has seven original committed M11 implementation checkpoints.
The absent `M11: complete customer activation foundation` subject is not
fabricated. One bounded CR-M11-02/03 recovery implementation commit follows
those seven, making eight M11 implementation commits in total. CR-M11-03 also
authorizes R09 to close the owner-aware OTP-link and auth/rate/domain Session
gaps without reopening R02–R08 capability scope.

Every OTP-sensitive policy call receives a mandatory server-derived expected
user and checks exact link ownership plus the verified generation. Auth/session
touch and every rate-limit check/record/clear transaction close before the
ordered OTP or Telegram domain transaction; no ORM instance crosses phases.

## Final Product Owner Decisions

| ID | FINAL repository consequence |
|---|---|
| PO-M11-01 | One capability ends at existing-customer activation. |
| PO-M11-02 | Session user is the only actor/ownership authority. |
| PO-M11-03 | Activation never invokes customer draft upsert. |
| PO-M11-04 | Purpose remains typed/server-selected; LOGIN code/MAC/lifecycle stay unchanged while link eligibility is strengthened. |
| PO-M11-05 | M7 generator/MAC/browser binding/lifecycle/dispatcher are preserved; OTP authority requires a phone-verified link generation. |
| PO-M11-06 | Exact registration defaults/scopes reuse the existing limiter table and keying. |
| PO-M11-07 | Readiness includes active phone-verified Telegram generation; failure is zero challenge/dispatch/event write. |
| PO-M11-08 | Only safe exact IDs/revision/link time/digest form the challenge snapshot. |
| PO-M11-09 | Valid accepted-language evidence is ordered `accepted_at,id`. |
| PO-M11-10 | Identity port authenticates M10 envelope/blind index and returns only revision. |
| PO-M11-11 | Document port returns exact current document after object-before-document recheck. |
| PO-M11-12 | Live mismatch invalidates before candidate MAC and does not consume an attempt. |
| PO-M11-13 | Dispatcher branches on persisted purpose; network boundary stays session-free. |
| PO-M11-14 | Customer remains PII-free and gains one timestamp plus two-state checks. |
| PO-M11-15 | CR-M11-01/02 jointly define the executable order; token is the mandatory first class whenever touched with later classes. |
| PO-M11-16 | Activation mutation/event/audit/rotation is indivisible. |
| PO-M11-17 | PostgreSQL barriers prove one winner and convergent loser state. |
| PO-M11-18 | Active state makes issue/new-code/verify an audit-free, rotation-free no-op. |
| PO-M11-19 | Rotation copies `active_shop_id`, preserves other sessions, and locks current session last. |
| PO-M11-20 | Active unlink is denied; protected same-phone relink/re-verification remains atomic. |
| PO-M11-21 | Future offer changes do not mutate an already active customer. |
| PO-M11-22 | Route family is fixed and has no identifier-bearing path/form/query authority. |
| PO-M11-23 | Stable errors and audit allowlists are exact and forbidden values never reach sinks. |
| PO-M11-24 | Original `c1d2e3f4a5b6` plus one bounded linear recovery child; no new table/dependency. |
| PO-M11-25 | Seven original committed checkpoints remain immutable; one bounded CR-M11-02/03 recovery implementation commit makes eight total M11 implementation commits, and no commit, push or closeout proceeds on a non-GREEN gate. |

## Repository Decisions

### RD-M11-01 — Narrow Placement

`app/customer_activation/` owns activation-only contracts, readiness,
coordinator/service, presentation, router and copy. `app/otp/` receives only
typed purpose/context, purpose-aware repository/issue/verify/dispatch
extensions. `app/customer/`, `app/offers/`, `app/customer_identity/`,
`app/customer_document/`, `app/storage/`, `app/auth/`, `app/telegram/` and
`app/audit/` receive bounded adapters or allowlist extensions only. There is
no generic activation/contact/markup registry or base, table, worker or queue.
Telegram receives only redacted typed sender/contact parsing, an exact binding
MAC boundary, fixed request-contact/removal markup, and verified lifecycle
extensions in its existing worker/service/repository seams.

### RD-M11-02 — Server-Owned Actor And Browser Context

The activation command is built only from `CurrentSessionContext`, current
authenticated `User`, trusted client IP and current session-derived browser
digest. Purpose and every domain/session UUID are absent from public command
input. The verify form contains only CSRF and code; new-code/request contain
only CSRF.

### RD-M11-03 — Purpose-Aware MAC Without Formula Change

`OtpPurpose.REGISTRATION` is added to the exact enum. The canonical M7 payload
and version are unchanged; purpose remains a typed field inside the MAC.
LOGIN golden vectors stay pinned and cross-purpose substitution fails.
`hmac.compare_digest` remains the only comparison.

### RD-M11-04 — Readiness Ports Return Minimum Evidence

Offer locking uses `SqlAlchemyOfferVersionRepository.lock_versions_for_purpose`
before an acceptance selector locks valid evidence rows ordered by
`accepted_at,id`. The identity adapter locks and authenticates the existing
record and returns only a positive revision. Document candidate discovery is
non-locking; the adapter locks ObjectFile before CustomerDocument and returns
only the current document ID after allowed/AVAILABLE revalidation. Every
readiness entry first requires an active link whose non-null
`phone_verified_at` exactly equals `linked_at`.

### RD-M11-05 — Snapshot Is Persistence Context, Not Public State

Four nullable context columns are added to `otp_challenges`. LOGIN rows keep
them null; REGISTRATION requires them plus inherited real user/link state.
The existing `telegram_linked_at` remains the complete verified-generation
snapshot; no contact phone, identity or binding MAC enters a challenge.
Snapshot IDs never appear in URL, form, HTML, audit payload, errors or repr.

### RD-M11-06 — Rate Pre-Phase

Registration rate check-and-record uses a short outer-owned transaction before
expensive readiness work. Account phone/user/IP are server-derived and HMAC-
bucketed. A single allowed request records exactly once across all three
buckets; a blocked request records no new attempt and creates no OTP state.

### RD-M11-07 — Purpose-Local Issuance

Issue and new-code discover/lock dispatches before challenges, then follow the
global order and require the same verified-link predicate for LOGIN and
REGISTRATION. Only outstanding REGISTRATION rows are expired/superseded/
cancelled. LOGIN may coexist. Insert conflicts use existing named-constraint
savepoints and never full-roll back a borrowed Session.

### RD-M11-08 — Durable Dispatch Reuse

Preparation derives MAC, TTL and message type from the persisted challenge
purpose. REGISTRATION preparation rechecks active user, exact link generation
with `phone_verified_at = linked_at`, and draft customer. LOGIN preparation
uses the same verified-generation rule. Provider send stays outside DB; TX-D2
records only typed result and safe event. UNKNOWN is terminally reconcilable
without auto-resend.

### RD-M11-09 — Verify Rechecks State Before OTP

Candidate resolution is browser+REGISTRATION only. The locked live snapshot is
compared before parsing/comparing the submitted code. Stale state records
`INVALIDATED_BY_REGISTRATION_STATE_CHANGE`, not VERIFY_FAILED, and does not
increment attempts. LOGIN and REGISTRATION both recheck active phone-verified
generation. Re-verification rotates `linked_at`, so older challenges retain
the inherited generic link-state invalidation semantics.

### RD-M11-10 — One-Way Customer Transition

`Customer` gains exact `draft|active` constants and `activated_at`. A narrow
locked transition requires draft and aware UTC time, sets status/timestamps
once, and has no reverse method. Missing customer is not created. M10 draft-
only mutation resolvers continue to reject active state.

### RD-M11-11 — Atomic Activation Coordinator

The route owns one function-scoped Session transaction. Correct verification
consumes challenge, appends OTP event, transitions customer, appends central
audit, then locks and rotates the trusted current AuthSession. Any exception
before dependency commit rolls back all. Repository/service never commits,
full-rolls back or closes the borrowed Session.

### RD-M11-12 — Current-Only Rotation

A dedicated activation adapter locks current AuthSession last by trusted
context ID and user, requires it active/unrevoked/unexpired, revokes it and
creates a replacement with new token/CSRF. It preserves normalized safe user
agent and `active_shop_id`; other sessions are untouched. Raw token is carried
only by a redacted result to cookie preparation.

### RD-M11-13 — Cookie Commit Ordering

The route prepares redirect and secure cookie after all DB mutations but
before returning. The function-scoped dependency commits before response send;
commit failure prevents the prepared response from being sent. Cookie
preparation failure raises before commit and triggers rollback. Already-active
and replay results contain no replacement token/cookie.

### RD-M11-14 — Active Link Invariant

All affected token rows are discovered and locked UUID-ascending before every
affected dispatch/challenge row, then User/Link/Customer. Ordinary unlink
denies active state before mutation and mutates only the already-locked token
set; permitted unlink clears verification. Protected relink/re-verification
requires matching self-contact, preserves the old verified link on mismatch or
collision, and invalidates both OTP purposes after a successful verified
generation change.

### RD-M11-15 — Migration And Downgrade

Original migration `c1d2e3f4a5b6` modifies only customers, otp_challenges,
otp_challenge_events and audit constraints. Downgrade refuses to proceed if an
active row exists; it never silently rewrites active to draft. Real PostgreSQL
walks cover empty and populated states. Recovery revision `d2e3f4a5b6c7` is
its sole zero-table child and adds only two pending-token columns, one link
verification column, their checks/index, and the canonical user-phone check.
Legacy links remain null/unverified. Its downgrade refuses pending or verified
state and removes only recovery objects.

### RD-M11-16 — Safe Audit And Errors

Central audit adds only `customer.activated -> customer` with exact three-key
payload. OTP journal remains a typed action journal with no metadata. Existing
Telegram link events are appended only on verified success. The original four
M11 errors plus `TELEGRAM_CONTACT_REQUIRED`, `TELEGRAM_PHONE_MISMATCH`, and
`TELEGRAM_PHONE_NOT_VERIFIED` have feature-local UZ-Latn/RU copy and expose no
prerequisite, identifier, secret or provider detail.

### RD-M11-17 — Web Boundary

Four fixed authenticated routes are composed once. GET is side-effect-free;
POSTs are CSRF+PRG. All responses are no-store, CSP/autoescape safe and
identifier-free. Profile receives only PII-free activation discovery/status.

### RD-M11-18 — Test Boundary

The original 48 unique threats/16 exact files and the 22-row CR-M11-02
recovery matrix in `docs/m11_repository_map.md` are mandatory. PostgreSQL
barrier tests patch only deterministic lock boundaries and never sleep. Static
guards reject inverse locks, transaction ownership, web Telegram calls,
forbidden sinks, SQLite/create_all and OUT scope.

### RD-M11-19 — Pending Contact Binding

`/start` no longer links. It persists only a domain-separated lowercase
HMAC-SHA-256 binding and contact-request timestamp under token-only locks,
commits, then sends one fixed contact keyboard. The existing redacted
`RATE_LIMIT_HMAC_KEY` and standard library are reused; no raw pending Telegram
identity, phone, new secret or arbitrary reply markup is allowed.

### RD-M11-20 — Contact Success And Mismatch

Contact requires private chat, present sender/contact IDs, exact self-contact,
one bound live token, and exact canonical ASCII equality with locked
`users.phone`. Mismatch is generic zero-write and preserves a prior verified
link. Success consumes/clears the token, writes one verified generation,
invalidates both OTP purposes, and appends the existing link event atomically;
the success/keyboard-removal send follows transaction closure.

### RD-M11-21 — Legacy And Phone Policy

Every legacy link receives `phone_verified_at = NULL` and is ineligible for
LOGIN/REGISTRATION OTP and activation. Stored user phones are exact ASCII
Uzbekistan E.164; upgrade validates without rewrite. A future phone-mutation
capability is OUT and would require clearing verification and OTP state.

### RD-M11-22 — Recovery History And Acceptance

The seven original committed implementation checkpoints are not rewritten.
Recovery is one bounded CR-M11-02/03 implementation commit, producing eight
total M11 implementation commits, followed by exact-SHA CI and docs-only
closure. Real Telegram acceptance uses a controlled same-phone development
account and sanitized outcomes/counts. A mismatching contact leaves its token
pending, so the operator explicitly issues a replacement token before the
controlled same-phone account verifies. Active protected acceptance uses the
same account/phone rather than the old unrelated-account A→B scenario. Random
fixture phones cannot establish real activation authority.

### RD-M11-23 — Owner, Phase And Future-Phone Boundary

LOGIN and REGISTRATION OTP eligibility is owner-aware by construction: every
OTP-sensitive edge supplies a server-derived expected user, verifies exact
`TelegramLink.user_id`, and verifies the active phone-verified generation.
Authentication/session-touch and all rate operations finish in closed
pre-phases before domain locks. The product has no current `User.phone`
mutation flow; any future authorized flow must atomically invalidate Telegram
phone verification and stale outstanding LOGIN and REGISTRATION OTP under the
FINAL global order.

## Stop Decisions

Stop rather than infer if the protected sources must change; CR order cannot
be implemented within existing M1–M10 semantics; a new table, dependency,
worker, dispatcher or infrastructure component is required; public bootstrap,
shop linkage or another OUT feature becomes necessary; a required gate or
external acceptance cannot become GREEN; raw contact phone or pending
chat/user persistence or a
token lock after `OtpDispatch` is required; self-contact equality cannot be
proved without weakening privacy; Telegram HTTP would overlap a Session;
legacy links would need to be trusted; or the working tree contains an
unexplained change. No bypass by role, amend, force push, rebase/squash, hidden
CI failure, skip/xfail or assertion weakening is an accepted recovery.
