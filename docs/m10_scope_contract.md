# Nasiya M10 Scope Contract

Status: authoritative M10 repository scope, pre-implementation.
Capability: `PII-safe Customer Identity & Customer Document Attachment Foundation`.
Product Owner disposition: `PO-M10-01..20 — 20/20 FINAL APPROVED`.
Amendment: `CR-M10-01 — FINAL APPROVED` amends only PO-M10-14 and
PO-M10-15 as specified below.

This document is executable for every task after M10.08. A later task may
implement only what this contract, `docs/m10_decisions.md`, and
`docs/m10_repository_map.md` explicitly place in scope. Uncertainty or a
required deviation is a stop condition, not permission to infer a feature.

## Authority And Exact Baseline

Conflicts are resolved in this order:

1. `docs/tt_nasiya_web_v1.md`, authoritative product requirements;
2. `/home/yalgashev/projects/nasiya_m10_00_final_scope_freeze.md`, the
   authoritative M10 freeze;
3. `CR-M10-01 — FINAL APPROVED`, the executable amendment to PO-M10-14/15;
4. this contract, `docs/m10_decisions.md`, and
   `docs/m10_repository_map.md`;
5. inherited M3, M8, and M9 final contracts;
6. current repository implementation and tests as integration evidence.

The external freeze and TT remain protected inputs and are not edited by M10.
Their pinned identities are:

| Evidence | Exact value |
|---|---|
| M10 input baseline/docs-only M9 closeout | `f96b9f0a6d6b506f6715aa354cb4346199f1f5c5` |
| M9 implementation | `e2cda04920964cf383a749e07504539ccdafa0ab` |
| M9 remote CI | GitHub Actions `30645425078`, `success` |
| M9 full baseline | `2540 passed`; `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| M9 checkpoints | `8/8` |
| Alembic head/only M10 parent | `a9b0c1d2e3f4` |
| TT Git blob | `d77c0f0f330a1330155a4aee3c46b05d97cf5561` |
| External M10 freeze SHA-256 | `de766bc75752cd80f64e49081b5764a0bc7b3b2112366f1a5d11818a7ab3a462` |

M10.01 verified the baseline on clean, synced `main` with divergence `0 0`
and no M10 implementation. M10.02-M10.07 are readiness evidence in
`docs/m10_repository_map.md`; they do not themselves implement product code.

## CR-M10-01 — Authoritative PO-M10-14/15 Amendment

CR-M10-01 changes only rate-limit placement in PO-M10-14 and compensation
timing in PO-M10-15. The one capability, two-table boundary, all 20 FINAL
decisions, and eight implementation checkpoints otherwise remain unchanged.

The corrected upload/attach sequence is exactly:

```text
request envelope guard
authentication
CSRF

RL-CHECK:
  short session_factory.begin()
  check_storage_upload_rate_limit(...)
  blocked -> RATE_LIMITED
  no attempt record
commit/close

TX-A:
  lock own customer
  require existing draft
  inspect submission replay
  lock/read current customer-document rows
  compare expected_current_document_id
commit/close

M8 ingest_sanitized_image:
  record_storage_upload_attempt exactly once inside M8 ingest
  final rate enforcement before source read/sanitize/provider I/O
  return only the server-produced detached ingest result

TX-B:
  lock own customer
  lock server-returned object_file FOR UPDATE
  require AVAILABLE and globally unattached
  lock/recheck current customer-document rows
  recheck draft, replay, and expected current
  attach/supersede/audit atomically
commit/close

on any post-ingest TX-B failure, TX-C:
  lock object_file FOR UPDATE
  require status AVAILABLE
  prove NOT EXISTS customer_documents for object_file_id
  attached/replayed/status-changed/missing -> NOOP
  otherwise mark_object_file_delete_pending(failure_code=None)
commit/close

later:
  existing reconcile_stale_object_deletes performs provider deletion
