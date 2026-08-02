# Nasiya M11 Decisions

Status: authoritative M11 repository decision log, pre-implementation.
Authority: TT, M11 Final Scope Freeze, then `CR-M11-01 — FINAL APPROVED`.
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
followed by locked revalidation. A link token may be a pre-anchor. Missing
rows may be skipped without later reverse acquisition. No retry/sleep/timeout,
advisory framework, second dispatcher, or semantic weakening is allowed.

## Final Product Owner Decisions

| ID | FINAL repository consequence |
|---|---|
| PO-M11-01 | One capability ends at existing-customer activation. |
| PO-M11-02 | Session user is the only actor/ownership authority. |
| PO-M11-03 | Activation never invokes customer draft upsert. |
| PO-M11-04 | Purpose is typed and server-selected; LOGIN stays unchanged. |
| PO-M11-05 | M7 generator/MAC/binding/lifecycle/dispatcher are extended, not replaced. |
| PO-M11-06 | Exact registration defaults/scopes reuse the existing limiter table and keying. |
| PO-M11-07 | Readiness failure is zero challenge/dispatch/event write. |
| PO-M11-08 | Only safe exact IDs/revision/link time/digest form the challenge snapshot. |
| PO-M11-09 | Valid accepted-language evidence is ordered `accepted_at,id`. |
| PO-M11-10 | Identity port authenticates M10 envelope/blind index and returns only revision. |
| PO-M11-11 | Document port returns exact current document after object-before-document recheck. |
| PO-M11-12 | Live mismatch invalidates before candidate MAC and does not consume an attempt. |
| PO-M11-13 | Dispatcher branches on persisted purpose; network boundary stays session-free. |
| PO-M11-14 | Customer remains PII-free and gains one timestamp plus two-state checks. |
| PO-M11-15 | CR-M11-01 is the sole executable lock-order authority. |
| PO-M11-16 | Activation mutation/event/audit/rotation is indivisible. |
| PO-M11-17 | PostgreSQL barriers prove one winner and convergent loser state. |
| PO-M11-18 | Active state makes issue/new-code/verify an audit-free, rotation-free no-op. |
| PO-M11-19 | Rotation copies `active_shop_id`, preserves other sessions, and locks current session last. |
| PO-M11-20 | Active unlink is denied; protected relink remains atomic. |
| PO-M11-21 | Future offer changes do not mutate an already active customer. |
| PO-M11-22 | Route family is fixed and has no identifier-bearing path/form/query authority. |
| PO-M11-23 | Stable errors and audit allowlists are exact and forbidden values never reach sinks. |
| PO-M11-24 | Revision `c1d2e3f4a5b6` is the only migration; no new table/dependency. |
| PO-M11-25 | No checkpoint, push or closeout proceeds on a non-GREEN gate. |

## Repository Decisions

### RD-M11-01 — Narrow Placement

`app/customer_activation/` owns activation-only contracts, readiness,
coordinator/service, presentation, router and copy. `app/otp/` receives only
typed purpose/context, purpose-aware repository/issue/verify/dispatch
extensions. `app/customer/`, `app/offers/`, `app/customer_identity/`,
`app/customer_document/`, `app/storage/`, `app/auth/`, `app/telegram/` and
`app/audit/` receive bounded adapters or allowlist extensions only. There is
no generic activation registry/base, table, worker or queue.

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
only the current document ID after allowed/AVAILABLE revalidation.

### RD-M11-05 — Snapshot Is Persistence Context, Not Public State

Four nullable context columns are added to `otp_challenges`. LOGIN rows keep
them null; REGISTRATION requires them plus inherited real user/link state.
Snapshot IDs never appear in URL, form, HTML, audit payload, errors or repr.

### RD-M11-06 — Rate Pre-Phase

Registration rate check-and-record uses a short outer-owned transaction before
expensive readiness work. Account phone/user/IP are server-derived and HMAC-
bucketed. A single allowed request records exactly once across all three
buckets; a blocked request records no new attempt and creates no OTP state.

### RD-M11-07 — Purpose-Local Issuance

Issue and new-code discover/lock dispatches before challenges, then follow the
global order. Only outstanding REGISTRATION rows are expired/superseded/
cancelled. LOGIN may coexist. Insert conflicts use existing named-constraint
savepoints and never full-roll back a borrowed Session.

### RD-M11-08 — Durable Dispatch Reuse

Preparation derives MAC, TTL and message type from the persisted challenge
purpose. REGISTRATION preparation rechecks active user, exact link generation
and draft customer. Provider send stays outside DB; TX-D2 records only typed
result and safe event. UNKNOWN is terminally reconcilable without auto-resend.

### RD-M11-09 — Verify Rechecks State Before OTP

Candidate resolution is browser+REGISTRATION only. The locked live snapshot is
compared before parsing/comparing the submitted code. Stale state records
`INVALIDATED_BY_REGISTRATION_STATE_CHANGE`, not VERIFY_FAILED, and does not
increment attempts. Link generation mismatch retains inherited link-change
semantics.

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

All affected dispatch/challenge rows are discovered and locked first. Ordinary
unlink locks User/Link/Customer and denies active state before mutation. Token-
protected relink uses the allowed token pre-anchor, follows the same suffix,
preserves the old link on collision, and invalidates both OTP purposes after a
successful generation change.

### RD-M11-15 — Migration And Downgrade

Migration `c1d2e3f4a5b6` modifies only customers, otp_challenges,
otp_challenge_events and audit constraints. Downgrade refuses to proceed if an
active row exists; it never silently rewrites active to draft. Real PostgreSQL
walks cover empty and populated states.

### RD-M11-16 — Safe Audit And Errors

Central audit adds only `customer.activated -> customer` with exact three-key
payload. OTP journal remains a typed action journal with no metadata. Four new
stable errors have feature-local UZ-Latn/RU copy and expose no prerequisite,
identifier, secret or provider detail.

### RD-M11-17 — Web Boundary

Four fixed authenticated routes are composed once. GET is side-effect-free;
POSTs are CSRF+PRG. All responses are no-store, CSP/autoescape safe and
identifier-free. Profile receives only PII-free activation discovery/status.

### RD-M11-18 — Test Boundary

The 48 unique threats and 16 exact files in
`docs/m11_repository_map.md` are mandatory. PostgreSQL barrier tests patch
only deterministic lock boundaries and never sleep. Static guards reject
inverse locks, transaction ownership, web Telegram calls, forbidden sinks,
SQLite/create_all and OUT scope.

## Stop Decisions

Stop rather than infer if the protected sources must change; CR order cannot
be implemented within existing M1–M10 semantics; a new table, dependency,
worker, dispatcher or infrastructure component is required; public bootstrap,
shop linkage or another OUT feature becomes necessary; a required gate or
external acceptance cannot become GREEN; or the working tree contains an
unexplained change. No amend, force push, rebase/squash, hidden CI failure,
skip/xfail or assertion weakening is an accepted recovery.
