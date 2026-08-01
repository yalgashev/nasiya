# Nasiya M10 Repository Map

Status: authoritative M10.02-M10.08 readiness map: source inventory, current
repository symbols, own-draft/crypto/storage audits, the M10.07 threat matrix,
and final repository placement, corrected by CR-M10-01 — FINAL APPROVED.
Baseline: `f96b9f0a6d6b506f6715aa354cb4346199f1f5c5`.
Scope: executable integration and planned file/symbol map only; no M10 product
code, schema, route, dependency, migration, template, test, or CI change is
implemented by this document.

This inventory separates product authority from closed-milestone inheritance
and implementation evidence. It does not amend `docs/tt_nasiya_web_v1.md` or
the external M10.00 Final Scope Freeze, and it cannot promote an informative
repository observation into a product decision.

## Source Classification And Precedence

The M10.00 precedence order controls conflicts. The classification below
controls how each source may be used: a closure report proves what shipped but
does not widen the normative scope; repository code proves current mechanics
but does not invent product semantics.

| Precedence tier | Source | Classification | Permitted use in M10 |
|---|---|---|---|
| 1 | `docs/tt_nasiya_web_v1.md` | AUTHORITATIVE product requirement | Defines the final product requirements for customer identity, documents, privacy, localization, stable errors, and tests. |
| 2 | `/home/yalgashev/projects/nasiya_m10_00_final_scope_freeze.md` | AUTHORITATIVE M10 milestone scope and PO decisions | Narrows the TT to one M10 capability and freezes the exact M10-only design and OUT scope. |
| 2A | `CR-M10-01 — FINAL APPROVED` | AUTHORITATIVE executable amendment | Corrects only PO-M10-14 rate-limit placement and PO-M10-15 compensation timing; 20/20 decisions, one capability, two-table boundary, and eight checkpoints remain unchanged. |
| 3 | `docs/m9_scope_contract.md`, `docs/m9_decisions.md`, `docs/m9_known_limitations.md` | AUTHORITATIVE inherited M9 boundary | Fixes audit/redaction, transaction, error/localization, acceptance, and deferred-capability boundaries. |
| 3 | `m9-result.md`, `docs/m9_final_report.md`, `docs/m9_repository_map.md` | INFORMATIVE M9 closure/repository evidence | Proves what shipped and where; cannot widen the M9 or M10 normative boundary. |
| 4 | `docs/m8_scope_contract.md`, `docs/m8_decisions.md`, `docs/m8_known_limitations.md` | AUTHORITATIVE inherited M8 boundary | Fixes ingest, sanitizer, lifecycle, storage I/O, authorization, presign, error, and test boundaries. |
| 4 | `m8-result.md`, `docs/m8_final_report.md`, `docs/m8_repository_map.md`, `docs/m8_storage_runbook.md` | INFORMATIVE M8 closure/repository/operations evidence | Proves what shipped and locates exact reuse; cannot widen the M8 or M10 normative boundary. |
| 5 | M10.00's M3 boundary plus closed implementation at `35a58a6444bd29ea7379354e2bf0de3bf0de81bc` | AUTHORITATIVE narrowing plus INHERITED implementation evidence | Supplies the one authenticated-user-owned, PII-free, draft-only customer aggregate. The referenced M3 result/contract files are absent, as recorded below. |
| 6 | M1-M7 closed auth/session/CSRF/DI/transaction/test contracts used by M3/M8/M9 | INHERITED infrastructure boundary | Must be reused where the later authoritative contracts cite it; it is not independently expanded in M10.02. |
| 7 | Current `README.md`, code, migrations, templates, tests, lockfile, and CI | INFORMATIVE repository evidence | Verifies real naming and behavior. A mismatch with higher authority is a stop condition, not permission to reinterpret it. |

The M10.00 hierarchy's final item points back to the FINAL technical
contracts in that same freeze; it is not a separate lower-authority source.
Those contracts remain part of tier 2. Repository evidence can refine future
file/symbol placement only where the freeze explicitly requires
reconciliation; it cannot override a frozen semantic decision.

`m8-result.md` proves M8 closed at implementation/recovery SHA
`af611b0d546479d1f21075d9b37fac748a71fc1e`, Alembic
`f8a9b0c1d2e3`, and remote `2167 passed`. `m9-result.md` proves M9 closed at
implementation SHA `e2cda04920964cf383a749e07504539ccdafa0ab`, Alembic
`a9b0c1d2e3f4`, and remote `2540 passed`; the docs-only closeout is the M10
baseline above.

### M3 Documentary Gap

The M10.00 freeze names `m3-result.md` and M3 customer draft contracts, but
neither `m3-result.md` nor an M3 scope/decisions/limitations/final-report file
exists anywhere in the tracked Git object history. M3 closure is instead
evidenced by:

- exact ancestor commit `35a58a6444bd29ea7379354e2bf0de3bf0de81bc`,
  `chore: complete M3 customer onboarding foundation`;
- the tracked `README.md` M3 boundary;
- migration `b1f3a7c9d2e4_create_customers_table.py`;
- current `app/customer/` implementation and real PostgreSQL/security tests.

This is a documentary-reference gap, not evidence that M3 semantics are open.
M10 must cite the exact surviving evidence and preserve its narrowest common
contract. The absent files grant no authority to add registration, activation,
PII, lead, or shop-link behavior.

## TT-Derived Requirement Inventory

### Customer Identity And Duplicate Handling

Authoritative TT sections 6.3, 7, 8, 11, 13, and 14 require:

- full customer registration eventually includes phone, Telegram/OTP, F.I.Sh.,
  JSHSHIR, a passport/ID photo, and current-offer acceptance;
- duplicate registration for the same JSHSHIR is rejected with stable
  `DUPLICATE_JSHSHIR`; the final product links an existing customer rather
  than creating a second one;
- shop-facing customer views exclude raw JSHSHIR/passport data;
- JSHSHIR and passport data are encrypted at rest;
- `customer` is the customer profile and `customer_document` is document
  metadata plus a storage reference, but the TT section 7 object list is
  expressly an orientation, not an exact schema;
- a Telegram-less lead does not store JSHSHIR or a document image before full
  registration and cannot become active or receive debt.

The TT does not specify the encryption primitive, encrypted-row layout,
blind-index construction, keyring, stale revision, document lifecycle, or
M10 route shape. Those are M10-freeze decisions, not TT-derived facts.

### Document Upload And Access

Authoritative TT sections 2, 6.3, 8, 11, 13, and 14 require:

- S3-compatible private object storage, MinIO for local development, database
  references rather than file bytes, and no direct public URL;
- backend-mediated JPEG/PNG/WebP upload, content-derived MIME validation,
  maximum 10 MB, and EXIF/GPS removal before storage;
- mobile capture markup
  `<input type="file" accept="image/*" capture="environment">`;
- authorization before a temporary presigned GET, default five-minute TTL,
  and private buckets with no public-read;
- stable `FILE_ACCESS_DENIED`, `FILE_STORAGE_ERROR`, `FILE_TOO_LARGE`, and
  `UNSUPPORTED_FILE_TYPE` outcomes;
- tests for false extensions/MIME, size, EXIF removal, absence of public URLs,
  and presign only after authorization.

The TT does not approve raw browser-to-storage upload, presigned PUT, local
disk, an alternate sanitizer, or generic attachment ownership.

### Audit, Privacy, Rendering, And Secrets

Authoritative TT sections 4.9, 8, 10, 11, and 13 require:

- important mutations in an append-only audit journal, with centralized
  redaction before persistence;
- no raw JSHSHIR, passport number, document URL, or image reference in audit
  JSON, and an automated PII-redaction test;
- no browser token or PII storage, no raw PII in structured logs, and no raw
  PII in Telegram messages;
- no-store on PII pages, Jinja autoescape, CSP without inline script, CSRF on
  unsafe requests, server-side authorization, and safe user-facing failures;
- storage and application secrets only from environment/secret management,
  never source control.

### Localization, Errors, And Tests

Authoritative TT sections 6.13, 8-11, 13, and 14 require:

- UZ-Latn as the primary UI and RU support; UZ-Cyrl UI is optional;
- localized safe error messages with stable English internal codes; the TT
  error list is an initial extensible catalog, not a closed enum forever;
- server-rendered mobile-first UI, labels, textual errors, visible focus,
  CSRF, and PRG for mutations;
- real PostgreSQL backend/domain tests, migration walks, authorization/IDOR,
  privacy, upload, CSRF, PRG, auth, and CI coverage.

TT's eventual persisted user language preference is not implemented in the
M9 baseline. M9's authoritative known limitation keeps localization
feature-local; M10.02 does not authorize a generic i18n/profile-locale system.

## Inherited M3 Boundary

The surviving M3 implementation and tests fix these M10 inputs:

| Boundary | Repository evidence | Required M10 consequence |
|---|---|---|
| One customer per auth user | `customers.user_id`, named unique `uq_customers_user_id` | Own customer is resolved server-side from the authenticated user. |
| Draft only | named check `ck_customers_onboarding_status_draft_only` | Identity/document work cannot activate or introduce an `active` transition. |
| PII-free aggregate | exact columns are `id`, `user_id`, `onboarding_status`, `created_at`, `updated_at` | Do not add plaintext identity/document columns to `customers`. |
| No untrusted customer authority | `get_current_customer_draft_state(session, current_user)` accepts no customer identifier | Customer UUID must not be accepted from path, query, or form as own-customer authority. |
| Auth-only web flow | `/customer/onboarding` and `/customer/profile` use current server session | No public or shop-assisted registration surface. |
| Caller-owned transaction | customer repository/service does not commit, fully roll back, or close | M10 repositories/services preserve outer ownership. |
| Safe view | current M3 view exposes draft status and masked phone only; UUID/PII leakage tests are present | New own-user views need an explicit safe allowlist and no-store response. |
| Idempotent creation | PostgreSQL `ON CONFLICT DO NOTHING` on user identity | M10 must not create a second customer or replace M3 ownership semantics. |

M3 supplies no final identity fields, encryption, JSHSHIR uniqueness,
document attachment, activation, customer lead, or `shop_customer` contract.

## Inherited M8 Boundary

M8 is the only storage ingest/sanitization/lifecycle/presign authority that
M10 may consume.

| Area | Exact inherited boundary |
|---|---|
| Ingest | Reuse `ingest_sanitized_image`; it accepts a composed session factory and injected storage, owns its short sessions, and never accepts a request `Session`. |
| Browser transport | Backend-mediated bounded multipart only; raw browser-to-storage, presigned PUT, public upload, local disk, and a second sanitizer are absent and forbidden. |
| Formats/limits | Fully decoded JPEG/PNG/WebP; input and output `<= 10_485_760` bytes, request envelope `<= 11_010_048`, pixels `<= 40_000_000`, each dimension `<= 16_384`, exactly one frame. |
| Sanitization | Content-selected format; verify/reopen/full decode; EXIF transpose; fresh pixel-only same-family re-encode; remove EXIF/GPS/XMP/ICC/comments/thumbnails/source metadata; no resize or fallback. |
| Lifecycle | Only an `AVAILABLE` `object_files` row can be attached/read; upload ambiguity and mismatch remain governed by M8 reconciliation and delete states. |
| Transaction phases | Sanitize with no Session; TX-S1 commit/close; external PUT/HEAD with no Session; fresh TX-S2 commit/close. No Session exists during PUT, HEAD, DELETE, presign, or HTTP fetch. |
| Result sensitivity | Ingest returns object UUID plus safe image metadata in memory; checksum wrapper is redacted. It returns no bucket, key, URL, filename, bytes, ORM entity, or parent. |
| Domain ownership | `created_by_user_id` is accountability only. M8 creates no domain consumer, route, or ownership and does not grant creator read access. |
| Read authorization | Injected concrete parent authorizer must prove actor permission and exact parent-object binding; missing and denied are identical; denial makes zero HEAD/presign calls. |
| Presign | `AVAILABLE` only, default TTL `300s`, URL in a redacted wrapper and never persisted/logged; no presigned PUT. |
| Errors | Reuse the four public file codes; internal provider/image/lifecycle details never alter the safe localized message or expose sensitive values. |
| Tests | Real PostgreSQL plus injected fake adapter; designated real local/CI MinIO only; no real cloud credentials, SQLite, `create_all`, skip/xfail/xpass, or assertion weakening. |

Inherited M8 limitations remain: production provider and production RPO/RTO
are unselected; MinIO is local/CI evidence; legal retention and automatic
purge are deferred; storage may be unavailable while general web health is
green; there is no local-disk fallback. M10 may add only the concrete customer
document parent/route authorized by the M10 freeze, not a generic file vault.

## Inherited M9 Boundary

M9 supplies the central audit/redaction and current presentation/error style,
but no customer identity or document capability.

| Area | Exact inherited boundary |
|---|---|
| Audit input | Immutable typed event with enum event/object type, actor kind/identity, UUID object identity, aware UTC time, and candidate metadata. Pre-serialized arbitrary JSON is not accepted. |
| Audit registry | The seven M9 events and four object types are a closed M9 registry. M10 may extend it only with the exact later-reviewed M10 registry authorized by the freeze, never arbitrary ingestion. |
| Redaction | Event-specific exact allowlists build bounded JSON; unknown keys are removed and missing/invalid required values fail closed. |
| Atomicity | Audit append adds/flushes inside the business outer transaction. Audit failure rolls back the associated mutation; there is no logger/best-effort fallback. |
| Audit surface | No read/search UI, export, mutation API, retention, purge, generic audit platform, or provider I/O. |
| Transaction ownership | Request/coordinator owns commit/full rollback/close. Repository/service may flush and use a savepoint for an exact named expected constraint only. |
| Errors | Extend `app.auth.error_codes:ErrorCode` with stable English codes and safe public mappings; internal detail is discarded. |
| Localization | Feature-local immutable UZ-Latn/RU copy/resolver, UZ-Latn fallback, no generic locale framework. Internal code is not the only rendered message. |
| Rendering | Jinja autoescape, no `Markup`/`|safe`/inline script/event handler/raw HTML, existing CSP, and no-store on every sensitive page, redirect, error, and fragment. |
| Authorization | M9 platform-admin authority is tenant-independent offer administration only. It does not grant another customer's PII/document access. Shop roles likewise grant none. |
| Registration boundary | Authenticated `REGISTRATION` offer acceptance is immutable evidence only; it does not register/activate a customer, collect PII, or create an attachment/shop link. |

For M10 audit/privacy, the inherited minimum is stricter than merely omitting
raw TT fields: names, JSHSHIR, document number, blind index, ciphertext,
nonce, key ID/material, object-file identity, bucket/key/checksum, filename,
presigned URL, raw form, UA, session/cookie/CSRF/token, exception, SQL, and
provider detail remain outside audit/log/error/report/default repr. Exact M10
safe payload keys are a freeze decision to be reconciled in later readiness
tasks, not inferred here.

M9 limitations remain inherited: no public registration/registration OTP,
activation, PII, customer document, `shop_customer`, debt runtime, generic
admin management, audit read surface, retention, or generic localization
framework.

## Source-Derived Versus M10-Freeze Design

This table prevents M10 planning decisions from being mislabeled as TT or
closed-milestone requirements.

| Concern | TT/closed-milestone requirement | M10-freeze-only FINAL design |
|---|---|---|
| Identity storage | Customer PII exists eventually; JSHSHIR/passport are encrypted; M3 `customers` is PII-free. | Separate 1:1 `customer_identities` encrypted row with no plaintext identity columns. |
| Identity fields | Full registration needs F.I.Sh., JSHSHIR, passport/ID. | Exact six fields, canonicalization bounds, and no extra PII. |
| Encryption | Encrypted at rest; secrets outside source. | PyCA `AESGCM`, AES-256-GCM, 32-byte key, random 12-byte nonce, schema/customer-bound AAD, versioned keyring. |
| Duplicate JSHSHIR | Duplicate customer creation is rejected with `DUPLICATE_JSHSHIR`. | Dedicated-key, domain-prefixed HMAC-SHA-256 blind index with DB uniqueness; no plaintext/plain hash or auto-link. |
| Stale identity | TT requires safe/idempotent behavior generally. | Positive revision, expected `0` on create, exact expected revision on update, stale zero-write/audit. |
| Document model | `customer_document` metadata/reference and private authorized file access. | Concrete `customer_documents`, one `CURRENT`, historical `SUPERSEDED`, unique object, submission replay and expected-current checks. |
| Upload workflow | M8 exact backend ingest and no-I/O-in-transaction phases. | TX-A snapshot, M8 ingest, TX-B attach/supersede/audit, then race-safe orphan compensation. |
| Read workflow | M8 concrete-parent authorization is required, creator is insufficient, TTL 300s. | Own-current-customer parent resolution without client customer UUID; `CURRENT` attachment only; access audit by customer-document identity. |
| Completeness | Later activation needs identity/document prerequisites. | `HasCompleteCustomerIdentity` and `HasCurrentCustomerIdentityDocument` queries that do not activate. |
| Audit | TT central redaction plus M9 typed exact registry. | Four exact M10 event names, two object types, and exact safe payload allowlists. |
| Web/errors | TT UZ-Latn/RU, stable localized errors; current feature-local pattern. | Four own-user routes and five new customer identity/document codes, subject to repository reconciliation. |

No proposed item in the right column is implemented or assigned a final
repository symbol by M10.02.

## Contradiction And Reconciliation Inventory

| ID | Apparent contradiction or gap | Reconciliation / disposition |
|---|---|---|
| M10-SRC-01 | M10.00 cites `m3-result.md` and M3 contract files that do not exist in tracked history. | Documentary gap recorded above. Preserve the narrow exact M3 commit/README/migration/code/test intersection; do not invent absent semantics. No product contradiction is present. |
| M10-SRC-02 | TT describes full self/shop-assisted registration and activation; M10 permits only an authenticated user's existing draft. | Milestone staging. Registration, REGISTRATION OTP, shop capture, lead conversion, `shop_customer`, and activation are explicitly deferred. |
| M10-SRC-03 | TT places duplicate handling at full registration and says an existing customer is linked; M10 needs uniqueness during draft identity save. | Earlier fail-closed uniqueness strengthens duplicate prevention. M10 returns only `DUPLICATE_JSHSHIR`; existing-customer disclosure/linking remains deferred. |
| M10-SRC-04 | TT's orientation names `customer` as the encrypted PII profile; M3 requires `customers` to remain PII-free and the freeze selects `customer_identities`. | TT section 7 is expressly not an exact schema. The freeze's separate 1:1 encrypted row is the authoritative M10 layout. |
| M10-SRC-05 | M8 closed with no domain consumer; M10 needs a customer document attachment. | Deliberate dependency handoff. M10 may add one concrete customer-document parent and authorizer while reusing M8 unchanged; generic attachment remains forbidden. |
| M10-SRC-06 | M9's event registry is exact/closed, while M10 needs identity/document events. | A later reviewed milestone may extend the registry. The freeze authorizes only its four exact M10 events/two object types; it does not authorize generic events or audit UI. |
| M10-SRC-07 | TT eventually allows platform-admin full PII view; M10 forbids admin/shop raw PII access. | Deferred capability. Neither `ShopRole` nor `is_platform_admin` is customer PII authority in M10. |
| M10-SRC-08 | TT expects a persisted user language preference; the closed baseline has feature-local localization only. | Preserve the M9 authoritative limitation and implement only a narrow UZ-Latn/RU shell when authorized; generic locale/profile persistence is outside M10. |
| M10-SRC-09 | TT permits safe flash/field errors; M10 forbids sensitive identity values in flash/error. | Render localized generic safe messages and stable codes only. Raw input and sensitive values never enter flash/error. |

