# Nasiya M9 Scope Contract

Status: authoritative repository contract for M9 implementation.
Milestone: M9 — Legal Offer Lifecycle & Registration Acceptance Foundation.
Date frozen in repository: 2026-07-31.
Source authority: `/home/yalgashev/projects/m9_00_final_scope_freeze.md`,
as amended only by FINAL `CR-M9-01` below.
This file narrows repository execution only; it does not replace
`docs/tt_nasiya_web_v1.md`.

## Post-Freeze Product Owner Correction — CR-M9-01

On 2026-07-31 the Product Owner explicitly approved `CR-M9-01` after the
M9.08 readiness audit proved that the baseline has no central append-only
audit/redaction authority. This correction supersedes only the freeze clauses
that assumed such an authority already existed and prohibited M9 from adding
its missing persistence.

M9 may add exactly one supporting `audit_log` table and one narrow,
transaction-aware audit/redaction boundary. The three offer-domain tables
remain unchanged. This support is part of the one legal-offer capability; it
does not authorize audit read UI, export, retention/purge, generic admin,
generic CMS, arbitrary event ingestion, notification, or a new runtime
dependency.

## Authority And Exact Baseline

Authority order is: tracked TT; approved M9.00 freeze as amended only by
FINAL `CR-M9-01`; the correction and remaining contracts in this file and
`docs/m9_decisions.md`; inherited M1–M8 contracts; then the real repository.
An implementation task stops rather than inventing semantics when those
sources cannot be reconciled.

| Evidence | Exact value |
|---|---|
| Repository | `/home/yalgashev/projects/nasiya` |
| Branch | `main` |
| M9 start / M8 docs-only closeout | `5429e950d0ef25dcb99617e7ca109b1aa08fc697` |
| M8 final implementation/recovery | `af611b0d546479d1f21075d9b37fac748a71fc1e` |
| M8 eighth implementation checkpoint | `3481be0491f87a2ad64d1a65d6d41eedbb00a8a3` |
| M8 implementation CI | GitHub Actions `30565830042`, success |
| M8 full suite | `2167 passed`; 0 failed/skipped/xfailed/xpassed |
| Alembic parent head | `f8a9b0c1d2e3` |
| M8 state | `M8 REMOTE GREEN — CLOSED` |
| Start sync state | `HEAD == origin/main`, divergence `0 0`, clean |
| TT Git blob / SHA-256 | `d77c0f0f330a1330155a4aee3c46b05d97cf5561` / `569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab` |
| Approved freeze SHA-256 | `1caf678e801c9de5b30ba053d62a25654b611334b4786e4aea9476ca50f892ac` |
| CR-M9-01 corrected guide SHA-256 | `c9470c649f99c2156530443b437fa25306ec4c2ae08450ee1f1891c22e08b818` |

M9.07 rechecked these values read-only. M9 product code, schema, dependency,
template, test, and CI work has not started at this baseline.

## One Capability

M9 implements exactly one capability:

> Manage one three-language legal-offer version through
> `DRAFT -> APPROVED -> CURRENT`, bind approval to external legal-review
> evidence, resolve the current offer fail-closed per purpose, and store an
> authenticated account's exact registration-offer acceptance as immutable
> evidence.

Acceptance does not register, activate, identify, or link a customer.

## Exact IN Scope

- `OfferPurpose`: `REGISTRATION` and future-only `DEBT_ACCEPTANCE`.
- `OfferLanguage`: `UZ_LATN`, `UZ_CYRL`, and `RU`.
- `OfferStatus`: `DRAFT`, `APPROVED`, and `CURRENT`.
- Three complete title/body/hash variants in every approved/current version.
- Draft creation and draft-only legal text edits.
- External legal-review evidence before `DRAFT -> APPROVED`.
- Immutable approved/current content and approval evidence.
- Atomic current replacement and at most one current version per purpose.
- Canonical title/body and deterministic SHA-256.
- Fail-closed current resolver.
- Authenticated-account acceptance of current `REGISTRATION` only.
- Immutable version/text/purpose/language/hash/time/bounded-UA evidence.
- Stale-form rejection and exact replay idempotency.
- Minimal platform-admin offer lifecycle UI and minimal account view/accept UI.
- Stable errors, UZ-Latn/RU UI copy, CSRF, PRG, no-store, autoescape, and CSP.
- Same-transaction redacted lifecycle/acceptance audit through the
  CR-M9-01-approved central boundary.
