# Nasiya M8 Scope Contract

Status: authoritative repository contract for M8 implementation.
Milestone: M8 — Secure Object Storage Foundation.
Source authority: `/home/yalgashev/projects/m8_00_final_scope_freeze.md`.
This file narrows repository execution only; it does not replace
`docs/tt_nasiya_web_v1.md`.

## Baseline

| Evidence | Value |
|---|---|
| Repository | `/home/yalgashev/projects/nasiya` |
| Branch | `main` |
| M7 implementation baseline | `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3` |
| M7 implementation CI | run `30453909901`, success |
| M7 docs-only closeout and M8 start HEAD | `1afa4af92fc905fe48af41e03506053619945445` |
| M7 closeout CI | run `30454876820`, success |
| Alembic parent head | `e7f8a9b0c1d2` |
| M7 full suite | `1666 passed`, no failed/skipped/xfailed/xpassed |
| M7 status | `M7 REMOTE GREEN — CLOSED` |

M8.01 rechecked this baseline read-only: clean synced `main`, divergence
`0 0`, both M7 SHAs in ancestry, exact-SHA CI green, one Alembic head, and
the TT blob unchanged between the implementation baseline and M8 start.

## One Capability

M8 implements exactly one capability:

Backend-mediated JPEG, PNG, and WebP images are bounded, fully decoded,
sanitized, re-encoded in the same format family, and stored in one private
S3-compatible bucket. PostgreSQL records a safe object lifecycle, external
storage failures are reconciled without blind PUT retries, and a presigned GET
can be produced only after an injected domain-parent authorizer allows access.

M8 is a transport-independent foundation. It does not attach an object to a
customer, owner application, shop news item, or any other product domain.

## Exact In Scope

- `ObjectStorageService` protocol and one boto3/botocore S3-compatible adapter.
- Private MinIO for local development and CI, with idempotent provisioning.
- One configured private bucket and `v1/objects/` random object-key prefix.
- Exactly one new table, `object_files`, in one linear Alembic revision.
- Backend-mediated image ingestion; raw browser-to-storage upload is forbidden.
- A reusable, opt-in ASGI pre-parse body guard. M8 has no production upload
  route, so its production protected-path set is empty.
- A bounded multipart helper: one exact file field, at most eight auxiliary
  fields, and existing session-bound CSRF validation.
- JPEG, PNG, and WebP selected from Pillow's fully decoded `Image.format`.
- Full decode, corruption/truncation/bomb checks, and `n_frames == 1`.
- EXIF orientation before a fresh pixel-only copy and metadata-free re-encode.
- Same-family encoders with fixed reviewed settings.
- Input and sanitized-output hard limit `10_485_760` bytes.
- Request envelope hard limit `11_010_048` bytes.
- Pixel hard limit `40_000_000`; each dimension hard limit `16_384`.
- Canonical MIME/extension from decoded format and lowercase SHA-256 over the
  exact sanitized bytes.
- Random key `v1/objects/<uuid4-lowerhex>.<jpg|png|webp>`.
- Lifecycle `PENDING_UPLOAD`, `AVAILABLE`, `FAILED`, `DELETE_PENDING`,
  `DELETED`.
- Upload order: sanitize, TX-S1 commit, external PUT/HEAD, TX-S2 commit.
- Immediate HEAD reconciliation for ambiguous PUT; no automatic PUT retry.
- Internal stale-upload reconciliation and delete primitives/CLI.
- Authorization-gated presigned GET for `AVAILABLE` rows, default TTL `300s`.
- Existing HMAC limiter with storage-specific user/IP limits `5/20` per
  `900s`.
- Fake adapter tests, real PostgreSQL tests, and designated real MinIO tests.
- Storage preflight, smoke, private-policy, degraded-mode, and local
  backup/restore operational coverage.
- Web startup and `/health` remain independent of storage configuration.
- M6 Telegram worker and M7 OTP dispatcher remain storage-independent.

## Exact Out Scope

- Registration, customer activation, `customer_document`, owner application,
  admin approval, or shop-news image integration.
- JSHSHIR, passport, F.I.Sh., new PII, PII encryption, offer, or acceptance.
- Generic user file vault, media library, or attachment polymorphism.
- Production public upload, download, or delete route.
- Public bucket/ACL, CDN, public URL, or browser presigned PUT.
- Antivirus, scanner, quarantine bucket, OCR, classification, or thumbnails.
- Production S3 vendor/SLA/RPO/RTO selection, custom CA, mTLS, or replication.
- Domain/legal retention, automatic `AVAILABLE` purge, or terminal-row purge.
- Generic scheduler, `job_run`, outbox, audit platform, message bus, Redis,
  Celery, or RQ.
