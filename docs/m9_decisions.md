# Nasiya M9 Decisions

Status: authoritative M9 repository decision log.
Source authority: `/home/yalgashev/projects/m9_00_final_scope_freeze.md`,
as amended only by FINAL `CR-M9-01`.
Product Owner disposition: `PO-M9-01..16 — 16/16 FINAL APPROVED`.

## Baseline Decision

M9 starts from clean, synced `main` at
`5429e950d0ef25dcb99617e7ca109b1aa08fc697`, the M8 docs-only closeout.
The final M8 implementation/recovery SHA
`af611b0d546479d1f21075d9b37fac748a71fc1e` and eighth checkpoint
`3481be0491f87a2ad64d1a65d6d41eedbb00a8a3` are ancestors. M8 CI run
`30565830042` succeeded with `2167 passed` and no
failed/skipped/xfailed/xpassed outcomes. M9's only Alembic parent is
`f8a9b0c1d2e3`.

## Post-Freeze Product Owner Correction — CR-M9-01

Status: `FINAL APPROVED` on 2026-07-31.

The M9.08 audit confirmed that the baseline has no central append-only
audit/redaction authority. The Product Owner approved exactly one supporting
`audit_log` table and a narrow transaction-aware append/redaction boundary
inside M9. This supersedes only the original assumption that the authority
already existed and the resulting prohibition on its missing table.

The correction preserves:

- the one legal-offer capability;
- exactly three offer-domain tables;
- all PO-M9-01..16 decisions;
- one linear M9 Alembic revision and eight checkpoint subjects;
- caller-owned transactions and same-transaction audit;
- no new direct runtime dependency;
- no audit UI/read/search/export/retention/purge, generic admin, CMS, outbox,
  notification, scheduler, or arbitrary event ingestion.

## Final Product Owner Decisions

| ID | FINAL decision | Binding repository consequence |
|---|---|---|
| PO-M9-01 | Milestone name is `Legal Offer Lifecycle & Registration Acceptance Foundation`. | Customer activation and PII are not M9 capabilities. |
| PO-M9-02 | Each purpose has one `CURRENT` version; UZ-Latn, UZ-Cyrl, and RU are variants of that version. | Languages never run independent current lifecycles. |
| PO-M9-03 | `APPROVED` and `CURRENT` require non-empty canonical title/body/hash for all three legal languages. | Partial versions fail `OFFER_INCOMPLETE`. |
| PO-M9-04 | Making a new target current demotes the old current to approved in the same transaction; an already-current target is an audit-free no-op. | Current switching is atomic, concurrency-safe, and replay-safe. |
| PO-M9-05 | Approved/current content and approval evidence are immutable; corrections use a new draft. | Historical legal and acceptance evidence cannot be rewritten. |
| PO-M9-06 | Only `CRLF/CR -> LF` normalization is allowed; all other Unicode/whitespace is preserved. Hash is domain-prefixed and eight-byte big-endian length-prefixed over canonical UTF-8 title/body. | Hashing has one deterministic algorithm and no trim/NFC/collapse. |
| PO-M9-07 | Offer list/detail and lifecycle mutations are platform-admin-only. Approval requires reviewer/authority, reviewed UTC time, and bounded reference from an external legal review. Existing guard/bootstrap is reused or only its missing minimum is added. | A shop role, button press, AI output, or test fixture is not legal approval; no admin suite is created. |
| PO-M9-08 | M9 acceptance is only for an authenticated account and `REGISTRATION`; accepting one current legal-language variant is sufficient. | No public registration or debt acceptance runtime. |
| PO-M9-09 | Acceptance POST server-resolves and exactly checks current purpose/version/text/language/hash. | A stale or mismatched form returns `OFFER_CHANGED` and writes nothing. |
| PO-M9-10 | One actor replaying one exact offer text converges through DB uniqueness and service handling. | One immutable acceptance and one audit event at most. |
| PO-M9-11 | Reuse the bounded User-Agent policy or add only an equivalent narrow helper. Raw/unbounded UA stays out of audit/log/error. | Persistence stores at most 512 normalized characters. |
| PO-M9-12 | Automated tests use synthetic legal text. Development seed, if any, remains draft; production approval/current requires real external-review evidence. | Test data never claims production legal approval. |
| PO-M9-13 | UI shell supports UZ-Latn and RU; legal resolver supports UZ-Latn, UZ-Cyrl, and RU. UI locale and accepted legal language are independent. | No mandatory UZ-Cyrl interface and no locale-derived acceptance. |
| PO-M9-14 | Keep `OFFER_UNAVAILABLE`; add `OFFER_CHANGED`, `OFFER_INCOMPLETE`, `OFFER_NOT_DRAFT`, `OFFER_NOT_APPROVED`, and `LEGAL_REVIEW_EVIDENCE_REQUIRED`. | Errors are stable, localized, deterministic, and content-safe. |
| PO-M9-15 | Repository/service uses caller-owned transactions and never commits, fully rolls back, or closes the session. | Route/coordinator owns the outer transaction. |
| PO-M9-16 | M9 closeout states that registration, activation, PII, customer documents, and `shop_customer` are unstarted. | M10/M11 scope is not pulled forward. |