```

RL-CHECK and M8 ingest use the same existing M8 user/IP buckets, keying,
settings, and `RATE_LIMITED` semantics. M10 never calls
`record_storage_upload_attempt` separately and creates no limiter, counter, or
scope. Request compensation performs no provider DELETE, imports no private
M8 `_delete_*` symbol, adds no public M8 delete API, and creates no scheduler.

TX-C's unattached proof and `AVAILABLE -> DELETE_PENDING` claim are one
transaction. An attach winner makes TX-C a no-op. A compensation winner makes
TX-B fail with `CUSTOMER_DOCUMENT_CHANGED` or a safe storage conflict and zero
attachment/audit. An attached object can never finish `DELETE_PENDING` or
`DELETED`.

## One Capability

M10 lets an active authenticated account save encrypted identity data for its
own already-existing M3 `draft` customer, attach and replace one current
sanitized passport/ID image through M8, view a masked no-store summary, and
obtain an authorized temporary GET for its own current document.

The capability ends there. Identity/document completeness does not register
or activate the customer and does not create a shop relationship.

## Exact IN Scope

- Server-resolved own existing draft customer; no customer selector input.
- Exact six-field canonical identity and encrypted 1:1 persistence.
- AES-256-GCM envelope through PyCA `AESGCM` and a versioned secret keyring.
- Dedicated HMAC-SHA-256 JSHSHIR blind index and fail-closed uniqueness.
- Optimistic identity revision with deterministic stale-write rejection.
- Concrete `customer_documents` attachment and current/superseded history.
- Submission replay and expected-current stale protection.
- Backend-mediated M8 ingest with the exact M8 sanitizer and lifecycle.
- CR-M10-01 RL-CHECK, object-row serialization, and TX-C orphan claim.
- Existing M8 reconciliation for eventual orphan provider cleanup.
- Own-current concrete-parent authorizer and default 300-second presigned GET.
- Non-activating identity and current-document completeness policies.
- Four exact central audit events and two exact audit object types.
- Four exact own-user routes, UZ-Latn/RU presentation, CSRF, PRG, CSP,
  autoescape, no-store, masking, and mobile accessibility.
- A localized, PII-free `/customer/identity` link and safe completion status on
  the existing authenticated customer profile; no new route or activation
  action.
- Real PostgreSQL tests; injected fake storage and approved local MinIO tests;
  exact M10.07 102-threat containment matrix.
- One linear Alembic revision whose sole parent is `a9b0c1d2e3f4`.
- Exactly one future reviewed direct dependency,
  `cryptography>=50.0.0,<51`, plus its exact `uv.lock` resolution.

## Exact OUT Scope

M10 must not implement or scaffold:

- public registration, public identity capture, or `REGISTRATION` OTP;
- customer creation, activation, `draft -> active`, or active correction;
- customer lead, `shop_customer`, or shop-assisted PII capture/access;
- debt, payment, rating, disclosure, notification, or scheduler;
- OCR/MRZ, selfie, biometric, face match, document authenticity, or
  government-registry verification;
- generic attachment/file-vault, CMS, full admin, audit UI, or KMS platform;
- admin/shop access to another customer's identity or document;
- raw browser-to-storage upload, presigned PUT, public bucket/read, local disk,
  alternate sanitizer, alternate storage lifecycle, or second PUT;
- generic i18n/profile locale, event bus, outbox, Redis/Celery, or background
  worker;
- key rotation command/migration, online blind-index-key rotation, key
  generation, KMS vendor selection, or fallback crypto;
- document approval/rejection, OCR-derived fields, multiple current images,
  front/back set, history UI, delete/purge/retention workflow;
- production legal content or changes to M9 offer acceptance;
- more than the exact two M10 domain tables.

## Product Owner Decisions — 20/20 FINAL

| ID | Binding decision |
|---|---|
| PO-M10-01 | M10 is the PII-safe identity/document foundation; activation and `shop_customer` are OUT. |
| PO-M10-02 | Only an active authenticated user's own existing M3 draft is eligible; a customer UUID is never client authority. |
| PO-M10-03 | Identity PII lives only in a separate encrypted 1:1 `customer_identities` row; `customers` stays PII-free. |
| PO-M10-04 | The payload has only first name, last name, optional middle name, JSHSHIR, `PASSPORT/ID_CARD`, and document number. |
| PO-M10-05 | One canonicalizer applies the exact name and document-number rules; no NFC/NFKC. |
| PO-M10-06 | JSHSHIR is exactly 14 ASCII digits; no checksum or registry claim. |
| PO-M10-07 | PyCA `AESGCM`, AES-256-GCM, 32-byte key, unique random 12-byte nonce, and customer/schema-bound AAD are mandatory; custom crypto is forbidden. |
| PO-M10-08 | An exact active key plus historical decrypt-key map is fail-closed; row key ID/schema version are stored, with no fallback or M10 rotation workflow. |
| PO-M10-09 | JSHSHIR uniqueness uses a separate 32-byte-key domain-prefixed HMAC-SHA-256 blind index; plaintext/plain hash is forbidden. |
| PO-M10-10 | The named unique conflict maps only to `DUPLICATE_JSHSHIR`; no other-customer disclosure or auto-link. |
| PO-M10-11 | Identity uses positive revision; create expects `0`, update expects the exact current revision, stale writes are zero-write/audit. |
| PO-M10-12 | `customer_documents` has unique objects, one current row, superseded history, `submission_id`, and expected-current protection; it is not generic attachment. |
| PO-M10-13 | Upload is backend-mediated and reuses M8 hard limits, sanitization, lifecycle, and errors exactly. |
| PO-M10-14 | As amended by CR-M10-01: envelope/auth/CSRF, read-only RL-CHECK, TX-A, M8 ingest-only final record, then TX-B; every external-I/O phase has no SQLAlchemy Session. |
| PO-M10-15 | As amended by CR-M10-01: TX-B failure triggers only atomic TX-C `DELETE_PENDING` claim; no request delete; existing M8 reconciliation provides eventual cleanup. |
| PO-M10-16 | Own current document read uses concrete parent authorization, `CURRENT` plus `AVAILABLE`, indistinguishable denial, and default 300-second GET. |
| PO-M10-17 | Identity/document mutation is draft-only; active correction is future scope. |
| PO-M10-18 | Own no-store view may show full normalized names only; JSHSHIR/document number are last-four masked and never sensitive-prefilled. |
| PO-M10-19 | Central audit contains only exact safe metadata, never PII, crypto values, storage references, or URLs. |
| PO-M10-20 | Repository/service retains caller-owned transactions; M10 closes with the customer still draft and registration/activation/shop link unstarted. |

## Identity Domain And Canonicalization

Exact vocabularies:

```text
CustomerDocumentType = PASSPORT | ID_CARD
CustomerDocumentStatus = CURRENT | SUPERSEDED
```

Exact canonical payload:

```text
first_name:      1..100 Unicode code points
last_name:       1..100 Unicode code points
middle_name:     null or 1..100 Unicode code points
jshshir:         exactly [0-9]{14}
document_type:   PASSPORT | ID_CARD
document_number: 5..32 characters matching [A-Z0-9 -]
```

For every name, outer whitespace is removed and each Unicode whitespace run
is collapsed to one ASCII space. Control, format, private-use, and surrogate
code points are rejected. No Unicode normalization form is applied.

Document number receives outer trim and ASCII uppercase conversion. Allowed
internal spaces and hyphens are preserved. JSHSHIR receives outer-whitespace
handling only and its canonical value must consist of exactly 14 ASCII
digits; Unicode digits are rejected. Routes, crypto, and persistence may not
implement alternate canonicalizers.

## Dependency And Redacted Settings Contract

The M9 baseline has no PyCA dependency. The only approved future direct
runtime addition is:

```text
cryptography>=50.0.0,<51
```

It must resolve through `uv.lock`; at the M10.05 audit date the reviewed
resolution is `50.0.0`. Failure to resolve/install this range is `BLOCKED`
for scope review, not permission to add another package or primitive.

`app/settings.py:Settings` remains the only environment loader. These are the
only M10 crypto inputs:

| Settings field | Environment name | Input |
|---|---|---|
| `customer_identity_active_key_id` | `CUSTOMER_IDENTITY_ACTIVE_KEY_ID` | `SecretStr | None` |
| `customer_identity_encryption_keys` | `CUSTOMER_IDENTITY_ENCRYPTION_KEYS` | `SecretStr | None`, a strict JSON object |
| `customer_identity_blind_index_key` | `CUSTOMER_IDENTITY_BLIND_INDEX_KEY` | `SecretStr | None` |

The fields may all be absent at base startup so `/health` stays independent.
Every identity read/write/completeness operation must call
`Settings.require_customer_identity_crypto_config()` before parsing PII or
opening a database transaction. It returns only a frozen, slots,
`repr=False` `CustomerIdentityCryptoConfig` snapshot with redacted key IDs and
`SecretBytes` material.

Key IDs are 1-64 ASCII characters matching `[A-Za-z0-9._-]+`. The encryption
mapping must be a non-empty strict JSON object with no duplicate JSON keys.
Every value and blind-index key is canonical padded RFC 4648 standard Base64
that strictly decodes to exactly 32 bytes. The active ID must exist; decoded
AEAD materials must be unique; the blind-index key must differ from every
AEAD, rate-limit, and OTP key. Values are not trimmed. There is no default,
fallback, generated key, algorithm setting, filesystem key, dump/iterator,
or generic KMS interface.

Absent, partial, malformed, duplicate, wrong-length, unknown-ID, key-reuse,
or authentication-failure configurations collapse to a typed constant-detail
unavailability result. They reveal no key ID/material/mapping detail and
perform zero PII parsing, DB, audit, or storage work.

## Cryptographic Envelope

The one envelope is:

```text
algorithm      = cryptography AESGCM / AES-256-GCM
key            = exactly 32 bytes selected by active key_id
nonce          = 12 cryptographically random bytes, unique per key
schema_version = 1
AAD            = b"NASIYA-CUSTOMER-IDENTITY-V1\0"
                 + customer_uuid.bytes
                 + uint32_be(1)
