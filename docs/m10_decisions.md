# Nasiya M10 Decisions

Status: authoritative M10 repository decision log, pre-implementation.
Source authority: `docs/tt_nasiya_web_v1.md` and
`/home/yalgashev/projects/nasiya_m10_00_final_scope_freeze.md`.
Product Owner disposition: `PO-M10-01..20 — 20/20 FINAL APPROVED`.
Amendment: `CR-M10-01 — FINAL APPROVED` changes only the executable form of
PO-M10-14 and PO-M10-15.

These decisions are closed for M10. A later task implements its assigned
slice; it does not reopen product, cryptographic, transaction, storage,
authorization, privacy, or test design.

## Baseline Decision

M10 starts from the clean/synced M9 docs-only closeout
`f96b9f0a6d6b506f6715aa354cb4346199f1f5c5`. The M9 implementation
`e2cda04920964cf383a749e07504539ccdafa0ab` is an ancestor; GitHub Actions
run `30645425078` is successful; the exact remote full suite is `2540 passed`
with no failed/skipped/xfailed/xpassed outcomes; M9 has `8/8` implementation
checkpoints. The only Alembic head and M10 parent is `a9b0c1d2e3f4`.

Protected-source pins are the TT blob
`d77c0f0f330a1330155a4aee3c46b05d97cf5561` and external freeze SHA-256
`de766bc75752cd80f64e49081b5764a0bc7b3b2112366f1a5d11818a7ab3a462`.
Changing either is not an M10 implementation option.

## CR-M10-01 — FINAL APPROVED

M10.06 found that the original wording of PO-M10-14/15 would duplicate M8's
authoritative attempt record and could not safely perform immediate provider
deletion during an attach race. CR-M10-01 is the sole post-freeze correction.

It preserves the one capability, exact two M10 tables, PO-M10-01..20 FINAL
status, eight checkpoints, and inherited M8 boundaries. It replaces only:

- PO-M10-14's upload-rate placement with a read-only early
  `check_storage_upload_rate_limit` short transaction followed by exactly one
  authoritative `record_storage_upload_attempt` inside
  `ingest_sanitized_image`;
- PO-M10-15's immediate request delete with an object-row-serialized TX-C
  that proves global nonattachment and atomically calls
  `mark_object_file_delete_pending(failure_code=None)`, leaving provider
  cleanup to existing `reconcile_stale_object_deletes`.

The exact order is envelope guard, authentication, CSRF, RL-CHECK, TX-A, M8
ingest, TX-B, and only on post-ingest TX-B failure TX-C. TX-B lock order is
customer, object_file, current-document rows. TX-C lock order is object_file,
attachment existence check. No request provider DELETE, private M8 delete
import, new public M8 delete API, new rate limiter/counter, or M10 scheduler is
permitted.

## Final Product Owner Decisions