- Exactly three M9 offer-domain tables plus the CR-M9-01 `audit_log`
  supporting table, one linear Alembic revision, and real PostgreSQL tests.
- A minimal tenant-independent platform-admin identity/guard/bootstrap
  foundation because the audited repository has none.

## Exact OUT Scope

- Public registration, user creation flow, or `purpose=REGISTRATION` OTP.
- Customer activation, active-customer creation, or activation UI.
- JSHSHIR, passport, F.I.Sh., new PII, PII encryption, or identity proofing.
- `customer_document` or an M8 object-domain attachment.
- `shop_customer`, owner application, or owner approval.
- Debt acceptance/runtime, debt, payment, void, receipt, or idempotency
  platform.
- Rating, hard block, disclosure, reporting, export, or billing.
- Notification, outbox, Telegram legal notification, scheduler, or `job_run`.
- Generic CMS, generic attachment, generic admin suite, impersonation, or
  admin-management UI.
- Production legal copy generated or approved by AI, tests, fixtures, or
  evidence-free admin action.
- A fourth offer-domain table, a second support table, arbitrary audit event
  ingestion, SQLite fallback, `create_all()`, a new direct runtime
  dependency, or TT edits.
- Audit read/admin UI, export, search, retention/purge, or generic audit
  platform features beyond the CR-M9-01 append/redaction boundary.

## Product Owner Decisions

The complete consequences are authoritative in `docs/m9_decisions.md`.
The frozen decision set is:

| ID | Frozen rule |
|---|---|
| PO-M9-01 | Milestone is Legal Offer Lifecycle & Registration Acceptance Foundation. |
| PO-M9-02 | One current version per purpose; all three languages belong to that version. |
| PO-M9-03 | Approved/current requires complete UZ-Latn, UZ-Cyrl, and RU variants. |
| PO-M9-04 | Current replacement demotes/promotes atomically; same target is a no-op. |
| PO-M9-05 | Approved/current content and approval evidence are immutable. |
| PO-M9-06 | Only line endings normalize; hash uses the exact prefixed, length-prefixed payload below. |
| PO-M9-07 | Offer administration is platform-admin-only and approval requires external review evidence. |
| PO-M9-08 | M9 acceptance is authenticated `REGISTRATION` only; one legal language is sufficient. |
| PO-M9-09 | POST re-resolves exact current state; mismatch is `OFFER_CHANGED`. |
| PO-M9-10 | Exact replay converges to one immutable acceptance. |
| PO-M9-11 | User-Agent evidence is bounded; raw/unbounded UA never reaches audit/log/error. |
| PO-M9-12 | Tests use synthetic text; seed data stays draft. |
| PO-M9-13 | UI is UZ-Latn/RU; legal languages are UZ-Latn/UZ-Cyrl/RU and independent. |
| PO-M9-14 | The frozen stable offer error set is used. |
| PO-M9-15 | Repository/service is caller-transaction-owned. |
| PO-M9-16 | M9 closeout explicitly leaves activation, PII, documents, and shop linking unstarted. |

## Domain And Canonical Hash

Allowed transitions are only:

```text
DRAFT -> APPROVED
APPROVED -> CURRENT
CURRENT -> APPROVED  # only inside atomic replacement
```

Only `DRAFT` content may change. Empty or whitespace-only title/body is
invalid. `APPROVED` and `CURRENT` content and legal-review fields cannot be
updated. A correction is a new draft version.

Canonicalization replaces `CRLF` and lone `CR` with `LF`; it performs no
trim, whitespace collapse, Unicode normalization, or punctuation change.
For canonical UTF-8 title and body:

```text
prefix = ASCII "NASIYA-OFFER-TEXT-V1\0"
payload = prefix
        + uint64_be(len(title_bytes)) + title_bytes
        + uint64_be(len(body_bytes)) + body_bytes
content_hash = lowercase_sha256_hex(payload)
```

`uint64_be` is unsigned, eight-byte, big-endian. Full title/body may appear
only in intended offer-text persistence and an authorized detail/view
response. It must not appear in audit, log, exception, error body, report,
result DTO, or default `repr`.

## Proposed Persistence

The M9 offer-domain persistence surface is exactly:

| Table | Required responsibility and invariant |
|---|---|
| `offer_versions` | UUID identity; purpose; positive server-issued `version_number`; status; creator/approval/current actor and UTC timestamps; bounded immutable external-review fields; unique `(purpose, version_number)`; status/evidence checks. |
| `offer_texts` | UUID identity; `offer_version_id` with `ON DELETE RESTRICT`; language; canonical title/body; lowercase SHA-256; UTC timestamps; unique `(offer_version_id, language)`. |
| `offer_acceptances` | UUID identity; user/version/text FKs with `ON DELETE RESTRICT`; purpose/language/version/hash snapshot; server UTC `accepted_at`; normalized UA up to 512 characters; unique `(user_id, offer_text_id, purpose)`. |