- Weighted-byte quota table or any new rate-limit table.
- Debt, payment, rating, notification, report, or M9 capability.
- Changes to M6 inbound worker or M7 OTP dispatcher roles.

## Inherited Contracts

| Contract | Authority | Repository evidence | M8 consequence |
|---|---|---|---|
| Caller owns request transaction | M4/M6/M7 contracts | `app/db.py:22` | Repositories/services never commit, fully roll back, or close the caller session. |
| External I/O is outside SQL transaction | M6/M7 two-phase patterns | `app/telegram/update_processing.py:128`, `app/otp/dispatcher.py:425`, `app/otp/dispatcher.py:442`, `app/otp/dispatcher.py:465` | Storage coordinator closes TX-S1 before PUT/HEAD and opens a fresh TX-S2. |
| Non-request coordinators own short sessions | M6/M7 runtime | `app/telegram/update_processing.py:136`, `app/otp/dispatcher.py:432`, `app/otp/dispatcher.py:472` | CLI/reconcile/delete use `session_factory.begin()` around DB-only phases. |
| Web starts in degraded mode | M6/M7 runtime | `app/main.py:27`, `app/main.py:57`, `app/settings.py:59` | Storage settings are optional at startup and required only by storage operations. |
| M6/M7 process isolation | M6/M7 scope | `compose.yaml:46`, `compose.yaml:72` | Worker/dispatcher receive no storage settings or MinIO dependency. |
| Secret values are typed/redacted | TT 8; M6/M7 | `app/settings.py:6`, `app/settings.py:31`, `app/telegram/bot_api.py:63`, `app/otp/crypto.py:33` | Access/secret keys and sensitive wrappers use `SecretStr` or `repr=False`. |
| Trusted IP only | M4/M6/M7 | `app/request_client_ip.py:19` | IP limiter uses only `resolve_client_ip`. |
| Existing HMAC rate limiter | M2/M6/M7 | `app/auth/rate_limit.py:32`, `app/auth/rate_limit.py:183` | Storage-specific scopes reuse `auth_rate_limits`; no new table. |
| Session-bound CSRF | TT 8; M6/M7 | `app/auth/deps.py:164`, `app/auth/deps.py:241` | Multipart helper is not exempt and must preserve the existing token semantics. |
| Auth/private responses use no-store | TT 8; M5–M7 | `app/security_headers.py:61` | Future domain adapters mark responses no-store; M8 adds no public response. |
| One CI job | M6/M7 decisions | `.github/workflows/ci.yml:12` | MinIO integration stays inside `dependency-sync`. |
| Real PostgreSQL only | Repository test contract | `tests/postgresql.py:29` | No SQLite, `create_all`, skip, xfail, or softened assertions. |

No unresolved TT, M7, or M8 freeze contradiction exists. TT's future
`object_file` owner scope, customer documents, news images, and scheduler are
later domain capabilities; M8 deliberately supplies only the shared private
storage foundation.

## Dependency Approval Contract

M8 may add exactly two direct runtime dependencies:

- `Pillow`;
- `boto3`, with `botocore`, `jmespath`, and `s3transfer` only as resolved
  transitives required by boto3.

M8.07 must verify package metadata, license, Python 3.12 support, wheel
availability, and the resolved dependency graph before changing
`pyproject.toml` and `uv.lock`. The exact resolved versions and licenses are
then recorded in `docs/m8_decisions.md` and `docs/m8_repository_map.md`.

No MinIO SDK, `python-magic`, libmagic, native codec package, antivirus
package, Redis, Celery, or other direct runtime dependency is approved. A
no-cache build must prove that the Pillow wheel in the current
`python:3.12-slim` image decodes and encodes JPEG, PNG, and WebP without an OS
package change. If it cannot, M8 stops for scope review.

## Settings Contract

The following optional bundle belongs in the existing `Settings` class:

```text
OBJECT_STORAGE_ENDPOINT_URL
OBJECT_STORAGE_REGION
OBJECT_STORAGE_BUCKET
OBJECT_STORAGE_ACCESS_KEY
OBJECT_STORAGE_SECRET_KEY
OBJECT_STORAGE_USE_SSL
OBJECT_STORAGE_ADDRESSING_STYLE=path
OBJECT_STORAGE_PRESIGNED_TTL_SECONDS=300
OBJECT_STORAGE_MAX_UPLOAD_BYTES=10485760
OBJECT_STORAGE_MAX_MULTIPART_BYTES=11010048
OBJECT_STORAGE_MAX_IMAGE_PIXELS=40000000
OBJECT_STORAGE_MAX_IMAGE_DIMENSION=16384
OBJECT_STORAGE_UPLOAD_RATE_LIMIT_WINDOW_SECONDS=900
OBJECT_STORAGE_UPLOAD_RATE_LIMIT_USER_ATTEMPTS=5
OBJECT_STORAGE_UPLOAD_RATE_LIMIT_IP_ATTEMPTS=20
OBJECT_STORAGE_RECONCILE_STALE_SECONDS=60
```

Rules:

- access and secret keys are `SecretStr | None`;
- endpoint, region, bucket, access key, and secret key are a complete bundle;
- absent bundle is valid for web startup; a partial bundle fails closed only
  when `require_object_storage_config()` is called;
- endpoint scheme and `USE_SSL` agree;
- addressing style is a bounded supported value and defaults to `path`;
- bucket syntax is strictly validated and is never taken from a request;
- TTL is `60..900`;
- upload maximum cannot exceed `10_485_760`;
- multipart maximum is greater than upload maximum and no greater than
  upload maximum plus `1_048_576`;
- dimensions, pixels, rate-limit values, and stale threshold are positive and
  cannot weaken the frozen defaults;
- settings repr, validation errors, model dumps, logs, and CLI output do not
  reveal endpoint or credentials;
- there is no secret hot reload or local-disk fallback.

## Upload Body Contract

The ASGI body guard is path-opt-in and executes before Starlette form parsing:

1. a declared `Content-Length` over `11_010_048` is rejected early;
2. absent, invalid, conflicting, or forged length never bypasses counting;
3. actual `http.request` body bytes are counted and limit+1 is rejected;
4. disconnect and downstream failure clean up without body logging;
5. unrelated routes are byte-for-byte behaviorally unchanged.

The bounded multipart helper takes `Request` rather than a FastAPI
`UploadFile` parameter. It parses with `max_files=1`, `max_fields=8`, and
`max_part_size=10_485_760`, requires one exact file field, bounds auxiliary
string fields, preserves session-bound CSRF, and closes the `UploadFile`.
M8 has no production upload route.

## Image Sanitization Contract

Input is read in bounded chunks and only up to limit+1. Empty input is
rejected. The reader does not log filename, claimed MIME, source bytes, or a
temporary path.

Decoded-format mapping:

| Pillow format | Canonical MIME | Canonical extension | Canonical mode |
|---|---|---|---|
| `JPEG` | `image/jpeg` | `jpg` | `RGB` |
| `PNG` | `image/png` | `png` | `RGB` or `RGBA` |
| `WEBP` | `image/webp` | `webp` | `RGB` or `RGBA` |

Filename extension and claimed MIME are ignored. The decode sequence is:

1. open bounded bytes;
2. capture and validate actual format, frame count, and pre-load dimensions;
3. treat Pillow decompression warning/error as fatal;
4. verify, reopen, and fully load;
5. recheck format, frame count, dimensions, and pixels;
6. apply `ImageOps.exif_transpose`;
7. recheck post-orientation dimensions and pixels;
8. build a fresh canonical image from pixel data only;
9. save without EXIF, GPS, XMP, ICC, comments, thumbnails, text chunks, or
   source `.info`;
10. reopen and fully decode output, verify format/dimensions/metadata absence;
11. enforce output byte limit and compute SHA-256 over exact output bytes.

Any `n_frames != 1` is unsupported. GIF, animated WebP, and APNG are rejected;
there is no first-frame fallback. Truncated/corrupt input is rejected and
`LOAD_TRUNCATED_IMAGES` is not enabled.

Fixed encoders:

```text
JPEG: quality=90, optimize=True, progressive=False, mode=RGB
PNG:  optimize=True, compress_level=9, mode=RGB|RGBA
WebP: lossless=True, method=6, mode=RGB|RGBA
```

There is no resize, quality fallback, or cross-format conversion.

The typed sanitized result contains only canonical content type/extension,
size, width, height, checksum, and an explicitly revealed in-memory sanitized
byte wrapper. Default `str`/`repr` hides bytes and full checksum.

## Persistence Contract

M8 creates exactly one table in one Alembic revision whose parent is
`e7f8a9b0c1d2`.

### `object_files`