| ID | FINAL decision | Binding repository consequence |
|---|---|---|
| PO-M10-01 | Milestone is `PII-safe Customer Identity & Customer Document Attachment Foundation`; activation and `shop_customer` are OUT. | M10 ends after an own-draft identity/document foundation; M11 owns activation/linking. |
| PO-M10-02 | Only an active authenticated user's own existing M3 draft is eligible; customer UUID is not client input. | Server derives ownership from the session user; public/shop/cross-customer routes are absent. |
| PO-M10-03 | Identity PII is a separate encrypted 1:1 row, not plaintext columns on `customers`. | M3 aggregate remains PII-free; one exact `customer_identities` table is added. |
| PO-M10-04 | Exact fields are first/last name, optional middle name, JSHSHIR, `PASSPORT/ID_CARD`, and document number. | No birth date, address, email, gender, extracted image data, or extra PII field. |
| PO-M10-05 | Names outer-trim/collapse Unicode whitespace and reject forbidden categories; document number outer-trims, ASCII-uppercases, preserves allowed separators; no NFC/NFKC. | One canonicalizer is shared by web, crypto, and persistence boundaries. |
| PO-M10-06 | JSHSHIR is exactly 14 ASCII digits; no invented checksum or registry verification. | Validation and uniqueness do not claim government authenticity. |
| PO-M10-07 | Use PyCA `AESGCM` with a 32-byte AES-256-GCM key, unique random 12-byte nonce, and customer/schema-bound AAD. | One reviewed dependency only; custom/alternate AEAD and fallback crypto are forbidden. |
| PO-M10-08 | Keyring has one active write key and exact historical decrypt keys; key IDs are bounded, stored, and fail closed. | No fallback, duplicate material, key-management platform, or M10 rotation command. |
| PO-M10-09 | JSHSHIR uses a separate 32-byte-key domain-prefixed HMAC-SHA-256 blind index. | Plain JSHSHIR/plain hash and AEAD-key reuse are absent; exact 32-byte DB uniqueness. |
| PO-M10-10 | Named blind-index conflict yields only `DUPLICATE_JSHSHIR`. | Savepoint preserves the caller Session; no other-customer disclosure or auto-link. |
| PO-M10-11 | Identity revision is positive; create expects `0`, update exact current revision. | Stale requests return `CUSTOMER_IDENTITY_CHANGED` with zero mutation/audit. |
| PO-M10-12 | Concrete document table has unique object, `CURRENT/SUPERSEDED`, one current, submission replay, and expected-current checks. | No generic attachment/approval system; sequential and parallel replay converge. |
| PO-M10-13 | Upload is backend-mediated and reuses the exact M8 guard, multipart, sanitizer, lifecycle, errors, and storage adapter. | No presigned PUT, raw browser storage, local disk, alternate sanitizer, or second PUT. |
| PO-M10-14 | CR-amended sequence is RL-CHECK (check only), TX-A, M8 ingest (record exactly once), TX-B; external I/O has no open Session. | Existing M8 buckets/settings/errors remain authoritative; route/coordinator owns short phases. |
| PO-M10-15 | CR-amended compensation is an atomic TX-C unattached proof plus `DELETE_PENDING` claim; existing M8 reconciliation deletes later. | Attach/object row is the race pivot; request does no provider delete and M10 adds no scheduler. |
| PO-M10-16 | Own read requires the current attachment, available object, concrete parent authorizer, indistinguishable denial, and default 300-second GET. | Creator/admin/shop identity is insufficient; no history or object-ID route. |
| PO-M10-17 | Identity/document is mutable only while customer is `draft`. | No active-customer correction or state transition is introduced. |
| PO-M10-18 | Authorized no-store summary may show normalized names; identifiers are last-four masked and never sensitive-prefilled; UZ-Latn/RU, CSRF/PRG/CSP/mobile rules apply. | PII stays out of URL/flash/browser storage and unsafe rendering. |
| PO-M10-19 | Audit stores only exact safe revision/status/type/outcome metadata. | No names, identifier, blind/crypto value, object/storage reference, or URL enters audit. |
| PO-M10-20 | Repositories/services do not own commit/full rollback/close; closeout leaves customer draft and registration/activation/shop link unstarted. | Route/coordinator owns outer transaction and M11 boundary stays explicit. |

## Repository Reconciliation Decisions

### RD-M10-01 — Narrow Package Placement

M10 uses two narrow packages:

- `app/customer_identity/` for canonical identity contracts, cryptography,
  identity persistence/service, presentation, and the exact own-user router;
- `app/customer_document/` for the concrete document model/repository,
  upload/attach coordinator, completeness query, parent authorizer, and narrow
  operation-time storage composition.

The document package has no generic parent registry, owner abstraction,
attachment base, approval workflow, or independent public router. The four
routes are composed once by `app/customer_identity/router.py` so their shared
own-user/no-store surface remains explicit. `app/customer/` keeps M3 ownership
and receives only the exact locked existing-own-draft resolver. No empty
package/module is created.

### RD-M10-02 — Exact Domain Vocabulary And Canonicalizer

`app/customer_identity/contracts.py:CustomerDocumentType` contains only
`PASSPORT` and `ID_CARD`.
`app/customer_document/contracts.py:CustomerDocumentStatus` contains only
`CURRENT` and `SUPERSEDED`.

`app/customer_identity/canonicalization.py:canonicalize_customer_identity`
is the only canonicalizer. Its input/output contract is the exact six fields
and bounds in `docs/m10_scope_contract.md`. It rejects extra fields, Unicode
digits, forbidden Unicode categories, and nonmatching document characters.
It does not apply NFC/NFKC or extract image content.

### RD-M10-03 — One Approved Dependency And Primitive