CR-M9-01 adds exactly one supporting table:

| Table | Required responsibility and invariant |
|---|---|
| `audit_log` | UUID identity; aware UTC `occurred_at`; bounded allowlisted `event_type`; `actor_kind` (`USER`/`SYSTEM`); nullable actor user FK with `ON DELETE RESTRICT` and kind/actor consistency check; bounded allowlisted `object_type`; object UUID; JSONB object containing only event-specific safe metadata. Insert-only application boundary; no update/delete repository, route, or service. |

For M9, `event_type` is restricted to the seven events in the audit section
and `object_type` to `user`, `offer_version`, `offer_text`, or
`offer_acceptance`.
`payload` must be a JSON object. The central redaction policy builds payloads
from event-specific allowlists and drops unknown keys before persistence; it
never accepts a pre-serialized JSON document. Actor/object identities are
dedicated columns rather than duplicated payload fields.

The partial PostgreSQL unique index
`uq_offer_versions_current_purpose` applies to `purpose` where
`status = 'CURRENT'`. Check/unique/FK/index names are deterministic and tested
through PostgreSQL inspection. No cascade may destroy historical offer or
acceptance evidence; M9 has no delete/purge route.

Version allocation locks the existing purpose set in stable UUID order, uses
`max(version_number) + 1`, and lets
`uq_offer_versions_purpose_version_number` close the first-row/concurrent
insert race. Expected uniqueness conflicts run inside `Session.begin_nested()`
and are recognized only by exact PostgreSQL constraint name.

The approved minimal platform-admin foundation adds
`users.is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE`; it adds no fourth
offer-domain table and never derives platform authority from `ShopRole`. The
single migration must have `f8a9b0c1d2e3` as its only parent and downgrade
only `audit_log`, the three M9 offer tables, their schema objects, and this
column.

## Platform-Admin Authorization

The baseline has authenticated users and tenant shop roles, but no platform
role, guard, or bootstrap. The approved minimal solution is:

- `User.is_platform_admin` is the explicit global authority bit.
- An offer-scoped dependency constructs an opaque `PlatformAdminActor` only
  for an active authenticated user whose bit is true.
- Every admin GET/POST uses that server-side dependency; every lifecycle
  service accepts `PlatformAdminActor`, not a shop role, and re-checks the
  actor's active platform-admin row before mutation.
- Anonymous HTML follows the existing login redirect contract; authenticated
  non-admin returns safe `FORBIDDEN` with no object detail.
- A first-admin operator CLI promotes one existing active user only when no
  platform admin exists. It locks all user rows in stable UUID order before
  counting, so concurrent bootstrap attempts cannot grant two first admins.
  It appends `platform_admin.bootstrapped` as a SYSTEM event in the same
  outer transaction. It is not an admin creation UI and cannot grant a second
  admin.

Approval additionally requires a non-empty reviewer/authority of at most 200
Unicode code points without control characters, an aware UTC reviewed time
not after injected `now`, and a 1–200 character reference matching
`[A-Za-z0-9][A-Za-z0-9._ -]{0,199}`. The approving admin is not the external
review authority merely by pressing the button.

## Transaction, Locking, And Current Switch

Request routes/coordinators own the outer transaction through
`app.db:create_database_session_dependency`. Repositories and services may
add/flush and may use a savepoint for an expected constraint, but never
commit, fully roll back, or close the caller session.

`make_current` receives the target and the server-rendered
`expected_current_version_id` (nullable). Inside one outer transaction it:

1. authorizes the platform actor;
2. locks every `offer_versions` row for the target purpose in stable UUID
   order with `FOR UPDATE`;
3. returns an audit-free no-op if the target is already current;
4. compares the locked current identity with the expected identity and
   returns `OFFER_CHANGED` without mutation on mismatch;
5. verifies target `APPROVED`, immutable review evidence, and three complete
   text variants;
6. demotes the old current and promotes the target;
7. appends redacted audit events in that same transaction;
8. flushes under the partial unique constraint; the outer owner commits.

This expected-current check gives two concurrent switch requests one winner;
the loser observes changed state and cannot silently replace the winner.
The partial unique index is the final database defense. No external I/O,
retry loop, notification, or full rollback is allowed.