## Repository Reconciliation Decisions

### RD-M9-01 — Package And Dependency Boundary

M9 code belongs in narrow `app/offers/` and CR-M9-01 `app/audit/` packages
plus explicit composition changes in existing auth/main/Alembic surfaces. Python `hashlib`,
`struct`/integer byte conversion, `unicodedata`, and existing dependencies
are sufficient. No new direct runtime dependency, generic base repository,
registry, CMS, event bus, outbox, or empty placeholder module is approved.

### RD-M9-02 — Purpose, Language, And State Storage

Purpose/language/status are Python `StrEnum` values stored as bounded strings
with explicit PostgreSQL check constraints, matching current project style.
No PostgreSQL enum type is introduced. Exact values are:

```text
REGISTRATION, DEBT_ACCEPTANCE
UZ_LATN, UZ_CYRL, RU
DRAFT, APPROVED, CURRENT
```

`DEBT_ACCEPTANCE` is schema/domain vocabulary only in M9; no runtime flow may
accept it.

### RD-M9-03 — Canonicalization And Hash

Canonicalization is a pure domain operation. It replaces line endings only,
rejects empty/whitespace-only values, preserves every other code point, and
computes lowercase SHA-256 over:

```text
"NASIYA-OFFER-TEXT-V1\0"
+ uint64_be(title_utf8_length) + title_utf8
+ uint64_be(body_utf8_length) + body_utf8
```

Neither routes, models, repositories, nor templates may implement a second
hash algorithm.

### RD-M9-04 — Three Domain Tables And One Audit Support Table

M9 creates exactly `offer_versions`, `offer_texts`, and
`offer_acceptances`. Historical FKs use `RESTRICT`; there is no delete route.
Uniqueness is enforced for `(purpose, version_number)`,
`(offer_version_id, language)`, current `purpose`, and
`(user_id, offer_text_id, purpose)`. All timestamps are aware UTC
`timestamptz`; all identities are PostgreSQL UUIDs.

Approval evidence bounds are exact: reviewer/authority is 1–200 Unicode code
points after outer trim and contains no control characters; reference is
1–200 characters and matches
`[A-Za-z0-9][A-Za-z0-9._ -]{0,199}`. `legal_reviewed_at` is aware UTC,
not future relative to injected `now`, and not after `approved_at`.

CR-M9-01 additionally creates exactly `audit_log`: UUID `id`, aware UTC
`occurred_at`, bounded `event_type`, `actor_kind`, nullable `actor_user_id`
referencing `users` with `RESTRICT`, bounded `object_type`, UUID `object_id`,
and JSONB object `payload`. A check requires a user actor for offer events and
a null system actor for initial bootstrap. It has insert-only application APIs
and no read/update/delete route or repository. The M9 event/object registries
and payload allowlists are exact in the scope contract.

### RD-M9-05 — Minimal Platform-Admin Foundation

The repository has no platform-admin identity, guard, or bootstrap.
`ShopRole` is tenant-scoped and must not be reused. PO-M9-07 authorizes the
minimum:

- add `User.is_platform_admin`, non-null boolean, default false;
- add an offer-scoped dependency that returns `PlatformAdminActor` only for
  an active authenticated platform admin;
- require that typed actor and re-check its active platform-admin row at the
  service boundary;
- add an operator-only first-admin CLI command for one existing active user,
  allowed only while the platform-admin count is zero. The command locks all
  user rows in stable UUID order before counting to serialize concurrent
  bootstrap attempts and appends the SYSTEM
  `platform_admin.bootstrapped` event in the same transaction.

This is one `users` column, not a role table or admin-management feature.
Granting a second admin, revocation, last-admin protection, admin creation UI,
impersonation, and global admin navigation remain OUT scope.

### RD-M9-06 — Current-Switch Concurrency

Every make-current form carries the nullable current version identity observed
by the server. In one caller-owned transaction, the service locks all version
rows for the purpose in stable UUID order. It returns no-op if target is
already current and `OFFER_CHANGED` if observed current differs from the
submitted expected identity. Only then does it demote/promote and append
audit. This yields one winner for competing requests.

`uq_offer_versions_current_purpose`, a PostgreSQL partial unique index on
`purpose WHERE status = 'CURRENT'`, is the last defense. Expected constraint
handling uses `Session.begin_nested()` and exact `diag.constraint_name`;
there is no full rollback or blind retry.

### RD-M9-07 — Exact Acceptance Authority

The browser may submit only `language` and displayed `offer_text_id`.
The service share-locks and re-loads current `REGISTRATION` plus the selected
variant. Missing current/variant is `OFFER_UNAVAILABLE`; any mismatch is
`OFFER_CHANGED`. Both are zero-write outcomes.