`pyproject.toml` may add exactly `cryptography>=50.0.0,<51`; `uv.lock` records
the exact reviewed resolution. `app/customer_identity/crypto.py` imports only
`cryptography.hazmat.primitives.ciphers.aead.AESGCM` for AEAD. Standard
library `hmac`/`hashlib.sha256` implements only the frozen blind-index formula;
secure random nonce generation uses a cryptographically secure source.

No direct `cffi`, low-level cipher/GCM, Fernet, AES-GCM-SIV, crypto fallback,
vendored primitive, optional extra, or operator-selectable algorithm is
allowed. Resolution failure blocks the task.

### RD-M10-04 — Exact Redacted Crypto Settings

`app/settings.py:Settings` gains only
`customer_identity_active_key_id`,
`customer_identity_encryption_keys`, and
`customer_identity_blind_index_key`, all optional `SecretStr` inputs at base
startup. Their exact environment names are
`CUSTOMER_IDENTITY_ACTIVE_KEY_ID`, `CUSTOMER_IDENTITY_ENCRYPTION_KEYS`, and
`CUSTOMER_IDENTITY_BLIND_INDEX_KEY`. `Settings.require_customer_identity_crypto_config()`
validates the whole strict bundle and returns
`app/customer_identity/crypto.py:CustomerIdentityCryptoConfig`, frozen,
slots, `repr=False`, with no dump/iterator/fallback.

The mapping/key-ID/Base64/32-byte/separation rules in the scope contract are
exact. Invalid configuration is indistinguishable and checked before PII parse
or DB. Key ID/material/count/source never appears in repr, error, log, audit,
report, tracked defaults, fixtures, or CI output.

### RD-M10-05 — Customer-Bound Versioned Envelope

`app/customer_identity/crypto.py` owns exactly schema version `1`, fixed-key
order deterministic UTF-8 JSON, the domain prefix
`b"NASIYA-CUSTOMER-IDENTITY-V1\0"`, `customer_uuid.bytes`, and big-endian
32-bit version AAD. `encrypt_customer_identity` returns one redacted envelope
of ciphertext including tag, random 12-byte nonce, active key ID, and version.
`decrypt_customer_identity` selects only the stored exact key and authenticates
before strict payload parsing.

Wrong customer/key/AAD, unknown or retired key, version mismatch, tamper,
truncation, malformed/noncanonical payload, or backend failure returns no
partial value and maps to the same internal unavailability boundary.

### RD-M10-06 — Dedicated Blind Index And Named Conflict

`app/customer_identity/crypto.py:compute_jshshir_blind_index` implements only
the frozen HMAC-SHA-256 domain. `customer_identities.jshshir_blind_index` is a
32-byte `BYTEA` with a deterministic named unique constraint. Repository
insert/update uses `Session.begin_nested()` only around that expected conflict
and matches exact PostgreSQL `diag.constraint_name`.

It returns `DUPLICATE_JSHSHIR` without another row lookup, masked identity,
linking, overwrite, audit, full rollback, or unusable Session. Online blind
key rotation is not implemented.

### RD-M10-07 — Exactly Two Tables And Linear Migration

`app/customer_identity/models.py:CustomerIdentity` maps
`customer_identities`; `app/customer_document/models.py:CustomerDocument`
maps `customer_documents`. They have only the columns and named checks,
uniques, partial unique index, and restrictive FKs in the scope contract.

One future Alembic revision is the sole child of `a9b0c1d2e3f4`; it also
updates the existing audit checks for the exact four events/two objects. No
revision ID is pre-reserved in readiness docs. `alembic/env.py` imports both
model packages. Downgrade reverses only M10 additions and preserves all M1-M9
data. SQLite, `create_all`, cascade history deletion, and a third M10 table are
forbidden.

### RD-M10-08 — Deterministic Existing-Own-Draft Lock

`app/customer/repository.py:load_existing_own_customer_draft_for_update`
accepts only a borrowed Session and the server-resolved authenticated user ID.
It selects via unique `customers.user_id`, locks the customer `FOR UPDATE`,
and requires exact `draft`. Missing/non-draft is
`CUSTOMER_DRAFT_REQUIRED` and creates nothing.

`create_customer_draft_if_missing` is not used. Customer UUID, document UUID,
object UUID, shop role, and platform-admin bit never establish authority.

### RD-M10-09 — Revisioned Identity Service