plaintext      = deterministic UTF-8 JSON with the exact six fields
                 in one fixed key order
ciphertext     = AESGCM output including its authentication tag
```

The canonical serializer accepts no extra key and emits no alternate form.
Decryption resolves only the row's exact key ID and schema version. Unknown
or retired key, wrong key/AAD/customer, nonce/ciphertext/tag tamper,
truncation, malformed JSON, noncanonical payload, or unsupported version
fails closed without fallback or partial plaintext.

Plaintext exists only inside the intended operation-local in-memory crypto
boundary and the authorized own-user no-store view. It must not enter ORM
state, persistence, audit, log, error, URL/query/redirect/flash, report,
default repr, browser storage, or test evidence using real PII.

## JSHSHIR Blind Index

The exact construction is:

```text
HMAC-SHA-256(
  dedicated_lookup_key,
  b"NASIYA-JSHSHIR-V1\0" + normalized_jshshir.ascii_bytes
)
```

The output is exactly 32 bytes. Plain JSHSHIR, plain SHA-256, encryption-key
reuse, index truncation, and online lookup-key rotation are forbidden. The
named PostgreSQL unique constraint is the final duplicate boundary. Only its
exact constraint name is recognized inside `Session.begin_nested()`; the
caller Session remains usable. Every sequential/parallel duplicate maps to
`DUPLICATE_JSHSHIR`, with zero overwrite/audit and no other identity detail.

The blind index, ciphertext, nonce, key ID/material, and authentication tag
are never audit/log/error/report/default-repr values.

## Persistence Contract

M10 creates exactly two domain tables and no generic schema object.

`customer_identities`:

| Column | Contract |
|---|---|
| `customer_id` | UUID primary key and FK to `customers.id`, `ON DELETE RESTRICT` |
| `ciphertext` | `BYTEA NOT NULL` |
| `nonce` | `BYTEA NOT NULL`, exact 12-byte check |
| `key_id` | `VARCHAR(64) NOT NULL`, exact syntax check |
| `schema_version` | `SMALLINT NOT NULL DEFAULT 1`, exact value check |
| `jshshir_blind_index` | `BYTEA NOT NULL`, exact 32-byte check, named unique constraint |
| `revision` | `INTEGER NOT NULL`, positive check |
| `created_at`, `updated_at` | aware UTC `TIMESTAMPTZ NOT NULL` |

`customer_documents`:

| Column | Contract |
|---|---|
| `id` | UUID primary key |
| `customer_id` | UUID FK to `customers.id`, `ON DELETE RESTRICT` |
| `object_file_id` | UUID FK to `object_files.id`, `ON DELETE RESTRICT`, globally unique |
| `submission_id` | UUID NOT NULL |
| `status` | bounded string `CURRENT | SUPERSEDED` |
| `attached_by_user_id` | UUID FK to `users.id`, `ON DELETE RESTRICT` |
| `attached_at` | aware UTC `TIMESTAMPTZ NOT NULL` |
| `superseded_by_document_id` | nullable self-FK, `ON DELETE RESTRICT` |
| `superseded_at` | nullable aware UTC `TIMESTAMPTZ` |

Named constraints/indexes enforce one identity per customer, one current
document per customer through a PostgreSQL partial unique index,
`(customer_id, submission_id)` replay uniqueness, global object attachment
uniqueness, exact statuses, current rows with null supersede metadata,
superseded rows with non-null replacement/time, and no self-replacement.
Historical FKs are `RESTRICT`; there is no delete/purge route or cascade.

The single M10 migration is a linear child of `a9b0c1d2e3f4`, creates only
these two domain tables plus the exact audit check extensions, imports model
metadata explicitly through `alembic/env.py`, and has a downgrade limited to
its own additions. Its revision identifier is assigned in the migration task;
M10.08 does not invent a speculative identifier.

## Own-Draft Authority And Transaction Ownership

`app.auth.deps:require_user` supplies the active actor. Customer authority is
then derived only from `current_user.id` through the unique
`customers.user_id`; no path, query, form, hidden input, object ID, shop role,
or platform-admin bit selects a customer.

Mutation uses the planned
`app/customer/repository.py:load_existing_own_customer_draft_for_update`:

1. select by server-resolved user ID;
2. `FOR UPDATE` the one possible customer row;
3. require exact status `draft`;
4. missing/non-draft -> `CUSTOMER_DRAFT_REQUIRED`, zero creation/write/audit.

It does not call `create_customer_draft_if_missing`. Identity/document code
never creates a customer. Shop owner/manager/cashier and platform-admin state
provide no cross-customer PII permission.

DB-only identity routes may use the existing request-owned outer transaction.
Document coordinators use `app.state.database_session_factory` for explicit
short phases. Repositories/services never commit, fully roll back, or close a
borrowed Session. Only a route/coordinator owns outer commit/rollback/close.
No SQLAlchemy Session or transaction may exist during source read, sanitize,
PUT, HEAD, DELETE, presign, or presigned HTTP fetch.

## Identity Save And Completeness

Identity save performs, in one outer DB transaction:

1. resolve complete crypto settings before PII parse/DB;
2. authenticate and lock the own existing draft;
3. compare submitted expected revision (`0` for create, exact positive value
   for update);
4. canonicalize, compute the blind index, and encrypt;
5. insert/update the 1:1 row and increment revision;
6. append `customer.identity_saved` in the same transaction;
7. let the route own commit and return PRG.

`CUSTOMER_IDENTITY_CHANGED` is zero-write/audit. Expected blind-index conflict
is isolated by savepoint and maps to `DUPLICATE_JSHSHIR`. Audit failure rolls
back the business mutation. No client key ID/ciphertext/customer/user ID is
accepted.

`HasCompleteCustomerIdentity` is true only when the row decrypts/authenticates
under its exact supported key/version/AAD, the strict canonical six-field
payload validates, the recomputed blind index matches, and revision is
positive. It does not activate, register, evaluate an offer, or call an
external registry.

`HasCurrentCustomerIdentityDocument` is true only for exactly one `CURRENT`
attachment whose exact object exists, is `AVAILABLE`, and has an M8-approved
image content type. Superseded/unavailable/missing state is false. It does not
activate the customer.

## Document Coordinator And M8 Reuse

Only `app.storage.service:ingest_sanitized_image` may ingest the image. It
retains M8's actual-byte request guard, bounded multipart, 10 MiB input/output,
40M-pixel, 16,384-dimension, one-frame JPEG/PNG/WebP decode, pixel-only
same-family re-encode, metadata removal, private lifecycle, and safe four-file
error mapping. Filename and browser MIME/extension are never authority.

The route accepts only file content plus `submission_id`, nullable
`expected_current_document_id`, and CSRF. Those UUIDs are concurrency/replay
tokens after own-customer resolution; they do not grant customer, object, or
document authority. Object identity comes only from the detached result of
the current M8 ingest call.

Lock order is binding:

```text
TX-A: customer -> current customer-document rows
TX-B: customer -> object_file -> current customer-document rows
TX-C: object_file -> attachment existence check
```

Every document attachment writer locks the object row before insert. TX-B
requires that it is `AVAILABLE` and globally unattached, then rechecks draft,
submission replay, and expected current before superseding/inserting/auditing.
Same submission replay converges to the existing attachment with no second
attachment/audit. Different stale submissions yield one winner and one
`CUSTOMER_DOCUMENT_CHANGED`. The old current row is superseded only inside a
successful TX-B.

After a post-ingest TX-B failure, TX-C follows the CR-M10-01 sequence above.
It calls only
`app.storage.repository:mark_object_file_delete_pending(failure_code=None)`
inside the same transaction as the global unattached proof. Provider cleanup
is eventual via
`app.storage.service:reconcile_stale_object_deletes`; definite deletion ends
`DELETED`, while ambiguous provider outcome stays `DELETE_PENDING` with
`DELETE_OUTCOME_UNKNOWN` for another reconciliation. M10 creates no
scheduler.

## Own-Current Document Access

`GET /customer/identity/document` takes no customer/object/document identity.
The server resolves the actor's own customer and exact current attachment. A
concrete `ObjectFileAccessAuthorizer` proves the current attachment/object
binding before M8 object lookup or provider call. Creator identity is never
read authority.

Missing, foreign, superseded, unavailable, or mismatched state is the same
`FILE_ACCESS_DENIED` and produces zero HEAD/presign. Success reuses
`create_authorized_presigned_get_url`, default TTL 300 seconds within existing
M8 bounds, appends `customer.document_access_granted`, and returns only the
short-lived URL boundary. The URL and storage identifiers are never persisted,
logged, audited, reported, flashed, or placed in browser storage; the response
and redirect are no-store with the inherited strict referrer policy.

## Audit And Data-Minimization Contract

The central registry is extended only with:

```text
customer.identity_saved             -> customer_identity
customer.document_attached          -> customer_document
customer.document_superseded        -> customer_document
customer.document_access_granted    -> customer_document
```

Exact payload allowlists are:

| Event | Required safe payload |
|---|---|
| `customer.identity_saved` | `revision`, `created_or_updated`, `document_type` |
| `customer.document_attached` | `status`, `submission_replayed` (must be false); no `submission_id` |
| `customer.document_superseded` | `replacement_document_id` (a customer-document UUID) |
| `customer.document_access_granted` | `ttl_seconds` |

The event/object enums, event-object map, redaction builders, ORM check SQL,
and Alembic checks change together. Audit remains append-only, typed,
event-specific, and in the same business transaction. Access audit completes
before URL release. There is no audit UI/search/export/retention platform.

Forbidden in audit, logs, errors, reports, default repr, or telemetry:

- first, last, or middle name;
- full or masked JSHSHIR/document number;
- blind index, ciphertext, nonce, authentication tag, key ID/material;
- object-file ID, bucket, key, checksum, filename, content metadata, image/file
  reference, or presigned URL;
- raw form, User-Agent, phone/other PII, session/cookie/CSRF/token/secret;
- exception text, SQL, DB constraint detail, or provider detail.

ORM/domain/config/result DTOs containing sensitive values must have explicit
redacted `repr` and no generic `asdict`/dump path. Public errors contain only
stable code and safe localized text.

## Routes, Web Presentation, And Errors

The complete public route set is:

```text
GET  /customer/identity
POST /customer/identity
POST /customer/identity/document
GET  /customer/identity/document
```

No route has a customer, object-file, or document path parameter. All are
own-user/account routes. The exact template is
`app/templates/customer/identity.html`; it extends the current base template
and uses a typed explicit view model. Full canonical names may appear only on
this authorized no-store view. Existing JSHSHIR/document number render only
as last-four masks; their form fields are always blank. Names may prefill.

The existing authenticated `app/templates/customer/profile.html` gains only a
localized link to `/customer/identity` and a PII-free identity/current-document
completion status supplied by an explicit redacted discovery view. Its GET
remains read-only and no-store; it creates/touches no customer, identity, or
document row and exposes no identifier/object UUID. This discoverability
changes no route count and adds no registration or activation action.

The document input is exactly an accessible labeled file input with
`accept="image/*" capture="environment"`. All POSTs reuse session-bound CSRF
and PRG. Every success/error/redirect/fragment is `Cache-Control: no-store`.
Jinja autoescape and current CSP remain intact; `|safe`, `Markup`, raw HTML,
inline script/style/event handlers, localStorage, and sessionStorage are
forbidden. UZ-Latn and RU use feature-local immutable copy with UZ-Latn
fallback; no account locale framework is created. Layout must work without
horizontal scroll at 320-430 px, use labels/textual errors, visible focus, and
44 px actions.

Reused error codes:

```text
UNAUTHORIZED FORBIDDEN VALIDATION_ERROR CSRF_FAILED RATE_LIMITED
DUPLICATE_JSHSHIR FILE_ACCESS_DENIED FILE_STORAGE_ERROR FILE_TOO_LARGE
UNSUPPORTED_FILE_TYPE
```

Exact M10 additions:

```text
CUSTOMER_DRAFT_REQUIRED
CUSTOMER_IDENTITY_CHANGED
CUSTOMER_DOCUMENT_CHANGED
CUSTOMER_IDENTITY_UNAVAILABLE
CUSTOMER_DOCUMENT_UNAVAILABLE
```

Unknown key/version, invalid configuration, tamper, malformed payload, and
crypto backend failure collapse into the safe unavailability/internal policy;
the response never distinguishes key, customer existence, constraint,
provider, or crypto cause.

## Repository Placement

Binding file/symbol placement is detailed in
`docs/m10_repository_map.md#m1008-approved-m10-placement`. In summary:

- `app/customer_identity/` owns identity contracts, canonicalization, crypto,
  models, repository, service, presentation, and the four-route router;
- `app/customer_document/` owns only the concrete attachment model,
  repository, coordinator, M8 parent authorizer, and storage composition;
- existing `app/settings.py`, `app/auth/error_codes.py`, `app/audit/`,
  `app/main.py`, `alembic/env.py`, and one Alembic child receive narrow
  extensions;
- `app/customer/` keeps M3 ownership and gains only the exact locked
  existing-own-draft repository resolver plus the narrow read-only profile
  discovery composition named in the repository map;
- no generic base, attachment ownership framework, alternate storage/crypto,
  empty module, or extra table is permitted.

## Automated Test Contract

`docs/m10_repository_map.md#m1007-repository-aware-threat-to-test-matrix` is
the executable matrix: M10-T001 through M10-T102, exactly 102 unique planned
test nodes across 18 named files. Each future implementation task must add the
nodes assigned to its boundary without renaming away the threat mapping.

All DB/domain/migration/concurrency integration tests use real PostgreSQL.
Storage tests use an injected fake adapter and the approved local MinIO path.
There is no SQLite, `create_all()`, real cloud credential, real PII fixture,
skip, xfail, xpass, assertion weakening, or reduced inherited suite. Tests
must cover the exact CR early-check/ingest-only-record/TX-B-lock/TX-C-claim/
existing-reconcile semantics, session-free external I/O, leakage canaries,
named constraints/savepoints, migration walk, authorization/IDOR, CSRF/XSS/
no-store/PRG, and M1-M9 containment.