No unresolved semantic contradiction requires a scope review at M10.02. The
M3 documentary gap must remain visible in later M10 contract citations; it is
not a basis for expanding M3 or M10.

## M10.02 Disposition

- Source authority is explicit and ordered.
- TT-derived requirements are separated from M10-freeze design decisions.
- M3, M8, and M9 inherited boundaries and deferred capabilities are explicit.
- Informative closure/repository evidence cannot override normative sources.
- No code, schema, dependency, route, final symbol placement, or later M10
  micro-task was started.

Result: `M10.02 PASS — AUTHORITATIVE INVENTORY COMPLETE`.

## M10.03 Current Repository Map

Baseline inspected: `f96b9f0a6d6b506f6715aa354cb4346199f1f5c5`,
clean tracked tree with only this untracked M10 readiness document, one
Alembic head `a9b0c1d2e3f4`, and no M10 implementation artifact.

Status vocabulary in this section:

- `EXISTS / REUSE` is an implemented boundary M10 must call unchanged;
- `EXISTS / EXTEND` is an implemented closed registry/composition surface
  that later M10 work may extend only under the freeze;
- `PATTERN ONLY` supplies repository mechanics, not product semantics;
- `NO REUSE` exists but grants the wrong authority for customer PII;
- `MISSING` records a required future seam, not permission to implement it in
  M10.03 or to invent its contract.

### Runtime DI And Transaction Ownership

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / REUSE | `app/db.py:Base` | Shared SQLAlchemy declarative metadata. | Future M10 models use this one base; no second metadata registry. |
| EXISTS / REUSE | `app/db.py:create_database_engine` | Builds the configured PostgreSQL engine. | No M10 engine or database abstraction. |
| EXISTS / REUSE | `app/db.py:create_database_session_factory` | Returns the shared `sessionmaker[Session]`. | Required input for M8 ingest/delete/presign and M10 short TX phases. |
| EXISTS / REUSE for DB-only request | `app/db.py:create_database_session_dependency` | Dependency yields one Session, commits after successful route completion, fully rolls back on exception, then closes. | Repositories/services borrow it and never commit/full-rollback/close. It must not wrap a route across external storage I/O. |
| EXISTS / REUSE | `app/auth/deps.py:get_database_session` | Bridges request DI to `application.state.get_database_session`; routes use `Depends(..., scope="function")`. | DB-only identity requests may follow the current outer-owner shape. Document I/O needs separate short factory-owned phases. |
| EXISTS / REUSE | `app/auth/deps.py:get_settings` / `get_current_time` | Injects the app settings snapshot and aware UTC request time. | Reuse settings and injected time; no wall-clock read in future domain operations. |
| EXISTS / REUSE | `app/main.py:create_app` | Creates engine/factory, stores `database_engine`, `database_session_factory`, and `get_database_session` on app state, then composes routers. | The session factory already exists at the web composition root. No storage client is constructed here. |
| PATTERN ONLY | `app/telegram/update_processing.py:process_telegram_update_tx_a` / `app/otp/dispatcher.py` short-phase helpers | Factory-owned DB phase, external phase, fresh DB phase. | Confirms the M10 coordinator shape; no worker/dispatcher behavior is imported. |

Critical integration rule: a future document route cannot both depend on the
request `get_database_session` for its whole execution and call external M8
storage within that execution. The current request dependency keeps its
Session alive until route return. The existing composition root instead
exposes `application.state.database_session_factory`, which is the exact
factory seam used by M8 coordinators to close DB phases before provider I/O.

### Customer Aggregate, Service, And Own-User Authority

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / REUSE | `app/customer/models.py:Customer` | `customers` has UUID `id`, unique `user_id`, draft-only status, and aware timestamps; it has no PII/document/shop/activation columns. | Remains the aggregate root and server-resolved parent. |
| EXISTS / REUSE | `app/customer/models.py:CUSTOMER_ONBOARDING_STATUS_DRAFT` | Exact value `draft`, enforced by `ck_customers_onboarding_status_draft_only`. | Identity/document mutation must require this state without adding a transition. |
| EXISTS / REUSE | `app/customer/repository.py:get_customer_by_user_id` | Loads by authenticated user identity. | Existing own-customer lookup; client customer UUID is unnecessary. |
| PATTERN ONLY | `app/customer/repository.py:create_customer_draft_if_missing` | PostgreSQL idempotent insert; caller owns transaction. | Creation semantics stay M3-only; M10 must not create another customer. |
| EXISTS / REUSE | `app/customer/service.py:get_current_customer_draft_state` | Accepts `session, current_user`, resolves by `current_user.id`, and returns a safe view or `None`; no customer identifier parameter. | Exact own-user authority pattern. |
| PATTERN ONLY | `app/customer/service.py:start_customer_draft` / `CustomerDraftStartError` | Thin service and safe generic failure; no commit/full rollback/close. | Mechanics only; identity errors use the reviewed stable catalog rather than leaking input. |
| EXISTS / REUSE | `app/customer/view_model.py:CustomerDraftView` / `build_customer_draft_view` | Frozen explicit allowlist containing masked phone and display status only. | Future own-user PII view needs a separate explicit no-store safe view; do not place PII on this M3 DTO by accident. |
| EXISTS / REUSE | `app/customer/router.py:router` | `/customer` prefix with GET onboarding/profile and POST start only. | Later exact M10 routes may extend this feature surface; M10.03 adds none. |
| EXISTS / REUSE | `app/customer/router.py:onboarding_page` / `profile_page` / `start_onboarding` | Current session authority, CSRF on POST, PRG, safe view context, no-store. | Route mechanics to preserve without inheriting public/shop/admin authority. |
| MISSING | production `require_own_customer` dependency | No standalone own-customer actor/dependency exists. | Current authority is `require_user` plus server-side `get_customer_by_user_id`; later work must not accept a customer UUID to fill this gap. |

### Authentication And Explicit Non-Authorities

| Status | Exact file:symbol | Current contract | M10 consequence |
|---|---|---|---|
| EXISTS / REUSE | `app/auth/deps.py:CurrentSessionContext` / `get_current_session_context` | Resolves server-side session state and carries the authenticated `User`. | Use only as request auth context. Its current `repr` includes session/user UUIDs, so it is not a log/report value. |
| EXISTS / REUSE | `app/auth/deps.py:require_user` | Returns the server-resolved authenticated user or raises `LoginRequired`. | Sole initial actor source for own-user M10 routes. |
| EXISTS / REUSE | `app/auth/deps.py:validate_csrf` | Validates session-bound form/header token for unsafe methods. | Every M10 POST must reuse it, including bounded multipart. |
| EXISTS / REUSE | `app/auth/template_context.py:with_csrf_context` | Adds only the current session's form token to a local template context. | Reuse on mutation forms; never store token or context in browser storage/logs. |
| NO REUSE | `app/offers/authorization.py:PlatformAdminActor` / `require_platform_admin_actor` / `assert_platform_admin_actor` | Tenant-independent authority only for M9 offer administration. | Platform-admin state grants no customer identity/document read or write access. |
| NO REUSE | `app/shop/enums.py:ShopRole` / `app/shop/dependencies.py:require_shop_staff` / `require_shop_owner` | Tenant membership authority for shop workflows. | Owner/manager/cashier status grants no customer PII capture or access. |

### Settings, Redacted Values, And Dependency State

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / EXTEND | `app/settings.py:Settings` | Pydantic settings use `SecretStr`, field/model validators, `hide_input_in_errors=True`, and optional operation-specific bundles. | Only the later reviewed identity crypto/keyring fields belong here; no secret may become a plain repr/error value. |
| EXISTS / REUSE | `app/settings.py:require_object_storage_config` | Fails closed if the optional storage bundle is incomplete and returns one immutable snapshot. | M10 storage operations reuse it through M8; web health stays storage-independent. |
| EXISTS / REUSE | `app/storage/contracts.py:StorageConfig` | Frozen `repr=False` snapshot; endpoint, bucket, access key, and secret key are redacted. | Do not duplicate storage settings or reveal values in M10 errors/reports. |
| EXISTS / REUSE | `app/storage/contracts.py:BucketName` / `ObjectKey` / `ObjectChecksumSha256` / `SanitizedImageBytes` / `PresignedObjectUrl` | Validated wrappers redact default `str`/`repr`; each has one narrow internal/response reveal method. | Values remain inside M8/provider or final response boundary, never audit/log/error/report. |
| EXISTS / REUSE | `app/storage/service.py:PreparedImageUpload` / `IngestedImageResult` | Object UUID, bucket/key, checksum, and bytes are redacted from repr; result is detached. | The object UUID may cross only in memory into the concrete attach phase. |
| MISSING | identity encryption/blind-index settings and redacted keyring wrappers | No active write key, decrypt-key map, lookup key, identity schema version, or identity settings error exists. | Exact settings shape remains a later readiness/implementation task. |
| MISSING / REVIEW REQUIRED | `pyproject.toml` and `uv.lock` PyCA `cryptography`/`AESGCM` dependency | No direct or resolved `cryptography` package is present at the baseline. | M10.03 adds nothing. Any later addition is limited to the freeze-approved reviewed dependency path. |

### Stable Errors And Localization

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / EXTEND | `app/auth/error_codes.py:ErrorCode` / `ERROR_CATALOG` | Stable English enum plus safe UZ-Latn default message and HTTP status. | Later reviewed M10 codes extend this exact catalog; raw input/constraint/key/provider detail never enters definitions. |
| EXISTS / REUSE | `app/auth/error_codes.py:get_error_http_status` / `get_public_error_body` | Public body contains only code/message and discards `internal_detail`. | Exact safe rendering boundary for identity/document failures. |
| EXISTS / REUSE | `app/storage/errors.py:StorageInternalCode` / `get_storage_public_error_code` | Closed internal storage outcomes map to the four public file outcomes. | M10 does not reinterpret provider/image/lifecycle failures. |
| EXISTS / REUSE | `app/storage/errors.py:StorageAccessDeniedError` / `StorageUploadError` | Safe closed exceptions with bounded code-only repr. | Missing/denied and upload errors remain non-disclosing. |
| PATTERN ONLY | `app/offers/web_presentation.py:OfferWebLanguage` / `resolve_offer_web_language` / `get_offer_web_copy` / `get_offer_web_message` | Feature-local frozen UZ-Latn/RU typed copy, UZ-Latn fallback through the OTP resolver, stable safe messages. | Exact localization structure to follow; offer/legal semantics are not reusable. |
| MISSING | customer identity/document web copy and locale resolver | Existing M3 customer templates are not a full UZ-Latn/RU copy contract. | Later work needs a narrow customer feature copy, not generic i18n/profile locale. |

### Central Audit Registry And Persistence

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / EXTEND | `app/audit/contracts.py:AuditEventType` / `AuditObjectType` | Exact seven-event/four-object M9 enums. | Exact M10 events/object types require a synchronized reviewed extension; no arbitrary string registry. |
| EXISTS / EXTEND | `app/audit/contracts.py:_EVENT_OBJECT_TYPES` | Fixed event-to-object mapping. | Every added event must map to its exact object type. |
| EXISTS / REUSE | `app/audit/contracts.py:AuditEvent` | Frozen typed input, aware UTC validation, immutable copied metadata, redacted actor/candidate repr. | Reuse the typed input; never pass serialized JSON or sensitive DTOs. |
| EXISTS / EXTEND | `app/audit/redaction.py:redact_audit_payload` / `_PAYLOAD_BUILDERS` | Per-event builder with exact safe keys; invalid/missing input fails closed and unknown keys do not survive. | M10 safe payload builders must be exact and must omit all PII/crypto/object-storage values. |
| EXISTS / REUSE | `app/audit/repository.py:append_audit_event` / `SqlAlchemyAuditWriter` | Adds and flushes one `AuditLog` in the caller transaction; no commit/full rollback/close/log/I/O. | Same-transaction identity/attachment audit seam. |
| EXISTS / EXTEND | `app/audit/models.py:AuditLog` / `_AUDIT_PAYLOAD_EXACT_SHAPE_SQL` | PostgreSQL checks close event, actor, object, and JSON shape; repr redacts actor/payload. | Contract enum/redaction/model checks and the future Alembic child must change together. |
| MISSING | M10 audit event/object members and payload builders | No identity/document audit name is accepted by Python or PostgreSQL. | Expected baseline state; M10.03 does not extend it. |

### M8 Upload, Delete, And Presign Reuse

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / REUSE | `app/storage/body_guard.py:StorageBodyLimitMiddleware` / `M8_STORAGE_BODY_GUARD_PATHS` | Actual-byte pre-parse guard exists; production protected-path set is empty because M8 has no route. | Later document upload must opt in only its exact path and wire the middleware without affecting unrelated routes. |
| EXISTS / REUSE | `app/storage/multipart.py:bounded_multipart_upload` / `BoundedMultipartUpload` | One file, at most eight auxiliary fields, bounded part/field sizes, cached session CSRF, owned cleanup, redacted file repr. | Exact request parser boundary; no FastAPI `UploadFile` auto-parameter or alternate parser. |
| EXISTS / REUSE | `app/storage/image.py:read_bounded_image` / `sanitize_bounded_image` | Exact M8 bounded decode, one frame, fresh pixels, metadata-free same-family encode. | Reached only through M8 ingest; no M10 image path duplicates it. |
| EXISTS / REUSE | `app/storage/service.py:ingest_sanitized_image` | Factory-owned limiter/TX-S1, provider PUT/HEAD with no Session, fresh TX-S2; returns detached `IngestedImageResult`. | Exact upload entry point. Request adapter supplies server actor, trusted IP, injected time/settings/storage. |
| EXISTS / REUSE outside request compensation | `app/storage/service.py:delete_available_object` | Moves `AVAILABLE` to `DELETE_PENDING`, closes DB phase, deletes/HEADs externally, then records `DELETED` or unknown in a fresh phase. | M10 TX-C does not call it. Request compensation claims `DELETE_PENDING` through the repository primitive; existing reconciliation owns later provider cleanup. |
| EXISTS / REUSE | `app/storage/contracts.py:ObjectReadAuthorizationRequest` / `ObjectFileAccessAuthorizer` | Typed redacted actor/object/parent request and injected authorizer protocol. | M10 constructs it only from server-resolved own current attachment; no untrusted object/customer authority. |
| EXISTS / REUSE | `app/storage/service.py:create_authorized_presigned_get_url` | Authorizer runs before DB object lookup/provider call; loads `AVAILABLE`, closes Session, then presigns for configured TTL. | Exact presign coordinator; concrete parent authorizer must prove attachment/object binding. |
| EXISTS / REUSE | `app/storage/repository.py:load_available_object_file` / `load_object_file_for_update` | Explicit lifecycle read/lock functions. | M10 attachment transaction verifies `AVAILABLE`; external phases stay in service coordinators. |
| EXISTS / REUSE | `app/storage/contracts.py:ObjectStorageService` | Narrow PUT/HEAD/DELETE/presigned-GET/data-plane protocol; no presigned PUT/admin API. | Inject unchanged. |
| EXISTS / REUSE | `app/storage/s3.py:create_s3_client` / `S3ObjectStorageService` | Explicit no-discovery, bounded, single-attempt application data-plane adapter. | Production web composition is still missing; do not create clients at import/startup or add another SDK. |
| PATTERN ONLY | `app/cli.py:_configure_storage_service` | Operation-time config, client, adapter, and close callback composition. | Useful composition evidence only; web must not call CLI or print operational values. |
| MISSING | web storage-provider dependency/factory | `app/main.py:create_app` stores settings and DB factory but constructs no S3 client/adapter. | Later narrow operation-time DI must preserve degraded startup and injected fake testing. |

### Trusted Client IP And Upload Rate Limit

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / REUSE | `app/request_client_ip.py:resolve_client_ip` / `ClientIpResolutionError` | Direct peer or exact trusted-proxy `X-Real-IP`; rejects ambiguity and returns typed canonical IP. | Resolve at request edge before M8 ingest. |
| EXISTS / REUSE | `app/telegram/client_ip.py:ResolvedClientIp` | Redacted wrapper; raw value is available only through `as_hmac_input`. | Never persist/log/report the raw address. |
| EXISTS / REUSE | `app/auth/rate_limit.py:AuthRateLimiter` / `check` / `record_failure` / `hash_rate_limit_key` | PostgreSQL row locking/upsert and HMAC-SHA-256 keys under existing secret; caller owns transaction. | Infrastructure only; no new rate-limit table or raw identity/IP key persistence. |
| EXISTS / REUSE | `app/storage/rate_limit.py:check_storage_upload_rate_limit` / `record_storage_upload_attempt` | Both use the exact storage user/IP buckets and safe `RATE_LIMITED` result; check is read-only, record checks then writes. | CR-M10-01 assigns the check to early M10 RL-CHECK and keeps record authoritative only inside `ingest_sanitized_image`; no duplicate counter or recorder. |

### Templates, Rendering, And Router Composition

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / REUSE | `app/templates/base.html` | Jinja base, dynamic `page_language`, external versioned CSS, no inline script. | Future customer identity/document templates extend it under autoescape. |
| EXISTS / EXTEND mechanics | `app/templates/customer/onboarding.html` / `profile.html` | Explicit fields, labeled structure, CSRF hidden field, safe view attributes, no raw context dump. | Keep onboarding unchanged; M10.68 may add only a localized identity link and PII-free completion status to profile while preserving read-only/no-store M3 behavior. |
| EXISTS / REUSE | `app/customer/router.py:TEMPLATES_DIR` / `templates` | Feature router uses the shared `app/templates` filesystem. | No second template engine or custom unsafe renderer. |
| EXISTS / REUSE | `app/security_headers.py:install_security_headers_middleware` / `set_security_headers` | CSP, frame denial, nosniff, and strict referrer policy. | No CSP weakening or inline code. |
| EXISTS / REUSE | `app/security_headers.py:mark_auth_response_no_store` | Sets `Cache-Control: no-store`. | Apply to every M10 page, redirect, fragment, and error. |
| EXISTS / EXTEND | `app/main.py:create_app` router composition | Includes auth, offers, customer, and shop routers directly. | Later customer feature route stays narrow and exactly once; M10.03 changes no composition. |
| MISSING | identity/document routes and templates | No `/customer/identity*`, file input, multipart production route, or M10 template exists. | Expected baseline state; no route is created here. |

### Alembic And Metadata Wiring

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / EXTEND | `alembic/env.py:target_metadata` | `Base.metadata`; every existing model package is imported explicitly, including customer, storage, audit, and offers. | Later M10 model package import must be explicit; no `create_all` or manual DDL. |
| EXISTS / REUSE | `alembic/env.py:run_migrations_online` / `run_migrations_offline` | Standard Alembic transaction paths using configured PostgreSQL URL. | No M10 migration runner. |
| EXISTS baseline | `alembic/versions/a9b0c1d2e3f4_create_legal_offer_foundation.py` | Single current head, child of M8, containing current audit registry checks. | Future M10 revision must be one linear child; M10.03 invents no revision ID. |
| EXISTS / EXTEND | `app/audit/models.py:AuditLog` constraints plus the current head migration | Python metadata and database checks close the same registry/payload shape. | Future registry extension requires both model metadata and migration check replacement in sync. |
| MISSING | M10 migration and M10 model metadata | No `customer_identities` or `customer_documents` table/model exists. | Expected baseline state. |

### PostgreSQL, Fake Storage, And Test Conventions