`app/customer_identity/service.py:save_own_customer_identity` requires a
typed authenticated actor, expected revision, canonical input, crypto config,
and borrowed outer Session. It locks own draft, verifies expected revision,
computes blind/envelope, writes revision `1` or current+1, and appends the
exact audit in the same transaction.

Stale revision and duplicate are zero-write/audit. Audit failure rolls back
with the business write. Repository/service never commit, fully roll back, or
close. Returned DTOs expose no PII/crypto value by default repr.

### RD-M10-10 — Completeness Is A Non-Activation Query

`app/customer_identity/service.py:has_complete_customer_identity` decrypts,
authenticates, strictly validates, recomputes the blind index, and requires a
positive revision. `app/customer_document/service.py:has_current_customer_identity_document`
requires exactly one current attachment and an available, allowed M8 image.

Both are server-internal policy queries. They do not transition state, create
records, call OCR/registry/biometric services, evaluate offers/OTP, or imply
registration completion.

### RD-M10-11 — CR-M10-01 Upload Coordinator

`app/customer_document/coordinator.py:upload_and_attach_own_customer_document`
owns the exact envelope/auth/CSRF, RL-CHECK, TX-A, M8 ingest, TX-B, and
conditional TX-C orchestration. It receives the existing session factory and
injected `ObjectStorageService`; it does not receive a request Session.

RL-CHECK calls only `check_storage_upload_rate_limit` in a short transaction.
M8 ingest alone records once and performs final enforcement before source
read/sanitize/provider I/O. The same existing settings/buckets/key/error are
used. M10 adds no recorder or limiter.

### RD-M10-12 — Object Row Is The Attach/Compensation Pivot

TX-A locks customer then current document rows. TX-B locks customer, the
server-returned object row, then current document rows. It requires
`AVAILABLE` and global nonattachment before replay/stale recheck and insert.
Every attachment writer uses that order.

`app/customer_document/repository.py:claim_unattached_object_for_compensation`
implements TX-C by locking the object, performing the global attachment
`NOT EXISTS`, and calling existing
`mark_object_file_delete_pending(failure_code=None)` in that same borrowed
transaction. It returns a redacted claimed/no-op outcome. It performs no
provider I/O and has no commit/rollback/close.

The request never calls `delete_available_object` or any `_delete_*` symbol.
Existing `reconcile_stale_object_deletes` eventually cleans a claimed orphan;
M10 creates no scheduler, command, retry policy, or public delete API.

### RD-M10-13 — Replay, Stale, And One-Current Semantics

The form's `submission_id` is unique per own customer and
`expected_current_document_id` is the server-observed nullable current ID.
Neither selects authority. Same-submission replay returns the existing
attachment with no upload/second insert/audit whenever replay can be resolved
before ingest; a race discovered after ingest preserves the existing winner
and TX-C handles only the new unattached object.

Different stale submissions serialize under the exact lock order. One wins;
the loser gets `CUSTOMER_DOCUMENT_CHANGED` and zero attachment/audit. The old
current is superseded, with complete replacement metadata, only in the same
successful TX-B that inserts the new current. DB partial/object/submission
uniques are final defenses, not substitutes for locking.

### RD-M10-14 — Existing M8 Is The Only Storage Boundary

The coordinator reuses `StorageBodyLimitMiddleware`,
`bounded_multipart_upload`, `ingest_sanitized_image`, object lifecycle,
`mark_object_file_delete_pending`, `reconcile_stale_object_deletes`,
`ObjectFileAccessAuthorizer`, and `create_authorized_presigned_get_url`.

There is no direct browser upload, presigned PUT, local disk, new sanitizer,
raw filename/MIME trust, alternate lifecycle, second PUT, private delete
import, or storage schema change. Source read, sanitize, PUT, HEAD, DELETE,
presign, and fetch occur with zero open SQLAlchemy Session/transaction.

### RD-M10-15 — Own-Current Parent Authorization

`app/customer_document/authorization.py:OwnCurrentCustomerDocumentAuthorizer`
implements the existing `ObjectFileAccessAuthorizer` protocol. It derives
customer from the authenticated user's server identity and proves exact
`CURRENT` attachment/object binding. The route accepts no object/customer/
document ID, and object creator is not authority.