```text
id UUID PRIMARY KEY
bucket VARCHAR(63) NOT NULL
object_key VARCHAR(255) NOT NULL
content_type VARCHAR(32) NOT NULL
size_bytes BIGINT NOT NULL
checksum_sha256 VARCHAR(64) NOT NULL
width_px INTEGER NOT NULL
height_px INTEGER NOT NULL
status VARCHAR(32) NOT NULL
created_by_user_id UUID NOT NULL
failure_code VARCHAR(64) NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
available_at TIMESTAMPTZ NULL
terminal_at TIMESTAMPTZ NULL
deleted_at TIMESTAMPTZ NULL
```

Required named objects:

```text
fk_object_files_created_by_user_id_users_id
uq_object_files_bucket_object_key
ck_object_files_bucket_format
ck_object_files_object_key_format
ck_object_files_content_type_allowed
ck_object_files_size_bytes
ck_object_files_checksum_sha256
ck_object_files_dimensions
ck_object_files_status_allowed
ck_object_files_failure_code_format
ck_object_files_state_consistent
ck_object_files_timestamp_order
ix_object_files_status_updated_at
ix_object_files_created_by_user_id_created_at
```

Rules:

- `created_by_user_id -> users.id ON DELETE RESTRICT`;
- key regex `^v1/objects/[0-9a-f]{32}\.(jpg|png|webp)$`;
- content type is exactly one of the three canonical MIME values;
- size is `1..10_485_760`;
- checksum is lowercase 64-hex;
- dimensions are `1..16_384`, with bigint product `<=40_000_000`;
- safe failure code matches `^[A-Z][A-Z0-9_]{0,63}$`;
- bucket/key pair is unique.

State rules:

```text
PENDING_UPLOAD: available_at/terminal_at/deleted_at NULL;
                failure_code NULL or safe unknown code
AVAILABLE:      available_at NOT NULL; terminal_at/deleted_at/failure_code NULL
FAILED:         available_at NULL; terminal_at and failure_code NOT NULL;
                deleted_at NULL
DELETE_PENDING: terminal_at/deleted_at NULL; failure_code optional
DELETED:        terminal_at and deleted_at NOT NULL
```

All timestamps are timezone-aware UTC, `updated_at >= created_at`, and later
state timestamps cannot precede creation/availability.

Forbidden columns include original filename, claimed MIME, public/presigned
URL, raw bytes, EXIF/XMP/ICC, owner polymorphism/domain attachment, ACL,
provider ETag as checksum, credential, arbitrary JSON, or provider error body.
`created_by_user_id` is accountability only and never grants read access.

## Upload And Failure Protocol

Happy path:

```text
bounded read + sanitize (no DB transaction)
generate UUID/key
TX-S1: create PENDING_UPLOAD; outer coordinator commits and closes session
external PUT exact sanitized bytes
external HEAD same key and verify size/content-type/checksum
TX-S2: lock row, mark AVAILABLE; outer coordinator commits
```

TX-S1 failure causes zero provider calls. No SQLAlchemy session or transaction
is open during PUT, HEAD, DELETE, presign, or an HTTP fetch. A successful
result exposes object UUID and safe metadata only.

Definite no-send/provider rejection:

- one PUT attempt at most;
- fresh TX-S2 marks `FAILED` with an allowlisted safe code;
- a TX-S2 failure leaves a stale pending row for reconciliation.

Ambiguous timeout/post-send outcome:

- never resend PUT and never generate a new key/row;
- immediate HEAD of the same key;
- exact match -> `AVAILABLE`;
- missing -> remain `PENDING_UPLOAD` with `UPLOAD_OUTCOME_UNKNOWN`;
- mismatch -> `DELETE_PENDING`, external delete, then `DELETED`;
- delete uncertainty leaves `DELETE_PENDING` with `DELETE_OUTCOME_UNKNOWN`.

## Reconciliation And Delete Contract

`reconcile_stale_object_uploads(now, batch_size)`:

- accepts batch `1..5000`;
- selects stale `PENDING_UPLOAD` in deterministic order;
- uses `FOR UPDATE SKIP LOCKED` in a short claim transaction;
- advances `updated_at` as the claim marker and closes the session before HEAD;
- exact HEAD -> fresh TX `AVAILABLE`;
- missing -> fresh TX `FAILED/OBJECT_MISSING_AFTER_UPLOAD`;
- mismatch -> fresh TX `DELETE_PENDING`, external delete, fresh TX `DELETED`;
- never PUTs, schedules itself, or exposes a route.