| Status | Exact file:symbol | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / REUSE | `tests/conftest.py:test_database_url` | Requires valid `TEST_DATABASE_URL` and an Alembic head; fails rather than skips. | All M10 integration tests inherit this fixture. |
| EXISTS / REUSE | `tests/conftest.py:test_database_engine` / `m2_test_database` | Session engine plus child-first cleanup before/after every test. | Future M10 tables must be added explicitly in FK-safe order. |
| EXISTS / REUSE | `tests/postgresql.py:validate_test_database_url` | PostgreSQL-only, rejects SQLite/non-PostgreSQL and databases not ending `_test`. | No SQLite fallback. |
| EXISTS / EXTEND | `tests/postgresql.py:M2_CLEANUP_TABLE_NAMES` / `cleanup_m2_tables` | Exact allowlisted DELETE order; no generic schema reset. | Later add M10 children before audit/object/customer parents. |
| EXISTS / REUSE | `tests/postgresql.py:get_alembic_head` | Requires one script head. | Exact M10 head checks extend existing tests after a migration exists. |
| EXISTS / REUSE | `tests/storage_fake.py:FakeObjectStorageService` / `FakeStorageOutcome` / `FakeStorageOperation` | Instance-local programmable fake with redacted object state and recorded safe calls. | Inject for deterministic document coordinator/storage failure tests. |
| PATTERN ONLY | `tests/test_customer_service_integration.py` | Caller ownership, own-user isolation, no identifier input, idempotency, independent-session `Barrier`/`ThreadPoolExecutor`. | Exact M10 own-user/revision/concurrency test style. |
| PATTERN ONLY | `tests/test_customer_web_idor.py` / `test_customer_id_urls_are_not_routable` | Two-user route isolation and no customer-ID URLs. | Extend the same threat, not the exact old route inventory. |
| PATTERN ONLY | `tests/test_customer_view_template_safety.py` / `test_customer_pii_leakage.py` | Explicit safe DTO fields, autoescape, safe context, DB/HTML/URL/log leakage canaries. | Foundation for stricter M10 PII/crypto/object-reference leakage tests. |
| PATTERN ONLY | `tests/test_storage_upload_prepare_postgresql.py` / `test_storage_workflow_matrix_postgresql.py` | Proves no Session during provider calls, last committed state, no second PUT, bounded delete/reconcile. | Reuse for attachment phase/compensation assertions. |
| PATTERN ONLY | `tests/test_storage_authorized_presign_postgresql.py` | Parent/object/actor denial, identical missing/non-available outcome, zero SDK on denial, Session closed before presign, URL not persisted/logged. | Exact document-access test shape. |
| PATTERN ONLY | `tests/test_storage_minio_integration.py` | Designated local MinIO data-plane acceptance, privacy, no presigned PUT, exact TTL, cleanup. | Only approved real storage integration surface. |
| PATTERN ONLY | `tests/test_audit_contracts.py` / `test_audit_redaction.py` / `test_audit_model_metadata.py` | Exact registry, event/object mapping, payload allowlists, unknown-sensitive-key dropping, DB checks, redacted repr. | Extend exactly for reviewed M10 audit members. |
| PATTERN ONLY | `tests/test_offer_migration_postgresql.py` / `test_storage_head_wiring.py` | Real empty/populated parent-child migration walk, exact schema inventory, one head, cleanup/CI wiring. | Future M9-to-M10-to-M9-to-M10 evidence. |
| MISSING | M10 unit/integration/web/MinIO tests | No M10 implementation test exists and none is skipped/xfail placeholder. | Expected baseline; M10.03 adds no test. |

### CI Integration Map

| Status | Exact file:key/step | Current contract | M10 integration point |
|---|---|---|---|
| EXISTS / REUSE | `.github/workflows/ci.yml:jobs.dependency-sync` | Single Ubuntu job with PostgreSQL 16 service and testing env. | Keep one job; no SQLite or separate weakened path. |
| EXISTS / REUSE | `Sync dependencies` | `uv sync --dev --frozen`. | Later dependency change must update lock and remain frozen. |
| EXISTS / EXTEND | `Run Alembic migrations` / `Show Alembic current revision` / `Verify Alembic M9 head` | Applies real migrations and asserts exact `a9b0c1d2e3f4`. | Exact assertion changes only after an approved M10 migration exists. |
| EXISTS / REUSE | `Run Ruff` | `ruff check .` plus `ruff format --check .`. | Mandatory unchanged gates. |
| EXISTS / REUSE | generated/masked MinIO setup and pinned runtime/init steps | Root/admin plane is isolated; app tests receive scoped credentials. | Reuse current local MinIO boundary, never add real cloud credentials. |
| EXISTS / REUSE | `Run narrow private MinIO integration` | Runs only `tests/test_storage_minio_integration.py` against local MinIO. | Later approved M10 MinIO acceptance may use the same runtime without weakening this matrix. |
| EXISTS / REUSE | M5/M6/M7 containment steps | Preserves prior roles and fake-runtime boundaries. | M10 adds no worker/OTP/shop capability. |
| EXISTS / REUSE | `Run full pytest` | Runs `pytest -ra`, fails on test failure and any skipped/xfailed/xpassed summary. | All M10 automated tests join the full real-PostgreSQL suite. |
| EXISTS / REUSE | `Clean up MinIO test runtime` | `if: always()`, removes runtime volume and temporary secret/evidence files. | Preserve cleanup and secret isolation. |
| MISSING | M10 crypto env, exact head assertion, focused tests, or acceptance step | CI contains no M10 setting or implementation evidence. | Expected baseline; M10.03 changes no workflow. |

### M10.03 Missing Surface Audit

The baseline has no:

- `app/customer_identity/` or `app/customer_document/` package;
- identity crypto/canonicalization/keyring/blind-index/completeness symbol;
- `customer_identities` or `customer_documents` model/table/migration;
- identity/document audit event, object type, payload builder, or DB check;
- web storage dependency, own-customer identity/document actor, M10 route,
  template, body-guard path, localized copy, or stable M10 error member;
- M10 unit, real PostgreSQL, fake-storage, MinIO, browser, containment, or CI
  evidence.

These are expected unimplemented integration surfaces, not scope ambiguity.
The direct `cryptography` dependency is also absent and remains a later
reviewed dependency-feasibility question; no custom primitive or fallback is
authorized.

## M10.03 Disposition

- Exact current file:symbol reuse and no-reuse points are mapped.
- Request-owned versus factory-owned transaction seams are explicit.
- Own-user authority resolves customer/object parents server-side.
- M8 ingest/delete/presign remains the single storage implementation.
- Settings, audit, errors, localization, Alembic, fixtures, fake MinIO, and CI
  extension points are explicit without implementing them.
- No code, schema, dependency, route, test, CI, or later M10 task was started.

Result: `M10.03 PASS — CURRENT REPOSITORY MAP COMPLETE`.

## M10.04 Customer Draft Ownership Audit

This section audits the surviving M3 implementation against the authoritative
M10.00 own-customer and draft-only boundary. It records current facts and a
narrow future integration recommendation; it adds no repository symbol,
schema, route, or behavior.

### Schema, Uniqueness, And Status Vocabulary

| Invariant | Exact repository evidence | Audit result |
|---|---|---|
| Minimal aggregate | `app/customer/models.py:Customer` has only `id`, `user_id`, `onboarding_status`, `created_at`, and `updated_at`; `tests/test_customer_model_metadata.py:test_customers_table_has_only_draft_foundation_columns` and `test_customers_table_has_no_pii_activation_or_shop_columns` freeze that surface. | M3 remains a PII-free customer root; M10 must not add identity/document fields to `customers`. |
| One customer per user | `app/customer/models.py:Customer.__table_args__` and `alembic/versions/b1f3a7c9d2e4_create_customers_table.py:upgrade` both define named `uq_customers_user_id`; `user_id` is non-null and references `users.id` with `ON DELETE RESTRICT`. | A server-resolved user maps to zero or one customer. Customer UUID is not needed to disambiguate ownership. |
| Database-enforced uniqueness | `tests/test_customer_db_constraints.py:test_customer_unique_user_draft_allows_one_per_user_and_separate_users` proves duplicate rows for one user fail while separate users remain isolated on real PostgreSQL. | Repository `scalar_one_or_none()` is deterministic under the database constraint; application-only uniqueness is not relied upon. |
| Exact status vocabulary | `app/customer/models.py:CUSTOMER_ONBOARDING_STATUS_DRAFT` is the single value `draft`; model and migration both define named `ck_customers_onboarding_status_draft_only` as exact equality. | M3 has no pending/active transition vocabulary. M10 may require `draft` but may not create an active transition or broaden this check. |
| Status enforcement | `tests/test_customer_model_metadata.py:test_customers_onboarding_status_is_draft_only` checks metadata; `tests/test_customer_db_constraints.py:test_customer_status_check_rejects_non_draft_values` rejects `active`, empty, and `pending` in PostgreSQL. | Draft-only is a database invariant, not merely a display convention. A service check is still required to fail closed if later milestones widen the database vocabulary. |
| Parent lifecycle | `tests/test_customer_db_constraints.py:test_customer_requires_existing_user`, `test_customer_restricts_parent_user_delete_until_customer_deleted`, and `test_customer_user_relationships_do_not_delete_cascade` prove a real user is required and implicit cascade deletion is absent. | M10 children must attach to the already resolved customer root and preserve historical `RESTRICT` behavior. |

The uniqueness relevant to M10 ownership is `customers.user_id`, not the
customer UUID and not a shop role. The inherited auth schema also uniquely
indexes `users.phone`, but phone is neither an M10 route selector nor customer
authority.

### Current Own-User Resolution

| Boundary | Exact repository evidence | Audit result |
|---|---|---|
| Authenticated active actor | `app/auth/deps.py:get_current_session_context` resolves the cookie through the database; `app/auth/sessions.py:resolve_by_raw_token` returns no authenticated user when `User.is_active` is false; `app/auth/deps.py:require_user` returns only that private resolved `User`. | The actor comes from server session state, not a submitted user/customer identifier. |
| Repository lookup | `app/customer/repository.py:get_customer_by_user_id` selects only `Customer.user_id == user_id` and returns zero or one row. | It is an own-user lookup only when its `user_id` argument came from the trusted auth boundary. It accepts no customer UUID, but it is not itself an authorizer. |
| Service read resolver | `app/customer/service.py:get_current_customer_draft_state(session, current_user)` derives the lookup key from `current_user.id`; its signature has no customer identifier. | Current read behavior is own-only and missing-safe, but non-locking and unsuitable as the M10 mutation primitive. |
| Route authority | `app/customer/router.py:onboarding_page`, `profile_page`, and `start_onboarding` call `require_user(context)` and pass only the returned user or `user.id`; all routes have fixed paths. | Form, query, and path data cannot choose the customer owner. |
| IDOR evidence | `tests/test_customer_web_idor.py:test_customer_web_flow_is_own_only_for_two_authenticated_users` submits forged `user_id`/`customer_id` fields and proves they do not affect ownership; `test_customer_id_urls_are_not_routable` proves customer-ID paths do not exist. | Untrusted identifiers are ignored structurally, and two authenticated users remain isolated. |
| Signature/route containment | `tests/test_customer_service_integration.py:test_current_customer_draft_state_accepts_no_customer_identifier` and `tests/test_customer_router_wiring.py:test_customer_routes_forbid_external_ids_and_scope_drift` pin the no-selector boundary. | M10 must extend this threat model, not introduce a generic customer-by-ID endpoint. |

`ShopRole`, an active shop selection, or a platform-admin bit is absent from
the M3 customer resolver and grants no alternate customer authority. M10 must
not compose a shop/admin dependency around this resolver to obtain another
user's customer.

### Idempotent Draft And Transaction Contract