Closure requires full suite and exact remote SHA with `0 failed`, `0 skipped`,
`0 xfailed`, and `0 xpassed`; before closure the M10 implementation remains
unstarted or incomplete, never implicitly green.

## Eight Checkpoints And Definition Of Done

The implementation checkpoints remain exactly:

1. `M10: freeze customer identity scope`
2. `M10: add encrypted customer identity contracts`
3. `M10: add customer identity persistence`
4. `M10: add customer identity services`
5. `M10: add customer document attachment`
6. `M10: expose customer identity web flows`
7. `M10: harden PII security and concurrency`
8. `M10: complete customer identity foundation`

A later docs-only closeout may be `docs: close M10 remote evidence`. This
document authorizes no commit or push by itself.

M10 can close only after the baseline/lineage, exact dependency/settings,
two-table migration, crypto/blind-index/revision, draft-only authority, M8
reuse, CR-M10-01 coordinator, one-current/replay/concurrency, access, masked
web, exact audit/errors, 102-threat plan, full M1-M9 containment, manual Chrome
flow, approved MinIO flow, eight remote-green checkpoints, exact pushed SHA,
final reports, clean/synced main, and continuing draft/non-activation state
are all evidenced. Until then `M10 REMOTE GREEN — CLOSED` must not be claimed.