Internal delete:

```text
AVAILABLE -> DELETE_PENDING -> external DELETE/HEAD -> DELETED
```

Provider missing is delete success. Timeout stays `DELETE_PENDING` with a safe
code for later bounded reconciliation. Delete and reconcile are idempotent and
concurrent-safe. M8 adds no public delete route and no automatic retention.

## Adapter Contract

`ObjectStorageService` provides only:

```text
put_object(...)
head_object(...)
delete_object(...)
create_presigned_get_url(...)
ensure_private_bucket(...)
```

The boto3 client is injected/testable, uses SigV4, configured endpoint/region,
configured path or virtual addressing, explicit M8 credentials, bounded
timeouts, and no automatic retry. Import and construction make no network
call. The default credential chain is not used for an M8 operation.

PUT is one `PutObject` with no ACL/public URL and includes the sanitized
SHA-256 as object metadata. HEAD returns typed size/content type/checksum or a
typed missing result. Provider ETag is never treated as the checksum. DELETE
is idempotent. Provider exceptions become closed sanitized outcomes without
endpoint, bucket, key, credential, request ID, response body, or SDK detail.

## Authorization And Presigned Access

M8 has no file route. Its service takes authenticated actor context, a
domain-parent authorization request, and object UUID.

1. injected `ObjectFileAccessAuthorizer` verifies the parent refers to the
   same object and allows the actor;
2. missing and denied map identically to `FILE_ACCESS_DENIED`;
3. denial performs zero HEAD/presign calls;
4. only an `AVAILABLE` row may proceed;
5. `created_by_user_id` alone is insufficient;
6. a GET URL is signed for the configured TTL, default `300s`;
7. URL is kept in a redacted wrapper and is never persisted or logged.

No presigned PUT exists.

## Stable Errors

Public stable codes:

```text
FILE_ACCESS_DENIED
FILE_STORAGE_ERROR
FILE_TOO_LARGE
UNSUPPORTED_FILE_TYPE
```

Closed internal safe codes:

```text
STORAGE_CONFIGURATION_UNAVAILABLE
STORAGE_PROVIDER_UNAVAILABLE
IMAGE_CORRUPT
IMAGE_TRUNCATED
IMAGE_PIXEL_LIMIT_EXCEEDED
IMAGE_DIMENSION_LIMIT_EXCEEDED
IMAGE_ANIMATION_UNSUPPORTED
SANITIZED_OUTPUT_TOO_LARGE
UPLOAD_OUTCOME_UNKNOWN
OBJECT_METADATA_MISMATCH
OBJECT_MISSING_AFTER_UPLOAD
DELETE_OUTCOME_UNKNOWN
```

Internal details never change the public localized message or expose sensitive
values.

## MinIO, CI, And Backup Contract

Local Compose adds pinned `minio`, pinned `minio-init`, and `minio-data`.
Root credentials are available only to MinIO/init. Idempotent init creates the
bucket, removes anonymous access, creates/updates a bucket-scoped application
user, attaches only required object operations, and verifies anonymous deny.
The web receives app credentials only when configured. Database migration,
web, M6 worker, and M7 dispatcher do not depend on MinIO health.

CI keeps the single `dependency-sync` job and PostgreSQL service. It adds a
bounded MinIO runtime and private-policy integration with test-only or
runtime-generated credentials masked before use. CI uses no real cloud
credential or network.

The runbook covers provisioning, rotation, degraded mode, preflight,
reconciliation, delete, private policy, safe troubleshooting, volume
persistence, backup, and restore. The local exercise mirrors objects to a
temporary backup, restores to a temporary private bucket, verifies object
count plus checksum metadata/content, and cleans up without `down -v`.
Production provider selection, legal retention, and provider-specific
RPO/RTO remain deferred.

## Validation Contract

Every checkpoint keeps:

- `uv sync --dev --frozen`;
- Ruff check and format check;
- relevant targeted tests;
- real PostgreSQL tests and fake storage adapter except designated MinIO tasks;
- one linear Alembic head;
- no skip/xfail/xpass or assertion weakening;
- M5/M6/M7 containment and process-isolation regressions;
- TT, scope, secret, metadata, key, and URL leakage audits;
- `git diff --check`.

Final M8 closure additionally requires a no-cache built-image codec/runtime
check, real local MinIO acceptance `16/16`, local backup/restore exercise,
eight exact checkpoint commits, exact pushed-SHA remote CI success, a
docs-only closeout, and clean synced `main`.