Missing/foreign/superseded/unavailable/mismatch is indistinguishable
`FILE_ACCESS_DENIED` before provider calls. Success uses M8 default 300-second
presign and audits by customer-document identity only. URL and object/storage
values are response-bound redacted values and never persisted or reported.

### RD-M10-16 — Exact Audit Extension

`app/audit/contracts.py`, `app/audit/redaction.py`,
`app/audit/models.py`, and the M10 Alembic revision change atomically for only:

```text
customer.identity_saved          customer_identity
customer.document_attached       customer_document
customer.document_superseded     customer_document
customer.document_access_granted customer_document
```

Payloads are exactly those in the scope contract. Unknown keys are removed;
missing/invalid required safe metadata fails closed. Identity/attachment
audit failure rolls back business mutation. There is no log fallback or audit
read/admin surface.

### RD-M10-17 — Stable Errors And Feature-Local Localization

The five new codes extend `app/auth/error_codes.py:ErrorCode` and
`ERROR_CATALOG`; existing auth/storage codes are reused. The public catalog
contains constant safe UZ-Latn text/status, while
`app/customer_identity/web_presentation.py` supplies immutable UZ-Latn/RU
page/action copy with UZ-Latn fallback.

Crypto, DB, key ID, constraint, other-customer, storage, and raw-input detail
never reaches a public message. M10 creates no generic i18n or persisted locale.

### RD-M10-18 — Four Routes, One Safe Template

`app/customer_identity/router.py:router` owns exactly:

```text
GET  /customer/identity
POST /customer/identity
POST /customer/identity/document
GET  /customer/identity/document
```

It is included once in `app/main.py:create_app`. The upload path alone joins
`app/storage/body_guard.py:M8_STORAGE_BODY_GUARD_PATHS` with the inherited
11,010,048-byte request envelope. `app/templates/customer/identity.html` is
the only new page template. It is typed, autoescaped, labeled, mobile-first,
UZ-Latn/RU, CSRF/PRG/CSP/no-store, uses the exact capture input, masks
identifiers to last four, and never sensitive-prefills or stores browser PII.

M10.68 narrowly extends the existing authenticated
`app/customer/router.py:profile_page` and
`app/templates/customer/profile.html` with a localized
`/customer/identity` link and a PII-free completion status from
`app/customer_identity/web_presentation.py:CustomerIdentityDiscoveryView`.
The discovery read creates/touches no row, renders no UUID/PII/object value,
adds no route, and exposes no registration or activation action.

Document storage operations obtain an operation-time injected adapter and a
detached redacted actor/context; a yielding request DB dependency must not
remain alive across M8/provider work. Production storage composition follows
the current operation-time config/client/close pattern and preserves optional
startup; tests inject fake/approved MinIO adapters.

### RD-M10-19 — Exact Threat-To-Test Contract

The M10.07 matrix in `docs/m10_repository_map.md` contains M10-T001..M10-T102,
102 unique exact planned tests across 18 named files. It is binding for names,
prevention, deterministic outcomes, evidence symbols, and containment.

All automated DB/concurrency/migration tests use real PostgreSQL. Storage uses
injected fake and designated local/CI MinIO. No SQLite, `create_all`, real
cloud credentials, real PII, skip/xfail/xpass, assertion weakening, or suite
reduction. CR-M10-01 race paths, audit atomicity, leakage, protected hashes,
and M1-M9 regressions are mandatory.

### RD-M10-20 — Outer Ownership, Evidence, And Stop Behavior

DB-only routes own one outer transaction; multipart/storage coordinators own
explicit short sessions through the shared session factory. Repositories and
services may add/flush and use an exact expected-conflict savepoint, but never
commit, fully roll back, or close a borrowed Session.

Every implementation checkpoint runs targeted/full tests, Ruff, format,
`git diff --check`, dependency, secret/PII, protected-source, scope, and
transaction/I/O audits. Commit/push occurs only when explicitly requested.
Any stop condition in `docs/m10_scope_contract.md` ends the current task as
`M10 BLOCKED`; no adjacent scope or weakened assertion is an acceptable fix.

## Decision Closure

The decisions above authorize only the repository files/symbols enumerated in
`docs/m10_repository_map.md`, only the exact schema/routes/events/errors, and
only the M10 capability. The deferred facts in
`docs/m10_known_limitations.md` remain limitations, not implementation work.