Acceptance acquires a share-compatible lock on the resolved current version
before evidence creation. It is therefore either valid for the locked current
or, after a completed switch, fails stale; it cannot validate one current and
persist against another.

## Acceptance, Replay, And Stale Semantics

The client submits only selected `language` and displayed `offer_text_id`.
Hash, purpose, version, status, actor metadata, and timestamps are not client
authority.

The service requires an authenticated active user, resolves current
`REGISTRATION` plus the selected language on the server, and compares exact
version/text/language/hash identity. No current or missing selected variant is
`OFFER_UNAVAILABLE`. Any displayed-text mismatch is `OFFER_CHANGED`; neither
case writes acceptance or audit.

Success snapshots the server-loaded values and injected UTC time. One of the
three legal languages is sufficient. Exact sequential or concurrent replay
returns the existing row/no-op result and emits no second audit. The unique
conflict is isolated by `begin_nested()`, matched by exact constraint name,
then re-read. A later current version makes the old evidence historical; it
does not mutate or delete it.

UA evidence is derived from at most the first 512 input code points: Unicode
control characters are converted to spaces, consecutive whitespace is
collapsed, outer whitespace is removed, and empty becomes `None`. Only this
bounded normalized value is persisted; it is excluded from audit/log/error
and default `repr`.

## Stable Errors And Localization

| Code | HTTP meaning |
|---|---|
| `OFFER_UNAVAILABLE` | `409 Conflict` |
| `OFFER_CHANGED` | `409 Conflict` |
| `OFFER_INCOMPLETE` | `422 Unprocessable Entity` |
| `OFFER_NOT_DRAFT` | `409 Conflict` |
| `OFFER_NOT_APPROVED` | `409 Conflict` |
| `LEGAL_REVIEW_EVIDENCE_REQUIRED` | `422 Unprocessable Entity` |

Existing `UNAUTHORIZED`, `FORBIDDEN`, `VALIDATION_ERROR`, and `CSRF_FAILED`
contracts remain. Codes stay English and stable internally; UZ-Latn and RU
safe message maps are presentation data. UZ-Cyrl is a legal-content language,
not a required UI locale. UI locale and accepted legal language are separate
fields and never infer one another.

The repository has no shared application locale/profile primitive. M9 uses
an offer-local pure resolver following
`app.otp.web_presentation:resolve_otp_web_language`: supported UI values are
`uz` and `ru`, with `Accept-Language` fallback to UZ-Latn. Legal language is
an explicit three-value query/form choice and defaults to UZ-Latn only when
the user has made no selection.

## Central Audit And Redaction Contract

Required append-only events are:

```text
platform_admin.bootstrapped
offer.version_created
offer.text_updated
offer.version_approved
offer.version_made_current
offer.version_demoted
offer.registration_accepted
```

Actor kind/UUID, object type/UUID, and UTC occurrence time are dedicated
columns. Offer events require `actor_kind=USER` and a user UUID. The initial
operator bootstrap uses `actor_kind=SYSTEM`, a null actor UUID, and the
promoted user as its object. The exact JSON payload allowlists are:

| Event | Object type | Exact payload keys |
|---|---|---|
| `platform_admin.bootstrapped` | `user` | `bootstrap_method` |
| `offer.version_created` | `offer_version` | `purpose`, `version_number`, `status` |
| `offer.text_updated` | `offer_text` | `purpose`, `version_number`, `language`, `content_hash` |
| `offer.version_approved` | `offer_version` | `purpose`, `version_number`, `from_status`, `to_status`, `legal_review_authority`, `legal_review_reference`, `legal_reviewed_at` |
| `offer.version_made_current` | `offer_version` | `purpose`, `version_number`, `from_status`, `to_status`, `previous_current_version_id` |
| `offer.version_demoted` | `offer_version` | `purpose`, `version_number`, `from_status`, `to_status`, `replacement_version_id` |
| `offer.registration_accepted` | `offer_acceptance` | `purpose`, `offer_version_id`, `offer_text_id`, `version_number`, `language`, `content_hash` |

Enums use their frozen string values; hashes are lowercase 64-character
SHA-256; UUID payload values are canonical strings; review strings keep their
domain bounds; `legal_reviewed_at` is canonical UTC ISO-8601. Nullable
`previous_current_version_id` is the only nullable payload key. Unknown keys
are discarded, missing required keys or invalid values fail closed before a
row is added. `bootstrap_method` has the single value `operator_cli`.