## Mandatory Stop Conditions

Stop the current task, do not commit/push, and report `M10 BLOCKED` at the
first occurrence of any of these:

1. M9 ancestry/remote closure, clean/sync, TT blob, or freeze hash differs.
2. Alembic parent is not the single head `a9b0c1d2e3f4`.
3. Protected TT/freeze content must be changed.
4. A PO-M10 or CR-M10-01 semantic must be weakened or reinterpreted.
5. M3 one-user/one-draft/PII-free ownership does not match the repository.
6. The approved PyCA range cannot resolve or a second/custom crypto runtime is
   required.
7. Secrets cannot remain in the redacted fail-closed settings boundary.
8. Plaintext PII, plain hash, or crypto/storage secret/reference must cross a
   forbidden persistence/output boundary.
9. Customer/object/document identity must be trusted from untrusted input.
10. Shop role or platform-admin state must grant cross-customer PII access.
11. Repository/service must commit, fully roll back, or close a Session.
12. A SQLAlchemy Session must remain open during external storage/source I/O.
13. M8 ingest, sanitizer, lifecycle, rate limit, presign, or reconciliation
    must be duplicated, weakened, or redesigned.
14. CR-M10-01 cannot guarantee ingest-only attempt recording, object-lock
    pivot, same-TX unattached claim, and attached-object safety.
15. Deterministic one-current/revision/replay/concurrency cannot be enforced.
16. Public registration/OTP, activation, shop link/capture, admin PII, debt,
    payment, rating, disclosure, notification, or scheduler is required.
17. OCR/MRZ, biometric, registry, generic attachment/CMS/admin/KMS/audit
    platform, alternate storage, or local disk is required.
18. Real PostgreSQL and fake/approved-MinIO tests cannot run deterministically,
    or SQLite/`create_all`/skip/xfail/assertion weakening is proposed.
19. Full suite or exact-SHA remote CI is not green at a checkpoint/closure
    gate.
20. Production key, credential, plaintext PII, crypto/storage identifier, or
    presigned URL enters source, fixture, CI, logs, evidence, or reports.

Known deferred boundaries are authoritative in
`docs/m10_known_limitations.md`; they are not license to implement adjacent
scope.