| Contract | Exact repository evidence | Audit result |
|---|---|---|
| Idempotent create | `app/customer/repository.py:create_customer_draft_if_missing` uses PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` on `Customer.user_id`, then resolves by that same user ID. | Sequential and concurrent starts converge on the one database-enforced row without exception-driven full rollback. |
| Concurrency | `tests/test_customer_service_integration.py:test_start_customer_draft_parallel_duplicate_create_is_idempotent` uses independent PostgreSQL sessions and proves one customer ID and a usable transaction per worker. | M3 draft creation is race-safe for its narrow start operation. This does not supply the row lock needed by later identity/document mutations. |
| Existing-row stability | `test_start_customer_draft_is_sequentially_idempotent_without_rollback` and repository tests prove repeat starts retain ID, timestamps, status, and one-row cardinality. | Replaying the M3 start does not mutate the draft. |
| No GET creation/touch | `tests/test_customer_get_side_effects.py:test_no_draft_customer_gets_redirects_and_discovery_do_not_create_rows` and `test_customer_gets_do_not_touch_existing_draft_timestamps` prove discovery is read-only. | M10 GET and completeness reads must not auto-create or touch the draft. |
| Caller-owned unit of work | `app/customer/repository.py` and `app/customer/service.py` issue no `commit()`, full `rollback()`, or `close()`; caller-ownership tests in `tests/test_customer_repository_integration.py` and `tests/test_customer_service_integration.py` prove isolation, rollback, commit, continuation, and no lifecycle takeover. | Future repository/service functions may select, lock, add, and flush only. Request routes or explicit coordinators remain outer transaction owners. |
| Request owner | `app/db.py:create_database_session_dependency` commits after a successful dependency yield, rolls back on exception, and closes in `finally`; `app/auth/deps.py:get_database_session` delegates to it. | A DB-only M10 request can reuse this owner. Document storage phases must instead use short factory-owned transactions and close them before external I/O. |

The existing `start_customer_draft(session, user_id)` is intentionally a draft
creation command. M10.00 requires an **existing** M3 draft, so M10 identity and
document paths must not call it, emulate its upsert, or convert a missing row
into a new customer.

### Deterministic Own-Draft Resolve/Lock Recommendation

For each future M10 identity mutation and each document TX-A/TX-B, add one
narrow repository operation with semantics equivalent to:

```text
lock_own_customer_draft(session, *, actor_user_id)
```

The name and placement remain subject to the M10.08 contract, but the boundary
is already deterministic:

1. Obtain the active authenticated `User` through
   `get_current_session_context` plus `require_user`; derive
   `actor_user_id = current_user.id` in trusted application code.
2. Accept no `customer_id` or `user_id` from form, query, path, headers, or
   template state. Do not substitute ShopRole or platform-admin authority.
3. Execute one `SELECT Customer WHERE Customer.user_id = actor_user_id FOR
   UPDATE`. Resolve with `scalar_one_or_none()` under `uq_customers_user_id`.
4. If missing, return the safe `CUSTOMER_DRAFT_REQUIRED` outcome with no
   create, mutation, or audit event.
5. After the row lock, require exact
   `CUSTOMER_ONBOARDING_STATUS_DRAFT`. Any other status fails with the same
   safe draft-required outcome and zero mutation/audit. This explicit check
   remains mandatory even though today's database permits only `draft`.
6. Only after those checks may trusted code derive `Customer.id` internally
   for identity/document FKs, crypto binding, completeness checks, or parent
   authorization. That UUID is never promoted to client authority.
7. Lock order is customer first, then the M10 child row/current-document set.
   Every competing mutation uses the same order. Expected revision,
   submission ID, or expected-current-document ID may be comparison/replay
   tokens only after ownership is fixed; none selects the customer.
8. The repository/service performs no commit, full rollback, or session close.
   A DB-only request owns its outer transaction. Document TX-A and TX-B each
   open, commit/rollback, and close a short coordinator-owned session; no
   session or transaction survives into M8/external storage I/O.

Non-mutating own-summary reads may retain the existing non-locking resolver,
provided they derive ownership from `current_user` and remain no-store.
Mutation/completeness decisions that must serialize with identity/document
writes use the locked resolver and recheck within the same transaction.

### Audit Findings

- PASS: schema and PostgreSQL constraints make the user-to-customer mapping
  zero-or-one and status exactly draft-only.
- PASS: current routes and service reads derive the customer from the
  server-resolved active user and accept no customer selector.
- PASS: draft creation is idempotent and repository/service transaction
  ownership is caller-controlled.
- EXPECTED GAP: no `FOR UPDATE` own-draft resolver exists. The present
  `get_customer_by_user_id` is read-only and must not be reused alone for M10
  mutation serialization.
- EXPECTED GAP: M3 `start_customer_draft` creates a missing draft; M10 must use
  a distinct existing-only resolver and fail closed instead.
- No contradiction or blocker was found. The missing lock function is planned
  M10 integration work, not authority to implement it in M10.04.

## M10.04 Disposition

- Own-customer authority is the authenticated active user's server-resolved
  `User.id`, never a client/customer/shop/admin selector.
- Own-draft resolution is deterministic through `uq_customers_user_id`.
- Mutation locking is customer-first `FOR UPDATE`, followed by exact `draft`
  validation and only then internal child/object work.
- Missing/non-draft resolves to `CUSTOMER_DRAFT_REQUIRED` with zero creation,
  mutation, or audit.
- Focused M3 model, PostgreSQL constraint/repository/service, router, IDOR, and
  GET-side-effect verification is green: `49 passed`.
- No product code, schema, route, test, dependency, CI, or later M10 task was
  started.

Result: `M10.04 PASS — OWN DRAFT BOUNDARY DETERMINISTIC`.

## M10.05 Crypto, Dependency, And Settings Readiness Audit

This section fixes one dependency strategy and one redacted configuration
shape before crypto implementation. It does not add a dependency, regenerate
the lock, add settings fields, or implement a cryptographic operation.

### Current Dependency Evidence

| Surface | Read-only evidence | Audit result |
|---|---|---|
| Python/runtime policy | `pyproject.toml:project.requires-python` is `>=3.12`; runtime dependencies use direct lower bounds and, where compatibility-sensitive, a next-major upper bound. | PyCA's current Python support includes 3.12, so no interpreter change is needed. |
| Direct dependency | `pyproject.toml:project.dependencies` has no `cryptography` or other AEAD package. | The frozen permission for exactly one reviewed direct crypto dependency is applicable. |
| Locked graph | `uv.lock` has no `cryptography` package and the local project package has no such `requires-dist`; `uv tree --locked --depth 1` confirms it is absent. | Neither the frozen environment nor its lock currently supplies `AESGCM`. |
| Local environment | The repository `.venv` reports neither an importable `cryptography` module nor installed `cryptography` distribution. | An undeclared ambient package cannot mask the dependency gap. |
| Existing `cffi` | `uv.lock:cffi` is inherited through `argon2-cffi-bindings` for the password stack. | `cffi` is not an AEAD API and must not be promoted to a direct dependency or used to construct crypto. |
| Reproducibility | `.github/workflows/ci.yml:Sync dependencies` runs `uv sync --dev --frozen`; `uv.lock` records exact artifacts and hashes. | A future approved dependency change must update `pyproject.toml` and `uv.lock` atomically, then pass the existing frozen sync. |

As of 2026-08-01, official PyPI identifies PyCA `cryptography` `50.0.0` as
the current stable release, published 2026-07-31, with Python 3.12 support.
The official API documents `AESGCM`, 128/192/256-bit keys, nonce-reuse
prohibition, and `InvalidTag` on key/nonce/AAD/ciphertext authentication
failure. Sources: [PyPI project metadata and releases](https://pypi.org/project/cryptography/),
[PyCA 50.0.0 changelog](https://cryptography.io/en/stable/changelog/), and
[PyCA authenticated-encryption API](https://cryptography.io/en/stable/hazmat/primitives/aead/).

### Single Approved Dependency Strategy

The one future direct runtime dependency is exactly:

```text
cryptography>=50.0.0,<51
```

- `cryptography` is the PyCA-owned package; similarly named packages are not
  substitutes.
- `>=50.0.0` selects the reviewed current stable line and `<51` prevents an
  unreviewed next-major upgrade. The generated `uv.lock` supplies the exact
  resolved version, artifacts, and hashes; at this audit date that resolution
  is `50.0.0`.
- No second crypto dependency, direct `cffi`, OpenSSL package, OS crypto
  wrapper, optional extra, vendored primitive, or runtime fallback is added.
- M10 uses only
  `cryptography.hazmat.primitives.ciphers.aead.AESGCM` for AEAD. It does not
  replace the frozen primitive with Fernet, low-level `Cipher`/GCM,
  AES-GCM-SIV, or a custom construction.
- The application supplies an exact 32-byte key and unique random 12-byte
  nonce under the frozen AAD contract. PyCA returns ciphertext with its
  authentication tag; M10 neither truncates nor handles a tag separately.
- Standard-library `hmac` plus `hashlib.sha256` remains the only blind-index
  primitive. It is not an AEAD fallback.
- The later dependency implementation is one atomic review of only
  `pyproject.toml` plus `uv.lock`; if this range cannot resolve/install under
  the existing Python/CI platforms, work is `BLOCKED` for scope review rather
  than adding another package.

### Exact Redacted Settings Input Contract

The existing `app/settings.py:Settings` remains the sole environment loader.
The future M10 extension is exactly this optional-at-base, atomic-at-operation
bundle; uppercase names are its environment representation:

| Settings field | Environment name | Raw type/default | Meaning |
|---|---|---|---|
| `customer_identity_active_key_id` | `CUSTOMER_IDENTITY_ACTIVE_KEY_ID` | `SecretStr \| None = None` | Exact active write-key selector. It is secret-wrapped because key IDs are forbidden in repr/log/error/report. |
| `customer_identity_encryption_keys` | `CUSTOMER_IDENTITY_ENCRYPTION_KEYS` | `SecretStr \| None = None` | One strict JSON object mapping every active/historical key ID to one encoded 32-byte AES key. Wrapping the entire source hides both IDs and material from settings dumps. |
| `customer_identity_blind_index_key` | `CUSTOMER_IDENTITY_BLIND_INDEX_KEY` | `SecretStr \| None = None` | One separately encoded, exact 32-byte HMAC lookup key. |

There is no environment-configurable algorithm, nonce, AAD, schema version,
fallback ID, default key, filesystem path, remote KMS selector, or rotation
command. AES-256-GCM, 12-byte nonce, AAD format, and schema version `1` are
code/domain constants from M10.00 rather than operator choices.

Encoding and mapping rules are exact:

- encryption mapping input is a JSON object only; empty objects, arrays,
  scalars, duplicate JSON keys, and trailing/non-JSON data are rejected;
- each mapping key and the active selector is 1–64 ASCII characters matching
  `[A-Za-z0-9._-]+`; values are not trimmed or canonicalized;
- every key value and the blind-index value is canonical padded RFC 4648
  standard Base64, decoded with strict validation, and must decode to exactly
  32 bytes;
- the mapping is non-empty, the active selector must match exactly one mapping
  key, duplicate decoded AEAD key material is rejected, and no fallback key is
  selected;
- the decoded blind-index key must differ from every AEAD key and is never
  reused as the rate-limit or OTP HMAC secret;
- production values come only from environment/secret management. Source,
  migration, fixtures, `.env.example`, CI output, documentation, and defaults
  contain no key material or configured key ID.

### Runtime Snapshot And Secret Wrappers

The current repository supplies the required style:

- `app/settings.py:Settings.model_config` has
  `hide_input_in_errors=True`; current secret fields use `SecretStr`;
- `TelegramWorkerCredentials` and `app/storage/contracts.py:StorageConfig`
  are frozen `repr=False` snapshots with explicit redacted repr;
- `Settings.require_otp_hmac_key` and `require_object_storage_config` are
  operation-time complete-bundle gates with typed, constant-detail errors;
- `tests/test_settings.py`, `tests/test_storage_settings.py`, and
  `tests/test_otp_secret_leakage.py` exercise repr, model-dump, validation,
  error, and log canaries.

M10 follows that style with one narrow
`require_customer_identity_crypto_config()` operation-time gate and a frozen,
slots, `repr=False` `CustomerIdentityCryptoConfig` snapshot. The snapshot:

- converts the active selector to a redacted key-ID value object;
- holds decoded keys as `SecretBytes` in an immutable mapping;
- exposes only an active-write-key accessor, exact stored-key lookup for
  decrypt, and a blind-index-key accessor to the crypto boundary;
- has no public mapping dump, iterator, `asdict`, fallback lookup, mutation,
  hot reload, or generic KMS/provider interface;
- renders only a constant redacted representation: no ID, material, mapping,
  count, encoded source, or fingerprint.

The raw settings fields remain optional so the inherited base web startup and
`/health` do not gain a crypto prerequisite. Identity capability is not
degraded to plaintext: every identity read/write/completeness operation must
obtain the complete snapshot before reading or parsing submitted PII, opening
a database transaction, decrypting, or appending audit.

### Fail-Closed Configuration Semantics

| Condition | Required outcome |
|---|---|
| All three raw settings absent | Base web/health may start; every identity crypto operation fails before PII parsing or side effects. |
| Partial bundle | The operation gate raises one typed redacted settings error; no default/generation/fallback. |
| Wrong raw type, outer whitespace, malformed/duplicate-key JSON, invalid ID syntax, non-canonical Base64, wrong decoded length, empty mapping, missing active selector, duplicate material, or key reuse | Reject without echoing the input, ID, material, mapping position, or validation detail. No crypto/DB/audit/storage work. |
| Complete valid bundle | Build one immutable snapshot; the active mapping entry is the sole write key, while decryption resolves only the exact stored ID across the full mapping. |
| Stored row has unknown key ID | Fail as internal identity unavailability; do not try active/first/last/any other key and do not expose the stored ID. |
| PyCA raises `InvalidTag`, `ValueError`, or a crypto/backend error | Collapse to typed internal identity unavailability, discard cause/detail, and emit no plaintext, crypto metadata, or partial result. |

The settings error message is constant and contains only that customer
identity cryptography is unavailable. At the HTTP boundary it maps to the
already frozen safe `CUSTOMER_IDENTITY_UNAVAILABLE` policy or generic internal
error policy; configuration variants are intentionally indistinguishable.
Repository/service transaction ownership is unchanged because configuration
is resolved before a session/transaction exists.

### Required Later Verification (Not Implemented Here)

- dependency inventory proves exactly one direct `cryptography` range and
  exact locked resolution; `uv sync --dev --frozen` succeeds;
- settings absent/partial/malformed/duplicate/length/key-reuse matrices fail
  closed without secret or key-ID leakage through repr, str, dump, error, log,
  traceback text, or source fixtures;
- snapshot immutability and exact active/historical lookup are pinned; unknown
  IDs have zero fallback attempts;
- synthetic AESGCM vectors cover round trip, random nonce divergence, wrong
  key/AAD/customer, tamper, and truncation; no real PII fixture is used;
- source/AST containment rejects custom cipher construction, alternative AEAD,
  key literals, logging/printing/asdict, and config access outside the narrow
  boundary.

## M10.05 Disposition

- Current baseline has no PyCA `cryptography`; the approved single dependency
  strategy is `cryptography>=50.0.0,<51` with exact `uv.lock` resolution.
- Three secret-wrapped inputs form one immutable operation-time keyring plus
  dedicated blind-index-key snapshot; all parsing and errors are redacted.
- Missing, partial, malformed, unknown-key, or authentication failure has no
  default/fallback and performs zero PII parse, DB, audit, or storage work.
- Existing settings/storage/OTP redaction and fail-closed verification is
  green: `193 passed`.
- No code, dependency, lockfile, settings, `.env`, fixture, or later M10 task
  was changed or started.

Result: `M10.05 PASS — CRYPTO/DEPENDENCY/SETTINGS PLAN UNAMBIGUOUS`.

## M10.06 M8 Storage Integration And Compensation Audit

This section audits the exact M8 upload, lifecycle, delete, and authorized-read
surfaces against M10.00's domain attachment coordinator and applies
`CR-M10-01 — FINAL APPROVED`. The correction amends only PO-M10-14 rate-limit
placement and PO-M10-15 immediate-delete compensation. It does not change the
20/20 FINAL decisions, one capability, two-table boundary, eight checkpoints,
M8 implementation, or M10 product code.

### Exact M8 Reuse Inventory

| Concern | Exact existing symbol | Closed behavior M10 must preserve |
|---|---|---|
| Pre-parse envelope | `app/storage/body_guard.py:StorageBodyLimitMiddleware` | Counts actual request bytes and rejects over `11_010_048` before multipart parsing. `M8_STORAGE_BODY_GUARD_PATHS` is intentionally empty because M8 has no production route; the future single M10 upload path must opt in without changing the bound. |
| Multipart/CSRF | `app/storage/multipart.py:bounded_multipart_upload` / `BoundedMultipartUpload` | Parses one file, at most eight fields, bounds the file and auxiliary values, reuses cached-form session CSRF validation, redacts the upload, and always closes the form/file. No automatic FastAPI `UploadFile` parameter may bypass it. |
| Trusted IP | `app/request_client_ip.py:resolve_client_ip` / `app/telegram/client_ip.py:ResolvedClientIp` | Produces the only admissible upload-IP input; raw IP is not persisted, logged, or returned. |
| Early rate check | `app/storage/rate_limit.py:check_storage_upload_rate_limit` | Read-only check over the exact M8 user/IP scopes, keys, settings, HMAC limiter, and safe `RATE_LIMITED` result. CR-M10-01 assigns it to a short pre-TX-A transaction; it records nothing. |
| Authoritative attempt record | `app/storage/rate_limit.py:record_storage_upload_attempt` | Checks both exact M8 buckets, records both only when allowed, and is already called by `prepare_sanitized_image_upload` before source read. M10 never calls it directly. |
| Upload entry point | `app/storage/service.py:ingest_sanitized_image` | The only M8 upload capability. It accepts a `session_factory`, borrowed async source, authenticated actor ID, trusted IP, injected time/settings/provider, and returns a redacted `IngestedImageResult`. |
| Sanitization | `prepare_sanitized_image_upload`, `read_bounded_image`, and `sanitize_bounded_image` | Complete config and user/IP rate admission precede bounded read; image decode/re-encode uses M8's sole sanitizer and never writes source/sanitized bytes to DB or disk. |
| Upload lifecycle | `create_pending_object_file`, `mark_object_file_available`, `mark_pending_upload_outcome_unknown`, `mark_object_file_failed`, and mismatch-delete transitions | Exact `PENDING_UPLOAD -> AVAILABLE/FAILED/DELETE_PENDING -> DELETED` state machine with row locks and safe failure codes. M10 adds only a concrete attachment row; it does not copy or reinterpret lifecycle state. |
| Provider boundary | `app/storage/contracts.py:ObjectStorageService` | Narrow PUT/HEAD/DELETE/presigned-GET protocol. Provider is injected and caller-owned; no presigned PUT, retry, discovery, local disk, or alternate SDK. |
| Internal delete | `app/storage/service.py:delete_available_object` | Locks `AVAILABLE`, commits `DELETE_PENDING`, closes the session, performs one DELETE and at most the required HEAD, then finishes in a fresh transaction. Missing is success; ambiguity stays `DELETE_PENDING/DELETE_OUTCOME_UNKNOWN`. |
| Lifecycle claim primitive | `app/storage/repository.py:mark_object_file_delete_pending` | Public caller-transaction-owned repository primitive; for an `AVAILABLE` row, `failure_code=None` performs the exact `DELETE_PENDING` transition and no provider I/O. CR-M10-01 assigns this primitive to M10 TX-C. |
| Delete recovery | `reconcile_stale_object_deletes` / `claim_stale_delete_pending` | Bounded deterministic `FOR UPDATE SKIP LOCKED` claim; session closes before HEAD/DELETE; no scheduler is supplied. |
| Authorized read | `ObjectReadAuthorizationRequest`, `ObjectFileAccessAuthorizer`, and `create_authorized_presigned_get_url` | Domain authorizer runs first; denied and missing are identical; only `AVAILABLE` loads; DB session closes before presign; creator UUID alone is never authority. |
| Safe URL/result | `PresignedObjectUrl`, `IngestedImageResult`, `StorageDeleteResult` | URL, object ID, bucket/key, and checksum are redacted or absent from repr. Presigned URL exists only long enough for the authorized response and is never persisted/logged. |
| DB construction | `app/db.py:create_database_session_factory` | Returns the established SQLAlchemy `sessionmaker`; M8 coordinators use `session_factory.begin()` for their own short commit/rollback/close phases and never accept a request `Session`. |

Focused real-PostgreSQL/injected-fake evidence is green: upload/session/failure,
internal-delete/reconcile, authorized-presign, workflow-race, multipart, and
body-guard suites report `78 passed`. In particular:

- `test_source_and_provider_boundaries_have_no_open_session` pins no Session
  during source read, sanitize, PUT, or HEAD;
- `test_internal_delete_commits_pending_before_one_external_delete` pins no
  Session during DELETE;
- `test_coordinator_closes_db_phase_before_presign` pins
  `authorize -> DB open/close -> presign`;
- workflow/delete concurrency tests pin no presign after `DELETE_PENDING`, one
  direct-delete provider call, bounded reconciliation, and no resurrection.

### Existing M8 Phase Semantics

`ingest_sanitized_image` currently executes:

```text
validate config
M8-RL: short factory-owned rate-limit transaction; close
bounded source read + sanitize; no Session
TX-S1: create PENDING_UPLOAD; commit/close
provider PUT once + HEAD; no Session
TX-S2: lock lifecycle row and persist outcome; commit/close
return only committed AVAILABLE result
```

Definite PUT failure marks `FAILED` without HEAD. Ambiguous PUT never causes a
second PUT: exact HEAD becomes `AVAILABLE`, missing/failing HEAD stays
`PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN`, and metadata mismatch follows the M8
delete path. Required TX-S2 failure returns no object and leaves the last
committed row for bounded recovery. Provider exceptions are unchained and
collapse to stable file errors without provider detail.

`delete_available_object` currently executes:

```text
TX-D1: lock ObjectFile; AVAILABLE -> DELETE_PENDING; commit/close
provider DELETE; if needed HEAD; no Session
TX-D2: DELETE_PENDING -> DELETED or record DELETE_OUTCOME_UNKNOWN; commit/close
```

A call that observes an already `DELETE_PENDING` row returns that state and
does not itself resume provider deletion; the bounded stale-delete reconciler
owns later takeover.

### Corrected M10 Upload/Attach Plan

CR-M10-01 makes the acceptable coordinator order executable:

```text
request byte guard
authentication + session touch
bounded multipart parse + session-bound CSRF
trusted client-IP resolution
close all request-auth DB state; retain only detached redacted inputs
RL-CHECK: short transaction; check_storage_upload_rate_limit only; close
TX-A: own-draft/current/replay snapshot; commit/close
M8 ingest_sanitized_image: authoritative attempt record exactly once
TX-B: attach/supersede/audit; commit/close
on post-ingest TX-B failure: TX-C atomically claim unattached DELETE_PENDING
later operator/CLI: existing reconcile_stale_object_deletes
```

Request/auth boundary:

1. Register only the concrete M10 upload POST path with
   `StorageBodyLimitMiddleware`; no wildcard or generic upload route.
2. Resolve the active authenticated user from the server cookie and resolve
   the trusted IP. Form/path/query data supplies neither actor, customer, nor
   object authority.
3. Do **not** inject `get_database_session` or today's
   `get_current_session_context` generator into a route that performs provider
   I/O. That dependency keeps its Session alive until the route returns and
   therefore violates the network-I/O invariant.
4. A later narrow request-security coordinator must reuse auth/session-touch
   and CSRF primitives in a short factory-owned DB phase, copy only a frozen
   redacted actor/CSRF verification snapshot, commit/close, and leave no ORM
   object/session behind before storage work. This is an auth DI seam, not a
   second authentication mechanism.
5. `bounded_multipart_upload` remains the only parser and owns file closure.

RL-CHECK, using one fresh `session_factory.begin()`:

1. Call `check_storage_upload_rate_limit` with the exact same typed `Settings`,
   authenticated actor, trusted `ResolvedClientIp`, and aware timestamp that
   will be passed to M8 ingest.
2. A blocked result returns exact `RATE_LIMITED`; TX-A, source read, object-row
   creation, and provider calls do not occur. The check writes no attempt.
3. Commit/close before TX-A. SQL/config failure maps through the established
   safe storage error boundary; no limiter detail or key is exposed.
4. Do not call `record_storage_upload_attempt` from M10. The later M8 ingest
   remains the only recorder and final enforcement point, preventing double
   charge while closing a concurrent-request race.

TX-A, using one fresh `session_factory.begin()`:

1. Lock the server-resolved own customer first and require exact `draft`.
2. Lock/read the current customer-document set in deterministic order.
3. Compare the submitted `expected_current_document_id` only after ownership
   is fixed; it is concurrency input, never authority.
4. Resolve `submission_id` replay. Same successful submission returns the
   existing safe outcome and performs no M8 ingest, upload, or audit.
5. On mismatch return `CUSTOMER_DOCUMENT_CHANGED` with no storage call.
6. Commit/close and retain only a redacted immutable snapshot. No Session is
   passed to M8.

M8 ingest phase:

1. Call `ingest_sanitized_image` exactly once with the application
   `session_factory`, multipart source, authenticated `User.id`, trusted
   `ResolvedClientIp`, injected aware time, one settings snapshot, and one
   injected `ObjectStorageService`.
2. Do not inspect or reproduce sanitizer, bucket/key, checksum, retry,
   lifecycle, PUT, or HEAD logic. The coordinator retains the returned object
   UUID only in memory and never exposes it to the browser or logs.
3. Continue to TX-B only after M8 returned a committed `AVAILABLE` result.

TX-B, using a new `session_factory.begin()`:

1. Lock own customer first and recheck exact `draft`.
2. Lock the exact in-memory M8 object row with
   `load_object_file_for_update`; require `AVAILABLE`, correct creator for
   accountability, and no existing attachment anywhere in
   `customer_documents`. Creator is not ownership.
3. Lock current documents in the same deterministic order as TX-A; recheck
   `submission_id` and expected-current identity.
4. Insert exactly one concrete customer-document attachment, supersede the old
   `CURRENT` only in this successful transaction, and append only allowlisted
   redacted audit events.
5. Commit/close before response work. TX-B performs zero storage/provider I/O.

If another transaction already committed the same submission, TX-B returns
the existing attachment and the just-ingested unused object enters the same
unattached-compensation decision; it never replaces or deletes the replayed
attached object. Different stale submissions yield one winner and one
`CUSTOMER_DOCUMENT_CHANGED` loser.

### Corrected Unattached Compensation

Compensation is not a public delete and never accepts an object UUID from the
client. Its candidate is only the current coordinator's in-memory
`IngestedImageResult` after TX-B failed or lost a replay race.

Required behavior:

1. Close/rollback the failed TX-B fully.
2. Open fresh TX-C; lock the candidate `ObjectFile`, then query the entire
   `customer_documents` table by `object_file_id`, not merely own/current
   documents. Any `CURRENT` or `SUPERSEDED` attachment means **do not delete**.
3. If the same submission is now attached, treat it as replay success where
   the caller contract permits; otherwise return the safe domain error. In
   either case perform zero provider delete.
4. If DB state is missing, unavailable, or ambiguous, do not delete. Safety
   wins over orphan cleanup; no identifier is reported for manual handling.
5. If globally unattached and exactly `AVAILABLE`, call public M8 repository
   primitive `mark_object_file_delete_pending(..., failure_code=None)` inside
   that same TX-C. Commit/close after the atomic absence-proof plus lifecycle
   claim.
6. The request stops there: it calls neither `delete_available_object` nor a
   provider DELETE/HEAD, imports no private M8 delete helper, and adds no public
   M8 delete API or scheduler.
7. Existing operator/CLI `reconcile_stale_object_deletes` eventually performs
   provider I/O with no Session open. Definite success/missing becomes
   `DELETED`; ambiguity remains
   `DELETE_PENDING/DELETE_OUTCOME_UNKNOWN` and reconcilable.

The attachment writer and compensation must lock `ObjectFile` before checking
or inserting the unique attachment. Therefore either attach commits first and
the delete guard sees it, or compensation commits `DELETE_PENDING` first and
TX-B rejects a non-`AVAILABLE` object. There is no check-then-delete race.

### Intended Own-Current Access Plan

1. Use a fixed own-document action; accept no customer/object UUID from path,
   query, form, or client-stored state.
2. A concrete customer-document parent resolver uses a short factory-owned
   transaction to derive the active user's own draft and exactly one
   `CURRENT` attachment. Missing, superseded, other-customer, or non-draft
   resolves to the same denial and returns no object metadata.
3. Build `ObjectReadAuthorizationRequest` only from that trusted result. The
   concrete `ObjectFileAccessAuthorizer` rechecks actor/parent/object binding;
   ShopRole, platform-admin, and `created_by_user_id` confer no access.
4. Call `create_authorized_presigned_get_url` unchanged. It authorizes first,
   loads only `AVAILABLE` in a short session, closes it, then calls provider
   presign with the configured TTL.
5. Return the redacted wrapper's URL only to the authorized response. Never
   persist, flash, audit, log, report, or place it in application/browser
   storage. Denial performs zero SDK calls and uses `FILE_ACCESS_DENIED`.

### CR-M10-01 Threat/Test Contract

Future M10 implementation tests must pin all of these outcomes without
weakening the existing M8 suite:

1. blocked early RL-CHECK causes no TX-A, source read, object row, or provider
   call;
2. allowed early check followed by an M8 final-record race denial causes no
   source read, provider call, or attachment;
3. success records one attempt exactly once with no duplicate increment;
4. post-ingest TX-B failure changes an unattached `AVAILABLE` object to
   `DELETE_PENDING` atomically in TX-C;
5. an attachment winner makes TX-C a NOOP and leaves the object `AVAILABLE`
   and attached;
6. a TX-C winner leaves the object `DELETE_PENDING` and attachment zero-write;
7. existing reconciliation changes a definite outcome to `DELETED` and keeps
   an ambiguous outcome `DELETE_PENDING/DELETE_OUTCOME_UNKNOWN`;
8. no SQLAlchemy Session exists during source read, sanitize, PUT, HEAD, DELETE,
   or presign;
9. no object UUID, bucket/key/checksum, filename, or presigned URL enters
   audit, logs, errors, reports, or repr output.

### CR-M10-01 Resolution Matrix

| ID | Prior conflict | FINAL APPROVED correction | Repository evidence / implementation obligation |
|---|---|---|---|
| M10-STOR-01 | Rate admission had to precede TX-A while the sole M8 ingest records internally. | Early M10 RL-CHECK calls `check_storage_upload_rate_limit`; only M8 ingest calls `record_storage_upload_attempt` and final-enforces exactly once. | Both public functions share `_build_buckets`, settings, safe result, and limiter. M10 adds no scope/counter and never records directly. |
| M10-STOR-02 | Separate unattached check plus immediate public delete left an attach race. | TX-B and TX-C serialize on `object_file FOR UPDATE`; TX-C atomically proves global absence and calls `mark_object_file_delete_pending(failure_code=None)`. Request performs no provider delete; existing reconciliation owns cleanup. | The repository primitive already accepts caller transaction ownership and `AVAILABLE -> DELETE_PENDING`; the existing reconciler already closes claim sessions before DELETE/HEAD. No M8 API change is required. |
| M10-STOR-03 | Current function-scoped request DB dependency would remain alive during storage work. | This is a fixed integration constraint rather than a scope amendment: finish authentication/session touch/CSRF in a short phase and pass only detached redacted inputs into RL-CHECK/coordinator work. | M10 route composition must prove no request Session during source read, sanitize, PUT, HEAD, DELETE, or presign; auth/cookie/CSRF semantics remain unchanged. |

The two previous scope-review blockers are closed by CR-M10-01 without changing
M8. M10-STOR-03 remains a test-pinned implementation obligation, not a product
decision or blocker. The correction adds no product code, scheduler, delete API,
rate-limit scope, counter, sanitizer, or storage provider.

## M10.06 Disposition

- M8 ingest, sanitizer, lifecycle, internal delete/reconcile, presign,
  redaction, provider injection, and factory-owned session behavior are mapped
  for exact reuse and are green (`78 passed`).
- The desired TX-A/M8/TX-B, compensation, and own-current access behavior is
  explicit, including zero provider I/O with any Session open.
- No product code, schema, dependency, lockfile, route, test, or CI was changed;
  no later M10 task implementation was started. The external guide's future
  task contracts were corrected as CR-M10-01 requires.
- CR-M10-01 fixes rate authority as early read-only check plus ingest-only
  exactly-once record and fixes compensation as an atomic TX-C lifecycle claim
  followed by existing M8 reconciliation.
- Exact lock order is TX-A `customer -> current customer-document rows`, TX-B
  `customer -> object_file -> current customer-document rows`, and TX-C
  `object_file -> global attachment existence check`.

Known limitation: a post-TX-B orphan is not deleted immediately in the request.
After the atomic `DELETE_PENDING` claim, existing M8 reconciliation deletes it
eventually; M10 creates no scheduler.

Result: `M10.06 COMPLETE — CR-M10-01 APPLIED`.

## M10.07 Repository-Aware Threat-To-Test Matrix

Status: FROZEN PLANNING CONTRACT. This section creates no product or test code.
Every test below is a future exact node ID, not a placeholder. A row is GREEN
only when its deterministic outcome is asserted independently even if a later
test implementation shares setup with another row. M10.08 must carry this
matrix forward without weakening it.

Evidence precedence remains TT -> M10.00 Final Scope Freeze -> CR-M10-01 ->
closed inherited contracts -> current repository symbols. `PO-M10-NN` below
means the exact decision in
`/home/yalgashev/projects/nasiya_m10_00_final_scope_freeze.md`; `CR-M10-01`
means the FINAL APPROVED guide section. A planned M10 symbol is never presented
as existing repository evidence.

### A. PII And Cryptography

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T001 | Plaintext identity fields persist in DB. | Identity persistence | Store only encrypted envelope fields and blind index in `customer_identities`; keep `customers` PII-free. | `tests/test_customer_identity_postgresql.py::test_plaintext_identity_fields_are_absent_from_database_storage` | Raw DB inspection finds none of the submitted identity canaries outside the intended encrypted representation. | PO-M10-03/04; `app/customer/models.py:Customer`; M10.00 §8.5 |
| M10-T002 | Plaintext PII escapes through audit, log, error, URL, flash, report, repr, or browser storage. | Cross-layer privacy | Allow plaintext only inside canonicalize/encrypt and authorized own-user no-store rendering boundaries; run sink canaries. | `tests/test_customer_identity_sensitive_data_audit.py::test_plaintext_identity_canaries_do_not_escape_allowed_in_memory_boundaries` | Every forbidden sink is canary-free while the authorized response exposes only the permitted name/masked values. | PO-M10-18/19; `app/audit/redaction.py:redact_audit_payload`; M10.00 §8.10–8.11 |
| M10-T003 | Ciphertext is moved to another customer row and decrypted with wrong customer/AAD. | AEAD binding | Bind AAD to exact customer UUID and schema version and fail closed on row substitution. | `tests/test_customer_identity_crypto.py::test_ciphertext_cannot_be_decrypted_under_another_customer_aad` | Substituted envelope raises the safe crypto failure and returns no plaintext. | PO-M10-07; M10.00 §8.3 |
| M10-T004 | Ciphertext, nonce, or authentication tag is tampered. | AEAD integrity | Use PyCA `AESGCM` authenticated decrypt with exact envelope validation; never salvage partial plaintext. | `tests/test_customer_identity_crypto.py::test_tampered_ciphertext_nonce_and_tag_fail_closed_without_plaintext` | Every independent mutation fails with the same safe internal outcome and zero plaintext. | PO-M10-07; M10.00 §8.3; `app/auth/error_codes.py:get_public_error_body` pattern |
| M10-T005 | Wrong encryption key decrypts or leaks detail. | Key selection | Select only the row key ID mapping and collapse authentication failure to a redacted error. | `tests/test_customer_identity_crypto.py::test_wrong_encryption_key_fails_closed_with_safe_error` | Wrong key yields no plaintext, no fallback, and no key/provider detail. | PO-M10-07/08; M10.00 §8.3 |
| M10-T006 | Unknown or retired key version silently falls back. | Keyring read path | Require exact key ID lookup; unknown/removed ID has no default-key fallback. | `tests/test_customer_identity_crypto.py::test_unknown_and_retired_key_ids_fail_closed_without_fallback` | Both unknown and retired IDs return the same safe failure and perform no alternate decrypt. | PO-M10-08; M10.00 §8.3 |
| M10-T007 | Encryption keyring is missing or invalid. | Settings/startup boundary | Validate exact 32-byte values, active ID membership, IDs, and mapping uniqueness; fail closed with redacted settings errors. | `tests/test_m10_dependency_boundary.py::test_missing_and_invalid_identity_keyring_configuration_fails_closed` | Each malformed configuration is rejected before identity work and no secret value appears in repr/error. | PO-M10-08; `app/settings.py:Settings`; `SecretStr`/`hide_input_in_errors` pattern |
| M10-T008 | Nonce is reused or deterministic. | AEAD write path | Generate a fresh random 12-byte nonce for every encryption and never derive it from customer or payload. | `tests/test_customer_identity_crypto.py::test_identical_payload_encryptions_use_distinct_random_nonces` | Repeated encryption of identical canonical input produces distinct 12-byte nonces and ciphertexts that both decrypt. | PO-M10-07; M10.00 §8.3 |
| M10-T009 | Payload schema version is wrong or unsupported. | Envelope/versioning | Authenticate the exact supported schema in AAD and reject all other versions before deserialization. | `tests/test_customer_identity_crypto.py::test_unsupported_identity_payload_schema_version_fails_closed` | Unsupported, missing, and malformed versions yield one safe failure and no plaintext/result DTO. | PO-M10-08; M10.00 §8.2–8.3 |
| M10-T010 | Blind-index key equals an encryption key. | Secret separation | Validate an exact independent 32-byte blind-index secret unequal to every encryption key. | `tests/test_m10_dependency_boundary.py::test_blind_index_key_must_be_distinct_from_encryption_keys` | Equal material is rejected fail-closed; distinct synthetic keys are accepted. | PO-M10-09; M10.00 §8.4; `app/settings.py:Settings` validation pattern |
| M10-T011 | Blind index escapes into audit/log/error. | Lookup privacy | Keep blind index inside crypto/repository boundaries and redact DTOs/exceptions/audit metadata. | `tests/test_customer_identity_sensitive_data_audit.py::test_blind_index_never_enters_audit_log_error_report_or_repr` | Canary blind-index bytes/encodings are absent from all inspected sinks. | PO-M10-19; M10.00 §8.10; `app/audit/redaction.py:_PAYLOAD_BUILDERS` pattern |
| M10-T012 | Sequential duplicate JSHSHIR creates/discloses another identity. | Identity uniqueness | Enforce unique HMAC blind index and map only the named conflict to stable `DUPLICATE_JSHSHIR`. | `tests/test_customer_identity_postgresql.py::test_sequential_duplicate_jshshir_returns_safe_duplicate_without_disclosure` | First insert commits; second has zero identity/audit write and reveals no existing customer data. | PO-M10-09/10; `app/offers/repository.py:_constraint_name` savepoint pattern |
| M10-T013 | Parallel duplicate JSHSHIR creates two identities. | PostgreSQL concurrency | Rely on the DB unique constraint plus expected-conflict mapping under real parallel transactions. | `tests/test_customer_identity_concurrency_postgresql.py::test_parallel_duplicate_jshshir_has_one_winner_and_one_safe_duplicate` | Exactly one identity/audit commits; the loser returns `DUPLICATE_JSHSHIR`. | PO-M10-09/10; real-PG concurrency style in `tests/test_storage_persistence_concurrency_postgresql.py` |
| M10-T014 | Unique conflict poisons the caller Session. | Transaction ownership | Contain the expected insert conflict in `Session.begin_nested()`; repository never full-rolls back/closes. | `tests/test_customer_identity_postgresql.py::test_duplicate_jshshir_savepoint_keeps_outer_session_usable` | After mapped duplicate, caller can query and commit unrelated outer-transaction work. | PO-M10-20; `app/offers/repository.py` / `app/otp/repository.py` savepoint patterns |
| M10-T015 | JSHSHIR is not exactly 14 ASCII digits. | Input contract | Validate length and `[0-9]` over canonical input; invent no checksum. | `tests/test_customer_identity_contracts.py::test_jshshir_requires_exactly_fourteen_ascii_digits` | Only exactly 14 ASCII digits pass; all shorter/longer/non-digit cases fail deterministically. | PO-M10-06; M10.00 §8.2 |
| M10-T016 | Unicode digits or hidden control characters bypass JSHSHIR validation. | Input canonicalization | Reject non-ASCII digits and all control/format characters without Unicode digit coercion. | `tests/test_customer_identity_contracts.py::test_jshshir_rejects_unicode_digits_and_hidden_control_characters` | Arabic/fullwidth digits and embedded controls are rejected before blind indexing. | PO-M10-05/06; M10.00 §8.2 |
| M10-T017 | Name/document-number normalization or length rules are bypassed. | Input canonicalization | Apply the one frozen canonicalizer: name trim/whitespace collapse and code-point rules; document trim/ASCII uppercase/charset/length. | `tests/test_customer_identity_contracts.py::test_names_and_document_number_enforce_frozen_normalization_and_lengths` | Boundary-valid values canonicalize exactly; overlong, forbidden-code-point, or invalid-character values fail. | PO-M10-05; M10.00 §8.2 |
| M10-T018 | Completeness returns true without successful identity decrypt. | Completeness policy | Require decrypt, schema/key validity, required canonical fields, and recomputed matching blind index. | `tests/test_customer_identity_postgresql.py::test_identity_completeness_requires_decrypt_and_matching_blind_index` | Missing/tampered/undecryptable/mismatched rows are false; only a valid envelope is true. | M10.00 §8.9; PO-M10-07/09 |
| M10-T019 | Stale identity revision overwrites newer data. | Identity command/concurrency | Lock own draft identity and compare exact positive revision; mismatch has zero write/audit. | `tests/test_customer_identity_postgresql.py::test_stale_identity_revision_returns_changed_with_zero_write_and_audit` | Stale command returns `CUSTOMER_IDENTITY_CHANGED`; latest ciphertext/revision/audit count is unchanged. | PO-M10-11; M10.00 §8.6 |
| M10-T020 | Parallel identity updates lose one update silently. | PostgreSQL concurrency | Serialize on the identity/customer row and recheck revision in the write transaction. | `tests/test_customer_identity_concurrency_postgresql.py::test_parallel_identity_updates_have_one_winner_without_lost_update` | Exactly one update increments revision/audits; the loser gets `CUSTOMER_IDENTITY_CHANGED`. | PO-M10-11; real-PG lock style in `app/storage/repository.py:load_object_file_for_update` |
| M10-T021 | Active/non-draft customer identity is mutated. | Customer-state authorization | Server-resolve own customer, lock it, and require exact `draft` on every mutation/recheck. | `tests/test_customer_identity_authorization.py::test_identity_mutation_requires_own_draft_customer` | Missing, foreign, and non-draft cases produce the same safe denial with zero identity/audit mutation. | PO-M10-02/17; `app/customer/models.py:Customer`; `ck_customers_onboarding_status_draft_only` |

### B. Authorization And IDOR

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T022 | Anonymous identity read/update succeeds. | Route authentication | Reuse server-side session authentication before resolving any customer or decrypting PII. | `tests/test_customer_identity_authorization.py::test_anonymous_identity_read_and_update_are_denied_without_pii` | Anonymous GET/POST is denied/redirected per auth convention with zero decrypt/write/audit. | PO-M10-02; `app/auth/deps.py:require_user`; `tests/test_customer_auth_route_matrix.py` |
| M10-T023 | Another authenticated user targets a customer UUID to read PII. | Own-customer authorization | Resolve customer only by authenticated `User.id`; expose no customer-ID route authority. | `tests/test_customer_identity_authorization.py::test_authenticated_user_cannot_target_foreign_customer_uuid` | Forged foreign UUID does not alter resolution; response is indistinguishable denial and contains no PII. | PO-M10-02; `app/customer/repository.py:get_customer_by_user_id`; `tests/test_customer_web_idor.py` |
| M10-T024 | Shop OWNER/MANAGER/CASHIER reads another customer's PII. | Role isolation | Treat every `ShopRole` as irrelevant to customer identity/document authority. | `tests/test_customer_identity_authorization.py::test_shop_roles_do_not_grant_cross_customer_pii_access` | All three roles are denied identically and trigger no decrypt/presign. | PO-M10-02; `app/shop/enums.py:ShopRole`; `app/shop/dependencies.py` |
| M10-T025 | `is_platform_admin` alone grants cross-customer PII access. | Admin isolation | Do not reuse offer-admin authorization for customer PII; own-user proof remains mandatory. | `tests/test_customer_identity_authorization.py::test_platform_admin_bit_does_not_grant_cross_customer_pii_access` | Admin-bit actor can access only their own draft customer and is denied foreign PII. | PO-M10-02/18; `app/offers/authorization.py:PlatformAdminActor` marked NO REUSE |
| M10-T026 | Client customer UUID becomes own-customer authority. | Command/route input | Omit customer ID from route/form command and derive it server-side from actor. | `tests/test_customer_identity_authorization.py::test_client_customer_uuid_is_never_an_authority_input` | Forged path/query/form UUID is rejected or ignored before repository lookup; server-resolved own customer is the sole target. | PO-M10-02; `app/customer/service.py:get_current_customer_draft_state` pattern |
| M10-T027 | Client object UUID becomes attachment authority. | Upload command | Accept no object ID; retain only the detached `IngestedImageResult` identity in memory into TX-B/TX-C. | `tests/test_customer_document_coordinator.py::test_client_object_uuid_is_never_attachment_authority` | Forged object input cannot select/attach/claim any object and no object identifier is echoed. | CR-M10-01; `app/storage/service.py:IngestedImageResult`; PO-M10-12/13 |
| M10-T028 | Full JSHSHIR/document number appears in GET view. | Authorized presentation | Decrypt in memory for policy, render only last-four masks for sensitive identifiers. | `tests/test_customer_identity_web.py::test_identity_get_masks_jshshir_and_document_number` | HTML contains exact masks and no full sensitive identifier. | PO-M10-18; M10.00 §8.11; `app/customer/view_model.py` presentation pattern |
| M10-T029 | Existing sensitive values are repopulated into update form fields. | Form presentation | Permit normalized name prefill only; leave JSHSHIR/document-number fields blank. | `tests/test_customer_identity_web.py::test_identity_update_form_never_prefills_sensitive_identifiers` | Names may render escaped; both sensitive input values are absent/empty on success and validation error. | PO-M10-18; `tests/test_customer_profile_template.py` pattern |
| M10-T030 | Presign occurs before own-current authorization. | Document read coordinator | Run concrete parent authorizer first, then load `AVAILABLE`, close DB, then call provider. | `tests/test_customer_document_presign.py::test_own_current_authorization_precedes_object_lookup_and_presign` | Denial performs zero SDK calls; allowed call order is authorize -> DB open/close -> presign. | PO-M10-16; `app/storage/service.py:create_authorized_presigned_get_url`; existing presign tests |
| M10-T031 | Superseded/foreign document receives a presigned URL. | Parent-object authorization | Resolve only own `CURRENT` attachment and require its object `AVAILABLE`; creator/admin role is insufficient. | `tests/test_customer_document_presign.py::test_superseded_and_foreign_documents_are_denied_without_presign` | All denied/missing variants return `FILE_ACCESS_DENIED`, zero SDK calls, and no URL. | PO-M10-16; `app/storage/contracts.py:ObjectFileAccessAuthorizer`; `load_available_object_file` |

### C. Web Security

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T032 | Identity/document mutation accepts missing, wrong, or cross-session CSRF. | Unsafe web routes | Reuse session-bound CSRF validation, including cached multipart form validation, before mutation. | `tests/test_customer_identity_csrf.py::test_identity_and_document_mutations_reject_missing_wrong_and_cross_session_csrf` | Every invalid token returns the established safe CSRF outcome with zero service/storage call. | PO-M10-18; `app/auth/deps.py:validate_csrf`; `app/storage/multipart.py:bounded_multipart_upload` |
| M10-T033 | PII or filename injects XSS. | Templates/errors | Autoescape all text, never trust filename, and keep raw values out of flash/error/URL. | `tests/test_customer_identity_xss.py::test_identity_and_filename_canaries_are_html_escaped` | Canary markup is escaped or absent across GET, validation, and upload responses; no executable node/attribute appears. | PO-M10-18/19; `tests/test_customer_view_template_safety.py`; M8 filename redaction contract |
| M10-T034 | Safe filter, `Markup`, inline script, or inline event handler bypasses escaping/CSP. | Template/source containment | Forbid unsafe rendering primitives and inline executable markup in M10 templates. | `tests/test_customer_identity_xss.py::test_m10_templates_forbid_unsafe_markup_inline_scripts_and_event_handlers` | Static scan finds none of the forbidden constructs and rendered CSP-compatible pages contain no inline executable content. | PO-M10-18; `tests/test_shop_get_leakage_xss_audit.py` source-audit pattern |
| M10-T035 | PII responses lack `Cache-Control: no-store`. | Response privacy | Apply no-store to identity/document pages, errors, redirects, and presign redirects. | `tests/test_customer_identity_web.py::test_identity_and_document_responses_errors_and_redirects_are_no_store` | Every enumerated status/path carries `Cache-Control: no-store`. | PO-M10-18; M10.00 §8.11; M9 no-store presentation pattern |
| M10-T036 | Raw PII enters query string, redirect location, or flash. | PRG/navigation | Post only in body; redirect to fixed own routes and flash only stable safe codes/messages. | `tests/test_customer_identity_web.py::test_raw_pii_never_enters_query_redirect_location_or_flash` | Canary values are absent from request URLs, `Location`, cookies/session flash, and response messages. | PO-M10-18/19; `app/auth/error_codes.py:get_public_error_body`; PRG patterns in `app/customer/router.py` |
| M10-T037 | Oversized multipart bypasses request body guard. | Upload envelope | Register only the exact upload route in `StorageBodyLimitMiddleware` and reject actual bytes before multipart/coordinator. | `tests/test_customer_document_coordinator.py::test_oversized_document_request_is_rejected_before_multipart_and_coordinator` | Oversize/chunked cases create no source parser, TX, object row, or provider call. | PO-M10-13; `app/storage/body_guard.py:StorageBodyLimitMiddleware`; `M8_STORAGE_BODY_GUARD_PATHS` |
| M10-T038 | Unsupported content type or decode failure reaches storage/attachment. | M8 ingest/sanitizer | Reuse bounded M8 parser/decoder and stable errors; create no attach on failure. | `tests/test_customer_document_coordinator.py::test_unsupported_or_undecodable_document_creates_no_row_or_provider_call` | Invalid MIME/decode returns exact M8 public error with zero object/attachment/provider write. | PO-M10-13; `app/storage/image.py:read_bounded_image`; `sanitize_bounded_image` |
| M10-T039 | Filename/extension is trusted as MIME. | Upload validation | Detect and validate decoded image content; treat filename as untrusted and non-persistent. | `tests/test_customer_document_coordinator.py::test_document_upload_uses_content_bytes_not_filename_or_extension` | False extension/MIME cannot select sanitizer/output type and filename is absent from DB/audit/log/error. | PO-M10-13/19; M8 image/multipart contracts; `tests/test_storage_image_decode.py` |
| M10-T040 | EXIF/GPS metadata survives upload. | Sanitization/provider boundary | Route all images through M8 fresh-pixel decode/re-encode before PUT. | `tests/test_customer_document_minio_integration.py::test_uploaded_document_strips_exif_and_gps_before_storage` | Provider object decodes with no submitted EXIF/GPS while valid pixels/content family remain. | PO-M10-13; `app/storage/image.py:sanitize_bounded_image`; `tests/test_storage_image_fresh_pixels.py` |

### D. CR-M10-01 Rate Limit And M8 Ingest

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T041 | Blocked early rate precheck still opens TX-A. | RL-CHECK/TX-A ordering | Run read-only `check_storage_upload_rate_limit` in its own short transaction and return before customer locking. | `tests/test_customer_document_coordinator.py::test_early_rate_denial_does_not_open_tx_a` | Only RL-CHECK transaction occurs; no customer/current-document query or lock executes. | CR-M10-01; `app/storage/rate_limit.py:check_storage_upload_rate_limit` |
| M10-T042 | Blocked early rate precheck reads image source. | RL-CHECK/source boundary | Return `RATE_LIMITED` before invoking M8 ingest or any `AsyncImageSource.read`. | `tests/test_customer_document_coordinator.py::test_early_rate_denial_does_not_read_image_source` | Source read/seek/sanitize spies remain at zero. | CR-M10-01; `app/storage/contracts.py:AsyncImageSource`; existing `test_rate_limit_rejection_precedes_source_read_row_and_provider` |
| M10-T043 | Blocked early precheck creates object row or provider call. | RL-CHECK/storage boundary | Stop before TX-S1/PUT/HEAD and perform no attempt record. | `tests/test_customer_document_coordinator.py::test_early_rate_denial_creates_no_object_row_or_provider_call` | Object-file count and provider call list are unchanged; exact `RATE_LIMITED` is returned. | CR-M10-01; `create_pending_object_file`; `ObjectStorageService` |
| M10-T044 | Early check allows, but M8 final-record race denial still reads source/calls provider. | Final rate enforcement | Keep `record_storage_upload_attempt` inside M8 immediately before source read; honor its race result. | `tests/test_customer_document_coordinator.py::test_m8_final_rate_race_denial_precedes_source_provider_and_attachment` | Final denial performs zero source/sanitize/PUT/HEAD and creates no object/attachment. | CR-M10-01; `app/storage/service.py:prepare_sanitized_image_upload`; `_record_upload_attempt` |
| M10-T045 | One successful upload attempt is counted twice. | Rate counter authority | M10 calls only the read-only check; M8 invokes authoritative recorder exactly once for the two existing buckets. | `tests/test_customer_document_coordinator.py::test_successful_upload_records_attempt_once_without_double_increment` | Recorder call count is one and each existing user/IP bucket advances by exactly one attempt. | CR-M10-01; `record_storage_upload_attempt`; `_build_buckets` |
| M10-T046 | M10 creates a new limiter scope/counter. | Scope/dependency containment | Reuse only `STORAGE_UPLOAD_USER_SCOPE` and `STORAGE_UPLOAD_IP_SCOPE` over existing auth-rate-limit storage. | `tests/test_m10_scope_containment.py::test_m10_defines_no_new_storage_rate_limit_scope_or_counter` | Source/schema scan finds no M10 limiter table, scope, key prefix, or duplicate counter. | CR-M10-01; `app/storage/rate_limit.py:STORAGE_UPLOAD_*`; `app/auth/rate_limit.py:AuthRateLimiter` |
| M10-T047 | M8 begins read/sanitize before final rate enforcement. | Ingest internal ordering | Call authoritative record/check before `read_bounded_image` and sanitizer. | `tests/test_customer_document_coordinator.py::test_m8_final_rate_enforcement_precedes_read_and_sanitize` | Instrumented order is record -> source read -> sanitize; blocked record stops both later steps. | CR-M10-01; `app/storage/service.py:prepare_sanitized_image_upload` |
| M10-T048 | SQLAlchemy Session/transaction remains open during source read, sanitize, PUT, or HEAD. | DB/network isolation | Close RL/TX-A and all M8 DB phases before non-DB work; pass only detached values. | `tests/test_customer_document_coordinator.py::test_upload_source_sanitize_put_and_head_have_no_open_session` | Session spy reports zero open sessions at each source/sanitizer/provider boundary. | PO-M10-14 amended by CR-M10-01; existing `test_source_and_provider_boundaries_have_no_open_session` |
| M10-T049 | Client object ID replaces M8-returned object identity. | TX-B input authority | Carry only the in-memory redacted ingest result into TX-B; exclude object ID from route/form. | `tests/test_customer_document_coordinator.py::test_tx_b_uses_only_server_returned_ingest_object_identity` | Forged client values cannot affect locked object; only the provider-created row is considered. | CR-M10-01; `app/storage/service.py:IngestedImageResult`; PO-M10-12 |
| M10-T050 | Non-`AVAILABLE` object is attached. | TX-B lifecycle guard | Lock exact object row and require `ObjectFileStatus.AVAILABLE` before attachment/audit. | `tests/test_customer_document_attachment_postgresql.py::test_tx_b_rejects_non_available_object_with_zero_attachment_and_audit` | Every pending/failed/delete-pending/deleted case is a safe zero-write conflict. | CR-M10-01; `app/storage/models.py:ObjectFileStatus`; `load_object_file_for_update` |

### E. Document Attachment And Stale/Current Races

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T051 | One customer has two `CURRENT` documents. | Document persistence | Enforce exact PostgreSQL partial unique current-per-customer index and transactional supersede/insert. | `tests/test_customer_document_attachment_postgresql.py::test_database_rejects_two_current_documents_for_one_customer` | Second concurrent/current insert cannot commit; final count is exactly one `CURRENT`. | PO-M10-12; M10.00 §8.5 |
| M10-T052 | One object file attaches to multiple document rows/customers. | Object attachment uniqueness | Enforce unique `customer_documents.object_file_id` and verify global unattached state under object lock. | `tests/test_customer_document_attachment_postgresql.py::test_database_rejects_object_file_attached_to_multiple_documents` | First attachment may commit; every second attachment fails with no extra audit/supersede. | PO-M10-12; CR-M10-01 object serialization pivot |
| M10-T053 | Stale expected-current token silently replaces the winner. | TX-A/TX-B stale guard | Compare nullable `expected_current_document_id` after own-customer locks in both phases. | `tests/test_customer_document_attachment_postgresql.py::test_stale_expected_current_returns_changed_without_replacement` | Loser returns `CUSTOMER_DOCUMENT_CHANGED`; winner/current/history/audit remain unchanged by loser. | PO-M10-12; M10.00 §8.7 |
| M10-T054 | Sequential submission replay creates duplicate document/audit. | Idempotency | Resolve `(customer_id, submission_id)` before ingest and again in TX-B; return existing safe result. | `tests/test_customer_document_attachment_postgresql.py::test_sequential_submission_replay_creates_no_second_upload_attachment_or_audit` | Replay performs zero source/provider work and counts stay one attachment/one event set. | PO-M10-12; M10.00 §8.7 |
| M10-T055 | Parallel submission replay creates duplicate attachment. | PostgreSQL concurrency | Serialize customer/object/current rows and converge unique submission to one committed result. | `tests/test_customer_document_concurrency_postgresql.py::test_parallel_submission_replay_converges_to_one_attachment` | Both callers receive one logical result; exactly one object is attached and unused object enters safe TX-C only. | PO-M10-12; CR-M10-01 TX-B/TX-C |
| M10-T056 | Old current is superseded before new attach succeeds. | TX-B atomicity | Supersede old/current, insert new, and append audit only inside one successful TX-B. | `tests/test_customer_document_attachment_postgresql.py::test_failed_new_attachment_leaves_previous_current_unchanged` | Any insert/audit/lifecycle failure rolls back; old row remains `CURRENT` with original metadata. | PO-M10-12/15; M10.00 §8.7 |
| M10-T057 | TX-B inserts without locking object row. | Attach/compensation serialization | Lock server-returned `object_file FOR UPDATE` before global attachment proof and insert. | `tests/test_customer_document_concurrency_postgresql.py::test_tx_b_locks_object_file_before_attachment_insert` | Controlled competitor blocks at the object row; no insert executes before lock acquisition. | CR-M10-01; `app/storage/repository.py:load_object_file_for_update` |
| M10-T058 | TX-B lock order differs from customer -> object -> current rows. | Deadlock prevention | Apply the one frozen order on every attachment writer path. | `tests/test_customer_document_concurrency_postgresql.py::test_tx_b_uses_customer_object_current_lock_order` | Instrumented SQL/lock barriers observe exact order with no inverse acquisition. | CR-M10-01 lock-order contract; current `.with_for_update()` repository style |
| M10-T059 | Attach/compensation race deadlocks or ends nondeterministically. | Cross-transaction concurrency | Share object-row pivot, bound waits, and make each winner force the other to a safe branch. | `tests/test_customer_document_concurrency_postgresql.py::test_attachment_and_compensation_race_finishes_without_deadlock` | Repeated real-PG runs terminate: either attached+`AVAILABLE` or unattached+`DELETE_PENDING`, never mixed. | CR-M10-01 parallel outcomes; `ObjectFileStatus` |
| M10-T060 | Attached object becomes `DELETE_PENDING`/`DELETED`. | Final cross-table invariant | TX-C global attachment check under the same object lock must NOOP for any attachment. | `tests/test_customer_document_concurrency_postgresql.py::test_attached_object_remains_available_under_compensation_race` | Final joined-state query finds no attached row whose object is pending/deleted. | CR-M10-01; PO-M10-16 availability requirement |
| M10-T061 | Attachment writes after compensation wins. | TX-B recheck | TX-B object lock must observe `DELETE_PENDING` and exit zero-write with safe conflict. | `tests/test_customer_document_concurrency_postgresql.py::test_compensation_winner_prevents_attachment_write` | TX-C commits first; TX-B creates no document/audit/supersede. | CR-M10-01 compensation-winner outcome; `mark_object_file_delete_pending` |
| M10-T062 | Compensation claims object after attachment wins. | TX-C recheck | TX-C locks object then checks global `NOT EXISTS` and NOOPs if any attachment committed. | `tests/test_customer_document_concurrency_postgresql.py::test_attachment_winner_makes_compensation_noop` | TX-B commits first; object remains `AVAILABLE`, attached, and no delete lifecycle field changes. | CR-M10-01 attach-winner outcome |
| M10-T063 | Superseded row has incomplete historical metadata. | Document lifecycle | Set `SUPERSEDED`, timestamps/linkage/revision fields together with new current in TX-B. | `tests/test_customer_document_attachment_postgresql.py::test_supersede_writes_complete_historical_metadata_atomically` | Successful replace yields one fully populated historical row and one current; rollback yields no partial fields. | PO-M10-12; M10.00 §8.5/8.7 |
| M10-T064 | Attachment audit failure does not roll back business mutation. | Audit/transaction atomicity | Append exact audit events in caller-owned TX-B; propagate failure to outer rollback. | `tests/test_customer_document_attachment_postgresql.py::test_attachment_audit_failure_rolls_back_supersede_and_insert` | Audit fault leaves prior current/object status unchanged and adds no document/audit row. | PO-M10-19/20; `app/audit/repository.py:append_audit_event` |

### F. CR-M10-01 Compensation And Reconciliation

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T065 | Post-TX-B unattached `AVAILABLE` object remains an unbounded orphan. | Failure compensation | Always invoke fresh TX-C after post-ingest TX-B failure and atomically claim eligible object `DELETE_PENDING`. | `tests/test_customer_document_compensation.py::test_tx_b_failure_claims_unattached_available_object_delete_pending` | Failed attach leaves no eligible `AVAILABLE` orphan; it is either attached/NOOP or committed `DELETE_PENDING`. | CR-M10-01; `mark_object_file_delete_pending`; known limitation |
| M10-T066 | TX-C absence proof and lifecycle transition use separate transactions. | Compensation atomicity | Lock, global `NOT EXISTS`, and `AVAILABLE -> DELETE_PENDING` in one `session_factory.begin()`. | `tests/test_customer_document_compensation.py::test_tx_c_proves_unattached_and_claims_delete_pending_in_one_transaction` | Commit fault rolls back both logical claim and status; competitor cannot attach between proof and transition. | CR-M10-01 TX-C; repository caller-owned transaction contract |
| M10-T067 | TX-C does not `FOR UPDATE` the object row. | Serialization pivot | Call `load_object_file_for_update` before status/attachment checks. | `tests/test_customer_document_compensation.py::test_tx_c_locks_object_file_for_update_before_attachment_check` | SQL/competitor barrier proves object lock precedes the global attachment query. | CR-M10-01; `app/storage/repository.py:load_object_file_for_update` |
| M10-T068 | TX-C omits global attachment existence check. | Destructive-safety guard | Query all `customer_documents` by target object ID, independent of customer/status. | `tests/test_customer_document_compensation.py::test_tx_c_requires_global_attachment_absence_before_delete_pending_claim` | Any current/superseded/foreign/replayed attachment forces NOOP and preserves `AVAILABLE`. | CR-M10-01; PO-M10-15 amended |
| M10-T069 | Request path calls S3/MinIO DELETE directly. | External-I/O containment | End request compensation after TX-C commit; provider cleanup belongs only to existing reconciliation. | `tests/test_customer_document_compensation.py::test_request_compensation_performs_no_provider_delete_or_head` | Fake provider records zero DELETE/HEAD during request for success, failure, and race branches. | CR-M10-01; `app/storage/service.py:reconcile_stale_object_deletes` |
| M10-T070 | M10 imports private M8 `_delete_*` helpers. | Module boundary | Use only public repository claim primitive and public existing reconciler; enforce source import guard. | `tests/test_m10_scope_containment.py::test_m10_imports_no_private_storage_delete_symbols` | AST/source scan finds no M10 import/reference to private M8 delete helpers. | CR-M10-01; private `app/storage/service.py:_delete_object_target` is NO REUSE |
| M10-T071 | M10 creates a new public delete API or scheduler. | Scope containment | Reuse operator/CLI reconciliation and add no route/service API/background scheduling capability. | `tests/test_m10_scope_containment.py::test_m10_adds_no_storage_delete_api_route_or_scheduler` | Route/symbol/dependency scan finds no public delete or scheduling surface. | CR-M10-01; M10.00 OUT §6; `app/cli.py` existing operator composition |
| M10-T072 | Attached/replayed object enters compensation. | Replay/destructive safety | Re-resolve replay/attachment under object lock and NOOP before lifecycle mutation. | `tests/test_customer_document_compensation.py::test_attached_or_replayed_object_is_compensation_noop` | Object/status/provider/audit counts remain unchanged; replayed attached object remains readable. | CR-M10-01; PO-M10-12/15 amended |
| M10-T073 | Changed/missing object status is still claimed/deleted. | Lifecycle recheck | Require exact existing `AVAILABLE`; all missing/other statuses are safe NOOP. | `tests/test_customer_document_compensation.py::test_missing_or_changed_object_status_is_compensation_noop` | Pending/failed/delete-pending/deleted/missing cases cause no transition/provider call. | CR-M10-01; `app/storage/models.py:ObjectFileStatus` |
| M10-T074 | Reconciliation does not mark definite provider result `DELETED`. | Deferred cleanup | Feed TX-C claims into existing stale-delete reconciler and reuse definite success/missing transitions. | `tests/test_customer_document_compensation.py::test_existing_reconciler_marks_definite_or_missing_delete_result_deleted` | Success and provider-missing both finish `DELETED` with one bounded delete attempt. | CR-M10-01; `reconcile_stale_object_deletes`; existing delete PostgreSQL tests |
| M10-T075 | Ambiguous delete stops being reconcilable `DELETE_PENDING`. | Failure recovery | Preserve `DELETE_OUTCOME_UNKNOWN` and pending state for later bounded takeover; no request retry. | `tests/test_customer_document_compensation.py::test_existing_reconciler_keeps_ambiguous_delete_pending_reconcilable` | Timeout/ambiguous outcome remains pending with exact safe code and succeeds or remains bounded on later reconcile. | CR-M10-01; `mark_delete_outcome_unknown`; `claim_stale_delete_pending` |
| M10-T076 | Provider DELETE runs while SQLAlchemy Session is open. | Reconciler I/O isolation | Commit/close stale claim transaction before HEAD/DELETE and finalize in a fresh transaction. | `tests/test_customer_document_compensation.py::test_reconciler_provider_delete_has_no_open_sqlalchemy_session` | Session spy is zero during provider HEAD/DELETE for definite and ambiguous branches. | CR-M10-01; existing `test_internal_delete_commits_pending_before_one_external_delete` |
| M10-T077 | Compensation exposes object/storage metadata in sinks. | Compensation redaction | Keep object identity in memory only; use redacted wrappers and stable safe codes; prohibit sink fields. | `tests/test_customer_identity_sensitive_data_audit.py::test_compensation_redacts_object_id_bucket_key_checksum_filename_and_url` | Object/storage canaries are absent from audit rows, logs, exceptions, reports, and repr. | PO-M10-19; CR-M10-01; `StorageDeleteResult`/wrapper repr contracts |

### G. Persistence And Migration

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T078 | M10 migration has wrong M9 parent lineage. | Alembic graph | Create one linear child whose `down_revision` is exact `a9b0c1d2e3f4`. | `tests/test_m10_migration_postgresql.py::test_m10_revision_is_single_linear_child_of_m9_head` | Alembic reports one head and exact M9 -> M10 edge. | `alembic/versions/a9b0c1d2e3f4_create_legal_offer_foundation.py`; M10.00 §8.5 |
| M10-T079 | M10 creates more than the exact two domain tables. | Schema scope | Migration may add only `customer_identities` and `customer_documents` plus their bounded constraints/indexes/audit-registry extension. | `tests/test_m10_migration_postgresql.py::test_m10_upgrade_creates_only_two_m10_domain_tables` | Catalog diff contains exactly those two new M10 tables and no unrelated table/type/sequence. | PO-M10-03/12; M10.00 §8.5 |
| M10-T080 | Plaintext PII columns are added to `customers`. | Schema privacy | Keep M3 aggregate schema unchanged and put the exact envelope in 1:1 identity table. | `tests/test_m10_migration_postgresql.py::test_customers_table_remains_pii_free_after_m10_upgrade` | Catalog columns/checks for `customers` match M9 and contain no identity field. | PO-M10-03; `app/customer/models.py:Customer`; `b1f3a7c9d2e4` migration |
| M10-T081 | Historical FK uses destructive cascade. | Referential lifecycle | Use restrictive/non-destructive FKs for identity/document/object history; no cascade can erase audit/history. | `tests/test_m10_migration_postgresql.py::test_m10_historical_foreign_keys_do_not_use_destructive_cascade` | PostgreSQL catalog shows approved delete actions only; attempted parent delete cannot silently remove history. | M10.00 §8.5/8.10; M9 append-only audit boundary |
| M10-T082 | One-current partial unique index is absent. | Document constraint | Create exact unique partial index for `customer_id` where status is `CURRENT`. | `tests/test_m10_migration_postgresql.py::test_customer_documents_has_one_current_partial_unique_index` | Catalog predicate/columns are exact and duplicate-current insert is rejected. | PO-M10-12; M10.00 §8.5 |
| M10-T083 | Object attachment uniqueness is absent. | Document constraint | Make `customer_documents.object_file_id` globally unique. | `tests/test_m10_migration_postgresql.py::test_customer_documents_object_file_id_is_globally_unique` | Catalog unique constraint is exact and cross-customer reuse cannot commit. | PO-M10-12; CR-M10-01 serialization pivot |
| M10-T084 | Identity revision/envelope checks are absent. | Identity constraints | Enforce positive revision, byte lengths, supported schema/key fields, and 1:1 customer key. | `tests/test_m10_migration_postgresql.py::test_customer_identities_enforces_revision_and_envelope_constraints` | Catalog inspection and adversarial inserts prove every exact constraint. | PO-M10-07/08/11; M10.00 §8.5 |
| M10-T085 | M9 -> M10 -> M9 -> M10 walk corrupts inherited data. | Migration reversibility | Downgrade only M10-owned schema/registry additions and re-upgrade cleanly on populated M9 baseline. | `tests/test_m10_migration_postgresql.py::test_m9_m10_m9_m10_walk_preserves_inherited_data` | Preexisting row digests/counts/constraints survive both directions exactly. | M10.00 §9.3; `tests/test_offer_migration_postgresql.py` walk pattern |
| M10-T086 | Downgrade changes M1–M9 schema/data. | Containment/migration | Snapshot inherited catalog/data and assert only M10 tables/audit extension disappear. | `tests/test_m10_migration_postgresql.py::test_m10_downgrade_changes_only_m10_schema_and_audit_registry_extension` | M1–M9 catalog/data snapshot is byte/value equivalent after downgrade. | PO-M10-20; current Alembic linear lineage; M10.00 §9.3 |
| M10-T087 | SQLite or `create_all()` fallback hides PostgreSQL semantics. | Test/runtime infrastructure | Require validated real PostgreSQL fixture and Alembic migrations; static guard forbids fallbacks. | `tests/test_m10_scope_containment.py::test_m10_tests_forbid_sqlite_and_create_all_fallbacks` | Source scan finds neither fallback and DB fixture rejects non-PostgreSQL URLs. | `tests/postgresql.py:validate_test_database_url`; `tests/conftest.py:test_database_url` |

### H. Audit And Leakage

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T088 | Identity-created/updated audit contains plaintext or crypto material. | Audit allowlist | Extend typed registry with exact identity events and payload builders accepting only safe revision/outcome metadata. | `tests/test_customer_identity_sensitive_data_audit.py::test_identity_audit_payload_rejects_plaintext_and_crypto_material` | Valid audit has exact safe keys; any PII/ciphertext/nonce/key/blind-index candidate is rejected or discarded and never persisted. | PO-M10-19; `app/audit/contracts.py:_EVENT_OBJECT_TYPES`; `redact_audit_payload` |
| M10-T089 | Document attached/superseded audit contains object metadata. | Audit allowlist | Audit by concrete customer-document object and allow only status/document-type/replay outcome fields. | `tests/test_customer_identity_sensitive_data_audit.py::test_document_audit_payload_rejects_object_storage_metadata` | Object ID, bucket/key/checksum, filename, submission ID, and URL are absent from persisted payload/repr. | PO-M10-19; M10.00 §8.10; `app/audit/redaction.py:_PAYLOAD_BUILDERS` |
| M10-T090 | Audit append failure leaves identity/document mutation committed. | Same-transaction audit | Append via caller Session before outer commit; repository/service never commits or full-rolls back. | `tests/test_customer_identity_postgresql.py::test_identity_and_document_audit_failures_roll_back_business_mutations` | Injected audit fault leaves identity revision/document lifecycle and audit counts exactly unchanged. | PO-M10-19/20; `app/audit/repository.py:append_audit_event` |
| M10-T091 | Default dataclass/ORM repr reveals PII or crypto fields. | In-process observability | Mark sensitive DTOs `repr=False`, implement bounded redacted repr, and redact ORM identity fields. | `tests/test_customer_identity_sensitive_data_audit.py::test_identity_models_and_dtos_redact_pii_ciphertext_nonce_and_blind_index_in_repr` | `repr`/exception formatting contains type/safe status only and none of the canaries. | PO-M10-19; `IngestedImageResult` / `AuditEvent` redacted repr patterns |
| M10-T092 | Exception/SQL detail reaches public response. | Error boundary | Catch expected persistence/crypto/storage failures narrowly and map to stable catalog codes without chaining sensitive causes. | `tests/test_customer_identity_sensitive_data_audit.py::test_identity_storage_and_sql_exceptions_collapse_to_safe_public_errors` | Public body has exact code/message only; SQL/constraint/provider/key/input detail is absent. | M10.00 §8.12; `app/auth/error_codes.py:get_public_error_body`; `StorageUploadError` |
| M10-T093 | Secret/key material enters tracked files or CI output. | Supply-chain/CI privacy | Use only secret env names and synthetic fixtures; scan tracked content/config/log contract for values. | `tests/test_m10_dependency_boundary.py::test_m10_secret_material_is_absent_from_tracked_files_and_ci_output_contract` | Canary/real key patterns are absent; CI references required variable names without printing values. | PO-M10-07–09; `.github/workflows/ci.yml`; `app/settings.py:SecretStr` pattern |

### I. Scope And Containment

| Threat ID | Threat | Affected boundary | Required prevention | Exact planned test file::test_name | Expected deterministic outcome | Evidence source / repository symbol |
|---|---|---|---|---|---|---|
| M10-T094 | Public registration or REGISTRATION OTP appears. | Milestone scope | Keep M10 behind existing authenticated session and add no signup/registration OTP route/event. | `tests/test_m10_scope_containment.py::test_m10_has_no_public_registration_or_registration_otp_surface` | Route/symbol/template/audit scan finds no such capability and anonymous user cannot create customer identity. | PO-M10-01/02; M10.00 OUT §6; existing auth/OTP routes |
| M10-T095 | Customer transitions `draft -> active`. | Customer lifecycle | Identity/document completion exposes policy only and never mutates `Customer.onboarding_status`. | `tests/test_m10_scope_containment.py::test_m10_never_transitions_customer_from_draft_to_active` | All M10 success flows leave status exactly `draft`; no active token/symbol/migration value exists. | PO-M10-01/17/20; `ck_customers_onboarding_status_draft_only` |
| M10-T096 | `shop_customer` or customer-lead capability is created. | Domain scope | Add neither table/model/route/service nor shop-assisted PII capture. | `tests/test_m10_scope_containment.py::test_m10_adds_no_shop_customer_or_customer_lead_surface` | Schema/module/route scan and migration catalog contain no lead/link capability. | PO-M10-01/02; M10.00 OUT §6; `app/shop/` inherited boundary |
| M10-T097 | OCR/MRZ, selfie, biometric, or registry verification is integrated. | External-verification scope | Treat images as sanitized attachments only; add no parsing/verification dependency or output. | `tests/test_m10_scope_containment.py::test_m10_adds_no_ocr_mrz_selfie_biometric_or_registry_integration` | Dependency/import/route/schema scan finds no forbidden integration or extracted identity field. | PO-M10-01/13; M10.00 OUT §6; `ObjectStorageService` narrow protocol |
| M10-T098 | Generic attachment/CMS/KMS/full-PII admin platform appears. | Architecture scope | Implement only concrete `customer_documents`, settings keyring, and own-user UI; no generic owner registry/admin key management. | `tests/test_m10_scope_containment.py::test_m10_adds_no_generic_attachment_cms_kms_or_full_pii_admin_platform` | Public symbols/routes/tables remain bounded to the two M10 tables and own-user capability. | PO-M10-01/12; M10.00 OUT §6; platform-admin NO REUSE boundary |
| M10-T099 | Debt/payment/rating/disclosure/notification/scheduler scope appears. | Milestone containment | Add no related model/service/route/job/dependency; reuse only existing M8 operator reconciliation. | `tests/test_m10_scope_containment.py::test_m10_adds_no_debt_payment_rating_disclosure_notification_or_scheduler` | Repository and schema scan finds no new forbidden capability; no scheduler wiring is introduced. | PO-M10-01; CR-M10-01; M10.00 OUT §6 |
| M10-T100 | M1–M9 auth/Telegram/OTP/shop/storage/offer contracts regress. | Inherited containment | Run milestone suites and permit only the exact M10 audit-registry extension/M8 consumption. | `tests/test_m10_scope_containment.py::test_m1_m9_contracts_and_exact_audit_extension_remain_contained` | Existing milestone tests stay GREEN; route/schema/event diffs equal the approved M10 additions only. | M9 baseline `f96b9f0a...`; `m8-result.md`; `m9-result.md`; current CI suites |
| M10-T101 | TT or Final Scope Freeze content changes. | Source authority | Pin TT Git blob and recorded external freeze SHA; exclude both from M10 edits. | `tests/test_m10_scope_containment.py::test_tt_and_final_scope_freeze_hashes_match_m10_baseline` | TT blob is `d77c0f0f330a1330155a4aee3c46b05d97cf5561` and freeze SHA-256 is `de766bc75752cd80f64e49081b5764a0bc7b3b2112366f1a5d11818a7ab3a462`. | `docs/tt_nasiya_web_v1.md`; external M10.00 Final Scope Freeze; M10.01 evidence |
| M10-T102 | CR-M10-01 is used to redesign M8. | M8 inheritance/scope | Reuse exact check/record/ingest/claim/reconcile symbols; add no limiter, sanitizer, delete API, retry, provider, or scheduler. | `tests/test_m10_scope_containment.py::test_cr_m10_01_reuses_m8_without_redesigning_storage` | Source/API/schema diff shows only the concrete M10 consumer; every frozen M8 surface and existing M8 test remains unchanged/GREEN. | CR-M10-01; `check_storage_upload_rate_limit`; `ingest_sanitized_image`; `mark_object_file_delete_pending`; `reconcile_stale_object_deletes` |

### Planned Test File Inventory And Accounting

The matrix defines exactly 102 unique planned node IDs across these 18 files:

1. `tests/test_customer_identity_crypto.py`
2. `tests/test_customer_identity_contracts.py`
3. `tests/test_customer_identity_postgresql.py`
4. `tests/test_customer_identity_concurrency_postgresql.py`
5. `tests/test_customer_identity_authorization.py`
6. `tests/test_customer_identity_web.py`
7. `tests/test_customer_identity_csrf.py`
8. `tests/test_customer_identity_xss.py`
9. `tests/test_customer_identity_sensitive_data_audit.py`
10. `tests/test_customer_document_attachment_postgresql.py`
11. `tests/test_customer_document_coordinator.py`
12. `tests/test_customer_document_concurrency_postgresql.py`
13. `tests/test_customer_document_presign.py`
14. `tests/test_customer_document_compensation.py`
15. `tests/test_customer_document_minio_integration.py`
16. `tests/test_m10_migration_postgresql.py`
17. `tests/test_m10_scope_containment.py`
18. `tests/test_m10_dependency_boundary.py`

| Coverage group | Threat IDs | Count | Frozen assertion focus |
|---|---:|---:|---|
| PII/cryptography | M10-T001–M10-T021 | 21 | Plaintext containment, AEAD/keyring, canonicalization, duplicate/revision concurrency, draft-only mutation. |
| Authorization/IDOR | M10-T022–M10-T031 | 10 | Server-derived own customer/object authority, role isolation, masked/no-prefill views, authorize-before-presign. |
| Web security | M10-T032–M10-T040 | 9 | CSRF, XSS/CSP, no-store/PRG, body/MIME/decode bounds, EXIF stripping. |
| CR rate/M8 ingest | M10-T041–M10-T050 | 10 | Read-only early check, ingest-only exactly-once record, final race enforcement, session-free I/O, server object/AVAILABLE. |
| Attachment/races | M10-T051–M10-T064 | 14 | DB uniqueness, stale/replay, TX-B lock order, attach/compensation serialization, atomic audit/supersede. |
| CR compensation/reconcile | M10-T065–M10-T077 | 13 | One-TX claim, no request delete/private API/scheduler, NOOP safety, existing reconciler outcomes and redaction. |
| Persistence/migration | M10-T078–M10-T087 | 10 | Linear child, exact two tables, constraints/FKs, real-PG walk/downgrade, no SQLite/create_all. |
| Audit/leakage | M10-T088–M10-T093 | 6 | Exact allowlists, atomic rollback, repr/errors/secrets. |
| Scope/containment | M10-T094–M10-T102 | 9 | Forbidden capabilities, M1–M9 regression, protected-source hashes, no M8 redesign. |
| **Total** | **M10-T001–M10-T102** | **102** | **Every mandatory threat has one unique exact planned test and deterministic assertion.** |

CR-M10-01 is fully executable in the matrix: RL-CHECK is M10-T041–T043;
ingest-only final record/enforcement is T044–T047; no-session upload boundaries
are T048; server object and `AVAILABLE` eligibility are T049–T050; TX-B
serialization is T051–T064; atomic TX-C and no request delete are T065–T073;
existing reconciliation is T074–T076; object-metadata containment is T077; and
the no-redesign closure guard is T102.

No new business/security decision is required by this matrix. Exact future
file/node placement follows current repository naming, real-PostgreSQL fixture,
injected-fake/approved-MinIO storage, typed error, redaction, caller-transaction,
and containment conventions.

Result: `M10.07 COMPLETE — THREAT-TO-TEST MATRIX FROZEN`.

## M10.08 Approved M10 Placement

This section converts the M10.02-M10.07 findings into the binding
repository-aware integration map used after M10.08. It is subordinate to
`docs/m10_scope_contract.md` and `docs/m10_decisions.md`; a conflict or
required additional surface is a stop condition. No listed `PLANNED / ADD` or
`PLANNED / EXTEND` symbol exists at the M10.08 baseline.

### Exact Baseline And Protected Inputs

| Boundary | Exact evidence | M10 rule |
|---|---|---|
| Input commit | `f96b9f0a6d6b506f6715aa354cb4346199f1f5c5` on synced `main`, divergence `0 0` before readiness docs | Preserve ancestry; M10 code starts only from this remote-green M9 baseline. |
| M9 implementation/CI | `e2cda04920964cf383a749e07504539ccdafa0ab`; Actions `30645425078`, success; `2540 passed`; `8/8` checkpoints | Full inherited suite and exact closed behavior remain containment gates. |
| Migration parent | `a9b0c1d2e3f4` | The one M10 revision is its sole linear child. |
| TT | Git blob `d77c0f0f330a1330155a4aee3c46b05d97cf5561` | Protected; never edited by M10. |
| M10.00 freeze | SHA-256 `de766bc75752cd80f64e49081b5764a0bc7b3b2112366f1a5d11818a7ab3a462` | Protected external authority; never edited by M10. |
| CR-M10-01 | `FINAL APPROVED`, only PO-M10-14/15 | Read-only early check, ingest-only record, object lock pivot, atomic TX-C claim, existing reconciliation. |

### New Narrow Packages

| Status | Exact future file:symbol | Single responsibility / invariant |
|---|---|---|
| PLANNED / ADD | `app/customer_identity/__init__.py` | Package marker only when concrete package content is added in the same task; no export registry or placeholder API. |
| PLANNED / ADD | `app/customer_identity/contracts.py:CustomerDocumentType` | Exact `PASSPORT`, `ID_CARD` vocabulary. |
| PLANNED / ADD | `app/customer_identity/contracts.py:CanonicalCustomerIdentity` | Frozen, redacted exact six-field canonical value; no extra PII and no revealing repr. |
| PLANNED / ADD | `app/customer_identity/canonicalization.py:canonicalize_customer_identity` | Sole name/JSHSHIR/document-number validator and canonicalizer; no NFC/NFKC. |
| PLANNED / ADD | `app/customer_identity/crypto.py:CustomerIdentityCryptoConfig` | Frozen slots `repr=False` active/historical AEAD and dedicated blind-key snapshot; no dump/fallback/KMS interface. |
| PLANNED / ADD | `app/customer_identity/crypto.py:CustomerIdentityEnvelope` | Redacted ciphertext/nonce/key-ID/schema-version value; exact byte bounds. |
| PLANNED / ADD | `app/customer_identity/crypto.py:encrypt_customer_identity` | PyCA AES-256-GCM, random 12-byte nonce, fixed JSON/AAD/version contract. |
| PLANNED / ADD | `app/customer_identity/crypto.py:decrypt_customer_identity` | Exact stored key/version resolution and authenticated strict decode; no fallback/partial result. |
| PLANNED / ADD | `app/customer_identity/crypto.py:compute_jshshir_blind_index` | Exact dedicated-key domain-prefixed HMAC-SHA-256; 32-byte output. |
| PLANNED / ADD | `app/customer_identity/models.py:CustomerIdentity` | Maps only `customer_identities`; encrypted row with positive revision and restrictive parent. |
| PLANNED / ADD | `app/customer_identity/repository.py:load_customer_identity_for_update` | Caller-owned locked identity read with no decrypt/log/commit/close. |
| PLANNED / ADD | `app/customer_identity/repository.py:save_customer_identity` | Insert/update plus expected named blind-index savepoint; no full rollback/commit/close. |
| PLANNED / ADD | `app/customer_identity/service.py:save_own_customer_identity` | Own-draft/revision/canonicalize/blind/encrypt/write/audit transaction service. |
| PLANNED / ADD | `app/customer_identity/service.py:get_own_customer_identity_view` | Authorized operation-local decrypt into an explicit no-store safe view; identifiers masked. |
| PLANNED / ADD | `app/customer_identity/service.py:has_complete_customer_identity` | Authenticated strict envelope/payload/blind/revision completeness; no activation. |
| PLANNED / ADD | `app/customer_identity/web_presentation.py:CustomerIdentityWebLanguage` / `resolve_customer_identity_web_language` | Feature-local UZ-Latn/RU resolver with UZ-Latn fallback; no generic locale state. |
| PLANNED / ADD | `app/customer_identity/web_presentation.py:CustomerIdentityPageView` | Exact allowlisted page DTO; name prefill allowed, JSHSHIR/document number never prefilled, redacted repr. |
| PLANNED / ADD | `app/customer_identity/web_presentation.py:CustomerIdentityDiscoveryView` | PII-free redacted profile-link/status DTO; no customer/document/object UUID and no activation affordance. |
| PLANNED / ADD | `app/customer_identity/router.py:router` | Owns only the four exact `/customer/identity*` routes and no identifier path/query authority. |
| PLANNED / ADD | `app/customer_document/__init__.py` | Package marker only with concrete implementation; no generic attachment exports. |
| PLANNED / ADD | `app/customer_document/contracts.py:CustomerDocumentStatus` | Exact `CURRENT`, `SUPERSEDED` vocabulary. |
| PLANNED / ADD | `app/customer_document/contracts.py:CustomerDocumentAttachmentResult` | Detached redacted replay/attach outcome; no object/storage value in repr. |
| PLANNED / ADD | `app/customer_document/models.py:CustomerDocument` | Maps only `customer_documents`; unique object/submission, one-current history. |
| PLANNED / ADD | `app/customer_document/repository.py:load_current_customer_documents_for_update` | Locks current rows only after the prescribed preceding lock(s). |
| PLANNED / ADD | `app/customer_document/repository.py:load_submission_replay` | Own-customer replay lookup; submission token is comparison-only, never authority/audit. |
| PLANNED / ADD | `app/customer_document/repository.py:attach_current_customer_document` | Requires caller-held customer/object/current locks; atomically supersedes/inserts; no commit/close. |
| PLANNED / ADD | `app/customer_document/repository.py:claim_unattached_object_for_compensation` | TX-C object lock + global `NOT EXISTS` + existing pending marker in one transaction; no provider I/O. |
| PLANNED / ADD | `app/customer_document/coordinator.py:upload_and_attach_own_customer_document` | CR-M10-01 RL-CHECK/TX-A/M8/TX-B/TX-C orchestration using the shared factory and injected storage. |
| PLANNED / ADD | `app/customer_document/authorization.py:OwnCurrentCustomerDocumentAuthorizer` | Concrete M8 parent authorizer; server-derived own current attachment/object binding only. |
| PLANNED / ADD | `app/customer_document/service.py:has_current_customer_identity_document` | Exactly-one-current plus AVAILABLE/allowed-image completeness; no activation. |
| PLANNED / ADD | `app/customer_document/request_security.py:CustomerDocumentRequestContext` | Detached redacted authenticated actor/session-CSRF evidence for the document coordinator; no ORM entity or browser selector. |
| PLANNED / ADD | `app/customer_document/request_security.py:resolve_customer_document_request_security` | Reuses current auth/session/CSRF primitives within a short owned DB phase and closes it before M8/source/provider work; no alternate auth. |
| PLANNED / ADD | `app/customer_document/dependencies.py:get_customer_document_storage_service` | Operation-time object-storage config/client/adapter lifetime with guaranteed client close; tests inject fake/approved MinIO, base startup stays optional. |

The identity package may import the concrete document coordinator/service for
the two document route handlers. The document package may import identity
types needed for `CustomerDocumentType` display/audit, but neither package
creates a generic cross-domain registry or accepts an ORM object across an
external-I/O phase.

### Existing Files With Exact Narrow Extensions

| Status | Exact file:symbol | Approved M10 change |
|---|---|---|
| PLANNED / EXTEND | `app/customer/repository.py:load_existing_own_customer_draft_for_update` | Add server-user-keyed `FOR UPDATE` resolver; existing-only, exact draft, no customer creation or untrusted customer UUID. |
| PLANNED / EXTEND | `app/customer/router.py:profile_page` | Compose only the read-only own-user identity discovery view; no customer creation/touch, mutation, storage I/O, or activation. |
| PLANNED / EXTEND | `app/settings.py:Settings` | Add exactly the three optional secret-wrapped crypto source fields. |
| PLANNED / EXTEND | `app/settings.py:Settings.require_customer_identity_crypto_config` | Add one atomic operation-time fail-closed validator returning only the redacted snapshot. |
| PLANNED / EXTEND | `app/auth/error_codes.py:ErrorCode` / `ERROR_CATALOG` | Add only `CUSTOMER_DRAFT_REQUIRED`, `CUSTOMER_IDENTITY_CHANGED`, `CUSTOMER_DOCUMENT_CHANGED`, `CUSTOMER_IDENTITY_UNAVAILABLE`, `CUSTOMER_DOCUMENT_UNAVAILABLE`; reuse existing codes otherwise. |
| PLANNED / EXTEND | `app/audit/contracts.py:AuditEventType` / `AuditObjectType` / `_EVENT_OBJECT_TYPES` | Add exactly four M10 events, two object types, and exact mappings. |
| PLANNED / EXTEND | `app/audit/redaction.py:_PAYLOAD_BUILDERS` | Add exact M10 event-safe builders only; no PII/crypto/object-storage keys. |
| PLANNED / EXTEND | `app/audit/models.py:AuditLog` checks | Extend event/object/payload SQL exact shapes in sync with Python contracts. |
| PLANNED / EXTEND | `app/storage/body_guard.py:M8_STORAGE_BODY_GUARD_PATHS` | Add only `/customer/identity/document` to the inherited 11,010,048-byte actual-body guard. |
| PLANNED / EXTEND | `app/main.py:create_app` | Include `app.customer_identity.router:router` exactly once; compose no startup storage client and no scheduler. |
| PLANNED / EXTEND | `alembic/env.py:target_metadata` imports | Import both concrete model modules into the one existing `Base.metadata`. |
| PLANNED / ADD | `alembic/versions/` (the single M10 identity-foundation revision) | Revision ID and filename are assigned atomically by the migration task; sole parent `a9b0c1d2e3f4`; exact two tables and audit-check extension only. |
| PLANNED / ADD | `app/templates/customer/identity.html` | One autoescaped UZ-Latn/RU no-store identity/document page; capture input, masks, blank sensitive inputs, no inline code/storage. |
| PLANNED / EXTEND | `app/templates/customer/profile.html` | Add only localized `/customer/identity` discoverability and safe completion status; no PII/UUID, registration, activation, or shop action. |
| PLANNED / EXTEND | `app/static/app.css` | Only existing token/system selectors needed for accessible 320-430px identity UI; no PII/data or inline style. |
| PLANNED / EXTEND | `pyproject.toml` / `uv.lock` | Exactly `cryptography>=50.0.0,<51` and its frozen resolution; no second direct crypto dependency. |

The migration identifier and filename are deliberately not pre-reserved by a
readiness document. No other production file is in the default M10 plan. If
implementation proves another file/symbol necessary, the task stops for scope
review before adding it.

### Existing Reuse Symbols That Must Not Be Reimplemented

| Boundary | Exact symbol(s) | Mandatory reuse |
|---|---|---|
| DB/session factory | `app/db.py:Base`, `create_database_session_factory`, `create_database_session_dependency` | Shared metadata/factory; DB-only route outer owner. The yielding dependency never wraps external I/O. |
| Runtime composition | `app/main.py:create_app` and `application.state.database_session_factory` | Existing factory seam for short coordinator phases; storage remains operation-time. |
| Authentication | `app/auth/deps.py:get_current_session_context`, `require_user`, `validate_csrf`; `app/auth.sessions:resolve_by_raw_token`, `touch_session`; `app.auth.csrf:verify_csrf_token` | Preserve cookie/session/user-active/CSRF semantics; new detached document context is composition, not a new credential mechanism. |
| Own customer | `app/customer/models.py:Customer`, `CUSTOMER_ONBOARDING_STATUS_DRAFT`; `app/customer/repository.py:get_customer_by_user_id`; unique `uq_customers_user_id` | Server user determines zero-or-one existing draft; never call draft creation from M10. |
| Client IP | `app/request_client_ip.py:resolve_client_ip`; `app/telegram/client_ip.py:ResolvedClientIp` | Existing trusted-proxy resolution and redacted HMAC input; raw IP not persisted/reported. |
| M8 rate limit | `app/storage/rate_limit.py:check_storage_upload_rate_limit`, `record_storage_upload_attempt` | Check only in RL-CHECK; record only and exactly once in M8 ingest; same buckets/settings/error. |
| M8 multipart/sanitize | `StorageBodyLimitMiddleware`, `bounded_multipart_upload`, `read_bounded_image`, `sanitize_bounded_image` | Existing actual-byte, field/file, decode, dimension/pixel/frame, and metadata rules; no alternate path. |
| M8 ingest | `app/storage/service.py:ingest_sanitized_image` | Sole upload API; final record before source/provider, detached result, provider I/O without Session. |
| M8 lifecycle/claim | `app/storage/repository.py:load_object_file_for_update`, `mark_object_file_delete_pending` | TX-B locks/requires AVAILABLE; TX-C calls public marker with `failure_code=None` after same-TX nonattachment proof. |
| M8 reconcile | `app/storage/service.py:reconcile_stale_object_deletes` | Sole eventual provider-delete owner for M10 orphan claim; no M10 scheduler or delete API. |
| M8 access/presign | `ObjectReadAuthorizationRequest`, `ObjectFileAccessAuthorizer`, `create_authorized_presigned_get_url` | Concrete parent auth before AVAILABLE lookup/provider; TTL default 300; URL redacted/nonpersistent. |
| M8 storage adapter | `ObjectStorageService`, `create_s3_client`, `S3ObjectStorageService` | Existing injected protocol and bounded adapter; no second SDK/provider contract. |
| Audit writer | `AuditEvent`, `redact_audit_payload`, `append_audit_event`, `SqlAlchemyAuditWriter` | Typed exact redaction and same-transaction append; no logger fallback. |
| Safe responses | `get_public_error_body`, `get_error_http_status`, `mark_auth_response_no_store`, `set_security_headers` | Constant safe errors, no-store, CSP/referrer/frame/nosniff protections. |
| Templates | `app/templates/base.html`, current configured Jinja autoescape | One shared safe renderer/base; no `Markup`, `|safe`, inline script/style/handler. |

### CR-M10-01 Exact Integration Points

| Phase | Owner and symbol | Session/I/O invariant | Deterministic failure outcome |
|---|---|---|---|
| Guard/auth/CSRF | body guard plus `resolve_customer_document_request_security` | Actual request envelope is bounded; auth/CSRF DB work is closed before storage source/provider work. | Existing safe body/auth/`CSRF_FAILED`; no storage row/provider call. |
| RL-CHECK | coordinator + `check_storage_upload_rate_limit` in `session_factory.begin()` | Short read-only DB transaction; no attempt record and no source/provider work. | `RATE_LIMITED`; no TX-A, object row, or provider call. |
| TX-A | coordinator + own-draft/current repository reads | Lock order `customer -> current documents`; commit/close before ingest. | `CUSTOMER_DRAFT_REQUIRED`, replay result, or `CUSTOMER_DOCUMENT_CHANGED`; no ingest. |
| M8 ingest | `ingest_sanitized_image` | M8 records exactly once and finally enforces before source read/sanitize/provider; its SQL phases close around I/O. | M8 safe rate/file/storage error; no attachment. |
| TX-B | coordinator + document/object repositories + audit writer | Lock order `customer -> object_file -> current documents`; AVAILABLE/global-unattached/replay/stale rechecks in one transaction. | Safe changed/storage conflict; zero partial supersede/attachment/audit. |
| TX-C | `claim_unattached_object_for_compensation` | Lock object and prove global nonattachment before same-TX pending marker; no provider call. | Attached/replayed/missing/status-changed is NOOP; eligible orphan becomes `DELETE_PENDING`. |
| Eventual delete | existing `reconcile_stale_object_deletes` | Its DB claim closes before DELETE/HEAD and finalizes in a fresh transaction. | Definite `DELETED`; ambiguous `DELETE_PENDING` + `DELETE_OUTCOME_UNKNOWN`. |

### Routes, Authority, And Sensitive Data Flow

| Route | Trusted authority/input | Operation-local sensitive boundary | Persistent/public result |
|---|---|---|---|
| `GET /customer/identity` | Server session user -> own customer | Config gate, exact row decrypt, strict canonical validation; names/identifier plaintext only in memory | No-store HTML: names allowed, identifiers last-four only; form identifiers blank. |
| `POST /customer/identity` | Server session user, CSRF, expected revision, six submitted fields | Canonicalize/blind/encrypt within service; no plaintext ORM/audit/error | Encrypted row + safe audit; PRG/no-store or stable safe error. |
| `POST /customer/identity/document` | Server session user, CSRF, replay/current tokens, bounded file; object ID only from M8 result | CR phases above; no request Session during source/provider I/O | Concrete attachment/audit or safe failure plus optional TX-C claim; PRG/no-store. |
| `GET /customer/identity/document` | Server session user only; server resolves own current document/object | Concrete authorizer and M8 presign; URL only at final response boundary | Access audit plus no-store redirect/response; no ID/URL persistence/logging. |

Plaintext name/JSHSHIR/document number is forbidden outside the intended
in-memory crypto operation and authorized no-store own-user view. Blind index,
ciphertext, nonce, key ID/material, object-file ID, bucket/key/checksum,
filename, and presigned URL are forbidden in audit/log/error/report/default
repr. Customer/object/document UUID input is never own-customer/object
authority. Shop roles and `is_platform_admin` never expand access.

### Schema, Audit, Error, And Route Accounting

| Surface | Baseline | Exact M10 delta | Final total/boundary |
|---|---:|---|---|
| M10 domain tables | 0 | `customer_identities`, `customer_documents` | Exactly 2 |
| Alembic revisions after M9 | 0 | One linear child of `a9b0c1d2e3f4` | One head |
| M10 audit events | 0 | identity saved; document attached, superseded, access granted | Exactly 4 |
| M10 audit object types | 0 | `customer_identity`, `customer_document` | Exactly 2 |
| New stable errors | 0 | draft required, identity changed, document changed, identity unavailable, document unavailable | Exactly 5 |
| New routes | 0 | four fixed `/customer/identity*` routes | Exactly 4, no identifier parameters |
| New templates | 0 | `customer/identity.html` | Exactly 1 page template |
| Direct crypto dependencies | 0 | `cryptography>=50.0.0,<51` | Exactly 1 |
| M10 scheduler/delete API | 0 | none | 0 |

### Test And CI Placement

The M10.07 matrix is authoritative without renaming: M10-T001-M10-T102 map
to exactly the 102 nodes already listed across these 18 planned files:

```text
tests/test_customer_identity_crypto.py
tests/test_customer_identity_contracts.py
tests/test_customer_identity_postgresql.py
tests/test_customer_identity_concurrency_postgresql.py
tests/test_customer_identity_authorization.py
tests/test_customer_identity_web.py
tests/test_customer_identity_csrf.py
tests/test_customer_identity_xss.py
tests/test_customer_identity_sensitive_data_audit.py
tests/test_customer_document_attachment_postgresql.py
tests/test_customer_document_coordinator.py
tests/test_customer_document_concurrency_postgresql.py
tests/test_customer_document_presign.py
tests/test_customer_document_compensation.py
tests/test_customer_document_minio_integration.py
tests/test_m10_migration_postgresql.py
tests/test_m10_scope_containment.py
tests/test_m10_dependency_boundary.py
```

Existing fixtures remain authoritative:

- `tests/conftest.py:test_database_url`, `test_database_engine`, and
  `m2_test_database` for real PostgreSQL;
- `tests/storage_fake.py:FakeObjectStorageService` for deterministic injected
  storage;
- current M8 MinIO environment/fixtures and `.github/workflows/ci.yml` for the
  designated integration lane;
- current full-suite CI commands and frozen `uv sync --dev --frozen`.

M10 may extend those fixtures narrowly but does not add SQLite, `create_all`,
real cloud credentials, automatic skip/xfail, or a second CI workflow. The
exact M10 dependency, targeted matrix, full suite, Ruff, formatting,
migration walk, MinIO, protected-source, and leakage gates integrate into the
existing workflow style. Full closure requires zero failed/skipped/xfailed/
xpassed outcomes at the exact pushed SHA.

### M10.08 Disposition

- The one capability, IN/OUT boundary, 20/20 decisions, exact crypto/settings,
  two-table schema, four routes/events, five errors, and 102-threat plan now
  have concrete repository integration points.
- CR-M10-01 is authoritative in this map: early check plus ingest-only record,
  object-file lock pivot, same-TX TX-C pending claim, and existing M8
  reconciliation, with no request delete or M10 scheduler.
- M3 own-draft, M8 storage, M9 audit/error/localization, caller-owned
  transaction, sensitive-data, PostgreSQL/fake/MinIO, and containment
  boundaries are preserved.
- No product code, schema, migration, dependency, lockfile, template, test,
  CI, commit, or push was performed by M10.08.

Result: `M10.08 COMPLETE — AUTHORITATIVE REPOSITORY DOCS FROZEN`.