Payload must not contain full title/body, raw UA, phone or other PII, IP,
token, cookie, session ID, CSRF, raw form, URL/object key, exception text, or
SQL detail. Audit append failure rolls back the business mutation through the
outer transaction.

The audited baseline has only domain-specific event tables. CR-M9-01 resolves
that readiness gap by authorizing `app/audit/` with:

- immutable typed `AuditEvent` input carrying event type, actor kind/identity,
  object, UTC time, and safe candidate metadata;
- event-specific `redact_audit_payload` allowlists that output a bounded JSON
  object and discard unknown keys;
- `append_audit_event(session, event)` that adds and flushes one `AuditLog`
  row without commit, full rollback, session close, logging, or external I/O;
- no query/list/read port, update/delete method, route, template, CLI, export,
  retention, or purge.

The seven M9 event names are the complete accepted registry for this milestone.
Adding another event name or payload key requires a later reviewed registry
change; callers cannot submit arbitrary event JSON.

## Exact Web Surface

| Method and path | Contract |
|---|---|
| `GET /admin/offers` | Platform-admin list; safe metadata only. |
| `GET /admin/offers/new` | Platform-admin draft form. |
| `POST /admin/offers` | Create draft; CSRF and PRG. |
| `GET /admin/offers/{offer_version_id}` | Authorized detail; the only admin response allowed full title/body. |
| `POST /admin/offers/{offer_version_id}/texts/{language}` | Draft-only canonical text upsert; CSRF and PRG. |
| `POST /admin/offers/{offer_version_id}/approve` | Complete version plus external-review evidence; CSRF and PRG. |
| `POST /admin/offers/{offer_version_id}/make-current` | Expected-current atomic switch; CSRF and PRG. |
| `GET /auth/registration-offer` | Authenticated exact current text for selected legal language. |
| `POST /auth/registration-offer/accept` | Exact-current acceptance; CSRF and PRG. |

All responses are no-store. Jinja autoescape is mandatory; legal text is
plain text, never `Markup`, `|safe`, inline script, or raw HTML. The existing
CSP/security middleware remains. Forms have labels, textual errors, visible
focus, at least 44px actions, and readable 320–430px layouts.

## Required Threat-To-Test Matrix

These exact planned tests are gates, not claims that the files already exist:

| Threat | Required test |
|---|---|
| Unauthorized approval | `tests/test_offer_authorization.py::test_non_platform_admin_cannot_approve_or_make_current` |
| Partial translations | `tests/test_offer_lifecycle_service.py::test_incomplete_three_language_version_cannot_approve_or_make_current` |
| Stale acceptance | `tests/test_offer_acceptance_postgresql.py::test_stale_version_text_language_or_hash_is_offer_changed` |
| Duplicate current | `tests/test_offer_db_constraints.py::test_partial_unique_index_rejects_second_current_per_purpose` |
| Replay acceptance | `tests/test_offer_acceptance_postgresql.py::test_exact_acceptance_replay_returns_single_immutable_row` |
| Content tampering | `tests/test_offer_immutability.py::test_approved_current_content_evidence_are_immutable_and_hash_mismatch_fails_closed` |
| XSS | `tests/test_offer_web_xss.py::test_legal_title_body_are_autoescaped_without_safe_or_inline_script` |
| CSRF | `tests/test_offer_csrf_matrix.py::test_all_offer_mutations_reject_missing_wrong_cross_session_csrf` |
| Audit leakage | `tests/test_offer_sensitive_data_audit.py::test_offer_logs_errors_audit_and_repr_omit_body_title_pii_token_session_csrf` |
| Race | `tests/test_offer_current_concurrency_postgresql.py::test_parallel_switches_have_one_winner_and_one_current` |
| Migration rollback | `tests/test_offer_migration_postgresql.py::test_m8_m9_m8_m9_walk_preserves_inherited_schema_and_data` |

Automated integration tests use only real PostgreSQL through the existing
fixtures. No SQLite, `create_all`, skip/xfail, weakened assertion, production
legal text, or networked legal review is permitted. M1–M8 regression and
containment remain mandatory.

## Stop Conditions

Stop the current implementation task without commit/push if baseline,
ancestry, TT blob, Alembic parent, platform authorization, the CR-M9-01
central audit/redaction contract, one-current locking/constraint, immutable evidence,
PostgreSQL determinism, no-leakage, or no-new-dependency gates cannot be met.
Also stop if activation, PII, documents, shop linking, debt, generic CMS, or
another OUT-scope capability is required.