On success the service snapshots only server-loaded version/text/purpose/
language/hash values, injected UTC time, and normalized UA. Exact replay
returns the existing row and does not append another event. UI locale changes
do not affect the legal language already accepted.

### RD-M9-08 — User-Agent Evidence

`app.auth.user_agent:truncate_user_agent` supplies the inherited 512-character
bound but does not remove control characters. M9 therefore adds one narrow
normalizer: inspect only the first 512 input code points, replace Unicode
control characters with spaces, collapse whitespace, strip the ends, and map
empty to `None`. No parser/fingerprinting dependency is added. The normalized
value is evidence persistence only and is excluded from audit/log/error/repr.

### RD-M9-09 — Error And Localization Placement

Stable offer codes extend `app.auth.error_codes:ErrorCode`; HTTP mappings are
409 for unavailable/changed/not-draft/not-approved and 422 for incomplete/
review-evidence-required. M9 presentation owns UZ-Latn/RU message maps.
Internal codes are never rendered as the only user message.

The repository has no shared profile locale. M9 follows the existing pure
presentation resolver style with UZ-Latn default and `Accept-Language`
fallback. Legal language remains a separate explicit three-value selection.

### RD-M9-10 — Exact Web Routes

The approved route set is:

```text
GET  /admin/offers
GET  /admin/offers/new
POST /admin/offers
GET  /admin/offers/{offer_version_id}
POST /admin/offers/{offer_version_id}/texts/{language}
POST /admin/offers/{offer_version_id}/approve
POST /admin/offers/{offer_version_id}/make-current
GET  /auth/registration-offer
POST /auth/registration-offer/accept
```

All POSTs are CSRF-protected and PRG. All responses are no-store. Full legal
text appears only in platform-admin detail or authenticated exact-current
view; all other read models contain safe metadata.

### RD-M9-11 — Minimal Central Audit Foundation

The repository contains domain-specific shop, Telegram, and OTP event tables,
but no TT-compliant central audit authority. CR-M9-01 authorizes the exact
minimum in `app/audit/`:

- `AuditEvent`, with typed event/actor/object/time and candidate safe metadata;
- `redact_audit_payload`, with a fixed per-event key allowlist, bounded scalar
  values, unknown-key removal, and no pre-serialized JSON input;
- `AuditLog`, mapped only to the approved `audit_log` support table;
- `append_audit_event(session, event)`, which adds/flushes in the caller's
  transaction and never commits, fully rolls back, closes, logs, or performs
  external I/O.

Only the seven M9 event names and four object types in the scope contract are
accepted. There is no read/query port, mutation API, UI, export, retention,
purge, generic event registry, or best-effort logger fallback.

### RD-M9-12 — Audit/Privacy Payload

Permitted fields are safe actor/object UUIDs, purpose, status, language,
version number, hash, and bounded review authority/reference. Forbidden
fields are title/body, raw UA, phone/JSHSHIR/passport or other PII, IP, URL,
object key, token, cookie, session ID, CSRF, raw form, exception, SQL, and
provider detail. Audit append failure must roll back the associated mutation.

### RD-M9-13 — Transaction Ownership

Request/coordinator owns commit/full rollback/close. Repository and service
may flush and may use a savepoint for named expected constraints. External
I/O is absent from M9 transactions. Audit must be SQL-transactional; a logger
or best-effort call cannot satisfy it.

### RD-M9-14 — Rendering And Cache Safety

Legal content is plain text under Jinja autoescape. `Markup`, `|safe`, inline
script, event-handler rendering, and raw HTML are forbidden. Existing CSP and
security-header middleware is retained. Admin/legal pages and every error,
redirect, and fragment on those flows receive `Cache-Control: no-store`.

### RD-M9-15 — PostgreSQL-Only Evidence

M9 migration, constraint, service, replay, race, and rollback tests run
against the existing real PostgreSQL fixtures. Empty database and populated
`M8 -> M9 -> M8 -> M9` walks are mandatory. SQLite, `create_all`, skip,
xfail, assertion weakening, and production legal text are forbidden.
Migration inspection must find exactly the three offer-domain tables,
`audit_log`, and the approved `users.is_platform_admin` column, with no other
M9 schema object.

### RD-M9-16 — No Silent Scope Expansion

M9 cannot implement registration OTP, activation, PII, documents, object
attachment, shop linking, owner application, debt/payment/rating/disclosure,
notification, scheduler, generic CMS, or full admin management. Discovery
that one is required is a blocker and scope-review trigger, not permission.

## Readiness Disposition

`GAP-M9-AUDIT` is resolved by FINAL `CR-M9-01`. The authority is not yet
implemented, so later persistence/service tasks must build and prove it
before claiming their checkpoints green. At the scope/readiness level there
is no unresolved authority decision and no permission to exceed the exact
minimal foundation above.
