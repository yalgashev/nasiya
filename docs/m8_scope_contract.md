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

## Post-Freeze Product Owner Correction — Provisioning And Data Plane

Recovery Fix 1 records a post-freeze Product Owner correction. It supersedes
the M8.42 runtime `ensure_private_bucket` and private-policy-preflight clauses
that previously mixed provisioning with application credentials. The frozen
source remains unchanged.

Provisioning is an admin-plane responsibility owned by
`deploy/minio-init.sh`, using root/admin credentials. It creates the configured
bucket if missing, enforces and verifies private anonymous access, and
creates/updates the bucket-scoped application identity and policy. Web and the
storage CLI receive only the application identity. Their
`ObjectStorageService` is data-plane only: configured-bucket access check,
PUT, HEAD, DELETE, and presigned GET. It never creates a bucket, inspects ACL
or bucket policy, calls PublicAccessBlock APIs, or performs another admin
operation.

`storage preflight` requires a complete application storage configuration and
checks only data-plane access to the configured bucket. It does not claim to
verify privacy or anonymous denial and emits only the fixed safe success or
failure status. Privacy and anonymous-deny proof belongs to `minio-init`,
designated real-MinIO acceptance tests, and CI provisioning checks. Root
credentials are never supplied to web, storage CLI, M6 worker, or M7
dispatcher.

## Post-Freeze Product Owner Correction — Immediate Missing HEAD

Recovery Fix 2 records a second post-freeze Product Owner correction. It
supersedes the requirement that a successful PUT followed by an immediate
missing HEAD is terminal. The frozen source remains unchanged.

An immediate missing HEAD is not definitive object absence, whether PUT
returned success or an ambiguous result. A fresh transaction locks the same
row, keeps it `PENDING_UPLOAD`, records
`failure_code=UPLOAD_OUTCOME_UNKNOWN`, advances `updated_at` with the injected
time, and returns the closed file error without an object result. The
coordinator performs exactly one PUT and one immediate HEAD; it does not
delete, presign, create another row/key, sleep, or retry PUT.

After the stale threshold, bounded reconciliation HEADs that exact row/key.
Exact metadata transitions it to `AVAILABLE`; a stale missing result may
transition it to `FAILED/OBJECT_MISSING_AFTER_UPLOAD`; mismatch transitions
through `DELETE_PENDING`, bounded delete, and `DELETED`. Supported production
providers must pass an explicit object-and-metadata read-after-write
visibility compatibility check before rollout. That rollout requirement does
not make an immediate missing HEAD terminal.

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
- Immediate HEAD verification for successful or ambiguous PUT; a missing
  result remains durably reconcilable and there is no automatic PUT retry.
- Internal stale-upload reconciliation and delete primitives/CLI.
- Authorization-gated presigned GET for `AVAILABLE` rows, default TTL `300s`.
- Existing HMAC limiter with storage-specific user/IP limits `5/20` per
  `900s`.
- Fake adapter tests, real PostgreSQL tests, and designated real MinIO tests.
- App-credential data-plane preflight, smoke, admin-plane private-policy,
  degraded-mode, and local backup/restore operational coverage.
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

Recovery Fixes 1 and 2 resolve the post-freeze provisioning/data-plane and
immediate-missing-HEAD contradictions through the Product Owner corrections
above; no unresolved TT, M7, or M8 contract contradiction remains. TT's future
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

## Storage Upload Rate-Limit Contract

`app/storage/rate_limit.py` reuses `AuthRateLimiter` and the existing
`auth_rate_limits` table with exactly two non-colliding scopes:

```text
scope storage_upload_user
raw HMAC input storage_upload:user:<authenticated UUID>
allowed attempts default/cap 5

scope storage_upload_ip
raw HMAC input storage_upload:ip:<ResolvedClientIp canonical value>
allowed attempts default/cap 20

window default/minimum 900 seconds
```

The policy converts each configured allowed-attempt count to the existing
limiter's exclusive rejection threshold by adding one. Therefore attempts
1..5 for a user and 1..20 for an IP are allowed; attempts 6 and 21 are
rejected, including under concurrent transactions. The window resets only at
`window_started_at + 900s`, not one second earlier. Safer configured lower
attempt caps or a longer window remain valid.

`check_storage_upload_rate_limit` is read-only.
`record_storage_upload_attempt` checks user then IP in stable order and, only
when both prechecks allow, records both in the caller-owned transaction. The
policy never commits, rolls back, or closes the Session. Only HMAC-SHA256
key hashes and the two fixed scopes reach the database; raw UUID/IP values do
not. User and IP rejection return the same `RATE_LIMITED` code/body and never
name the limiting scope.

The upload coordinator completes this short DB-only preflight before reading
or decoding the borrowed image source. A rejected attempt creates no
`object_files` row and performs no sanitizer, PUT, HEAD, DELETE, or presign
call. There is no byte weighting, global quota, bucket quota, additional
table, or clear-on-success behavior.

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

## Exact Image Sanitizer Appendix (M8.16)

This appendix freezes the implementation details for M8.17–M8.25 against
Pillow `12.3.0`. It does not add a route, database operation, storage call, or
source-file persistence.

### Input ownership and bounded read

- The source is the existing Starlette `UploadFile`/spooled object yielded by
  the bounded multipart context; no second temporary file is created.
- The reader treats the source as an async `read(size)`/`seek(offset)` object,
  seeks to byte zero once, and requests chunks of at most `65_536` bytes.
- The accumulated source buffer can contain at most `10_485_761` bytes. Each
  request is capped at the remaining bytes through limit+1, so a compliant
  source is never read past that boundary.
- Zero bytes are rejected. Exactly `10_485_760` bytes are allowed;
  `10_485_761` bytes are `FILE_TOO_LARGE`.
- `read()` must return `bytes`; a read/seek failure becomes a safe boundary
  failure without the exception text, filename, claimed MIME, body, or spool
  path.
- The reader borrows the file object. The M8.13 multipart context is its owner
  and closes the `UploadFile` in `finally`, including parse, read, validation,
  sanitizer, and downstream failures. Direct test doubles are closed by their
  creating fixture.
- The source bytes exist only in a function-local bounded buffer and a
  function-local `BytesIO`; neither is stored, logged, included in an error,
  or exposed by default `repr`/`str`.

The ASGI envelope remains `11_010_048` bytes, the single file-part and reader
limit remain `10_485_760` bytes, and the decoded limits remain `16_384` per
dimension and `40_000_000` pixels.

### Pillow decode sequence

Every Pillow operation that can decode input runs inside a local
`warnings.catch_warnings()` block with
`Image.DecompressionBombWarning` promoted to an exception. The implementation
does not mutate `Image.MAX_IMAGE_PIXELS` or enable
`ImageFile.LOAD_TRUNCATED_IMAGES`.

The exact sequence is:

1. Construct a fresh `BytesIO` over the bounded immutable source bytes.
2. Call `Image.open()`. Capture `image.format`; it must be exactly `JPEG`,
   `PNG`, or `WEBP`. Filename extension and submitted content type are never
   consulted.
3. Read `image.size`, validate each dimension in `1..16_384`, and validate
   `width * height <= 40_000_000`.
4. Treat `getattr(image, "n_frames", 1) != 1` or
   `getattr(image, "is_animated", False) is True` as unsupported animation.
   This fallback is required because a Pillow JPEG object has no `n_frames`
   attribute.
5. Call `image.verify()`, close that image, seek a new `BytesIO` to byte zero,
   and call `Image.open()` again. The first object is never reused after
   `verify()`.
6. Recheck actual format, dimensions, pixel product, frame count, and
   animation status on the reopened image, then call `image.load()` for a full
   decode.
7. Recheck dimensions and pixel product after `load()`.
8. Apply `ImageOps.exif_transpose()` to the fully loaded image. Malformed EXIF
   is a safe sanitizer failure; raw EXIF or its exception detail is never
   rendered.
9. Recheck transformed dimensions and pixel product because orientations
   `5..8` can swap width and height.

`UnidentifiedImageError`, syntax/value failures, or a failed `verify()` map to
`IMAGE_CORRUPT`. An `OSError` during verify/reopen/load maps to
`IMAGE_TRUNCATED`. Pillow decompression warnings/errors and explicit pixel
overflow map to `IMAGE_PIXEL_LIMIT_EXCEEDED`; a per-axis overflow maps to
`IMAGE_DIMENSION_LIMIT_EXCEEDED`. Frame violations map to
`IMAGE_ANIMATION_UNSUPPORTED`. All of these publicize only
`UNSUPPORTED_FILE_TYPE`.

Structurally valid trailing container bytes, if accepted by both Pillow
verification and full decode, are never copied: only decoded pixels enter the
fresh output. Structural corruption is rejected. M8 does not add a second
hand-written JPEG/PNG/WebP container parser.

### Canonical pixel-only image

The source format fixes the output family:

| Source `Image.format` | Output MIME | Extension | Fresh mode |
|---|---|---|---|
| `JPEG` | `image/jpeg` | `jpg` | always `RGB` |
| `PNG` | `image/png` | `png` | `RGBA` when transparency exists, otherwise `RGB` |
| `WEBP` | `image/webp` | `webp` | `RGBA` when transparency exists, otherwise `RGB` |

For PNG/WebP, transparency is decided after orientation from Pillow
`image.has_transparency_data`; this covers alpha modes and palette
transparency. The oriented image is converted to the selected canonical mode,
then a new image is created with
`Image.frombytes(mode, size, converted.tobytes())`. The fresh image receives
no source `.info`, palette, EXIF, ICC profile, XMP, GPS, comment, thumbnail,
text chunk, filename, or claimed MIME.

There is no first-frame selection, resize, crop, quality retry, mode fallback,
or conversion to a different format family.

### Fixed encode and bounded output

The fresh image is saved to a function-local in-memory output using exactly:

```text
JPEG: format="JPEG", quality=90, optimize=True, progressive=False
PNG:  format="PNG", optimize=True, compress_level=9
WEBP: format="WEBP", lossless=True, method=6
```

No `exif`, `icc_profile`, `xmp`, `comment`, `pnginfo`, or source `info`
argument is passed. Encoding is deterministic for the same canonical pixels,
Pillow/codec version, platform image, and fixed arguments; cross-version
byte identity is not promised.

The encoder output is observed through a capped in-memory writer. It retains
at most `10_485_761` bytes and aborts further writes once limit+1 is known.
Output of exactly `10_485_760` bytes is allowed; limit+1 maps through
`SANITIZED_OUTPUT_TOO_LARGE` to `FILE_TOO_LARGE`. There is no quality or
compression fallback.

### Output reopen and metadata verification

Before returning, the exact encoded bytes are reopened in a new `BytesIO`,
fully loaded under the same fatal decompression-warning policy, and checked:

- format, canonical mode, dimensions, pixel product, `n_frames == 1`, and
  `is_animated is False` match the expected result;
- `len(image.getexif()) == 0`;
- EXIF, GPS, XMP, ICC, comment, thumbnail, and source text keys are absent;
- for PNG, `image.text` is empty;
- JPEG encoder-owned JFIF fields and static WebP structural
  `background`/`duration`/`loop`/`timestamp` fields are allowed, but they are
  not copied from source metadata;
- a second full decode succeeds.

An output reopen or verification mismatch fails closed as
`IMAGE_CORRUPT`/`UNSUPPORTED_FILE_TYPE`; no partially sanitized bytes are
returned.

### Typed success and failure surface

On success, the sanitizer returns exactly `SanitizedImage`:

```text
metadata.content_type
metadata.canonical_extension
metadata.size_bytes
metadata.width_px
metadata.height_px
metadata.checksum_sha256
sanitized_bytes
```

The checksum is lowercase SHA-256 over the exact bytes that passed output
reopen verification. `ObjectChecksumSha256` and `SanitizedImageBytes` keep
their values redacted from default `repr`/`str`; only their existing narrow
internal reveal methods expose them to the later key/storage coordinator.

The frozen failure mapping is:

| Boundary | Safe internal/public outcome |
|---|---|
| empty input, unidentified/corrupt format, failed output verification | `IMAGE_CORRUPT` → `UNSUPPORTED_FILE_TYPE` |
| truncated/full-decode `OSError` | `IMAGE_TRUNCATED` → `UNSUPPORTED_FILE_TYPE` |
| dimension overflow | `IMAGE_DIMENSION_LIMIT_EXCEEDED` → `UNSUPPORTED_FILE_TYPE` |
| pixel/decompression-bomb violation | `IMAGE_PIXEL_LIMIT_EXCEEDED` → `UNSUPPORTED_FILE_TYPE` |
| multi-frame/animated input | `IMAGE_ANIMATION_UNSUPPORTED` → `UNSUPPORTED_FILE_TYPE` |
| input or encoded output limit+1 | `FILE_TOO_LARGE` |
| source read/seek infrastructure failure | `FILE_STORAGE_ERROR` |

No failure includes source bytes, raw metadata, filename, claimed MIME, a
temporary path, full checksum, or a partially encoded result. No raw or
sanitized image is written to the database or filesystem by the sanitizer.

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

## Exact `object_files` Schema Appendix (M8.27)

The only M8 revision has parent `e7f8a9b0c1d2` and creates exactly one table,
`object_files`. It creates no PostgreSQL enum, sequence, trigger, function,
view, second table, or data migration. UUID values are application-generated.

### Exact columns

| Column | SQLAlchemy/PostgreSQL type | Null | Default / semantic |
|---|---|---:|---|
| `id` | `postgresql.UUID(as_uuid=True)` | no | primary key; Python `uuid4`, no server default |
| `bucket` | `String(63)` / `VARCHAR(63)` | no | configured private bucket |
| `object_key` | `String(255)` / `VARCHAR(255)` | no | generated versioned PII-free key |
| `content_type` | `String(32)` / `VARCHAR(32)` | no | canonical MIME only |
| `size_bytes` | `BigInteger` / `BIGINT` | no | exact sanitized byte length |
| `checksum_sha256` | `String(64)` / `VARCHAR(64)` | no | exact sanitized-byte SHA-256 |
| `width_px` | `Integer` / `INTEGER` | no | canonical width |
| `height_px` | `Integer` / `INTEGER` | no | canonical height |
| `status` | `String(32)` / `VARCHAR(32)` | no | no database or Python default; coordinator supplies it |
| `created_by_user_id` | `postgresql.UUID(as_uuid=True)` | no | accountability FK, not access ownership |
| `failure_code` | `String(64)` / `VARCHAR(64)` | yes | safe internal code only |
| `created_at` | `DateTime(timezone=True)` / `TIMESTAMPTZ` | no | Python UTC now and server `CURRENT_TIMESTAMP` |
| `updated_at` | `DateTime(timezone=True)` / `TIMESTAMPTZ` | no | Python UTC now and server `CURRENT_TIMESTAMP`; repository advances explicitly |
| `available_at` | `DateTime(timezone=True)` / `TIMESTAMPTZ` | yes | first verified availability |
| `terminal_at` | `DateTime(timezone=True)` / `TIMESTAMPTZ` | yes | failed/deleted terminal transition |
| `deleted_at` | `DateTime(timezone=True)` / `TIMESTAMPTZ` | yes | confirmed provider deletion |

Application datetimes are timezone-aware UTC. `TIMESTAMPTZ` is the database
enforcement surface; no naive local-time column is permitted. There is no
implicit ORM `onupdate`: state primitives set `updated_at` to their injected
UTC `now`.

The sole foreign key is:

```text
created_by_user_id -> users.id
name: fk_object_files_created_by_user_id_users_id
ON DELETE RESTRICT
```

It has no delete cascade. Deleting a user cannot silently delete or orphan an
external object record.

### Exact named unique and indexes

```text
UNIQUE (bucket, object_key)
  name: uq_object_files_bucket_object_key

INDEX (status, updated_at)
  name: ix_object_files_status_updated_at
  unique: false

INDEX (created_by_user_id, created_at)
  name: ix_object_files_created_by_user_id_created_at
  unique: false
```

No additional M8 unique or secondary index is created.

### Exact named checks

`ck_object_files_bucket_format`:

```sql
bucket ~ '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'
AND bucket !~ '\.\.'
AND bucket !~ '\.-'
AND bucket !~ '-\.'
AND bucket !~ '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'
```

`ck_object_files_object_key_format`:

```sql
object_key ~ '^v1/objects/[0-9a-f]{32}\.(jpg|png|webp)$'
```

`ck_object_files_content_type_allowed`:

```sql
content_type IN ('image/jpeg', 'image/png', 'image/webp')
```

`ck_object_files_size_bytes`:

```sql
size_bytes BETWEEN 1 AND 10485760
```

`ck_object_files_checksum_sha256`:

```sql
checksum_sha256 ~ '^[0-9a-f]{64}$'
```

`ck_object_files_dimensions`:

```sql
width_px BETWEEN 1 AND 16384
AND height_px BETWEEN 1 AND 16384
AND width_px::bigint * height_px::bigint <= 40000000
```

The explicit bigint casts prevent integer overflow before multiplication.

`ck_object_files_status_allowed`:

```sql
status IN (
  'PENDING_UPLOAD',
  'AVAILABLE',
  'FAILED',
  'DELETE_PENDING',
  'DELETED'
)
```

`ck_object_files_failure_code_format`:

```sql
failure_code IS NULL
OR failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
```

`ck_object_files_state_consistent` is exactly the disjunction:

```sql
(
  status = 'PENDING_UPLOAD'
  AND available_at IS NULL
  AND terminal_at IS NULL
  AND deleted_at IS NULL
  AND (
    failure_code IS NULL
    OR failure_code = 'UPLOAD_OUTCOME_UNKNOWN'
  )
)
OR (
  status = 'AVAILABLE'
  AND available_at IS NOT NULL
  AND terminal_at IS NULL
  AND deleted_at IS NULL
  AND failure_code IS NULL
)
OR (
  status = 'FAILED'
  AND available_at IS NULL
  AND terminal_at IS NOT NULL
  AND deleted_at IS NULL
  AND failure_code IS NOT NULL
)
OR (
  status = 'DELETE_PENDING'
  AND terminal_at IS NULL
  AND deleted_at IS NULL
  AND (
    failure_code IS NULL
    OR failure_code IN (
      'OBJECT_METADATA_MISMATCH',
      'DELETE_OUTCOME_UNKNOWN'
    )
  )
)
OR (
  status = 'DELETED'
  AND terminal_at IS NOT NULL
  AND deleted_at IS NOT NULL
)
```

`available_at` intentionally remains either null or non-null for
`DELETE_PENDING` and `DELETED`: a normal delete originates from `AVAILABLE`,
while cleanup of a mismatched ambiguous upload was never available.
`DELETED.failure_code` may be null or any value allowed by the safe format
check so the terminal row can retain a safe mismatch/delete audit outcome.

`ck_object_files_timestamp_order`:

```sql
updated_at >= created_at
AND (available_at IS NULL OR available_at >= created_at)
AND (terminal_at IS NULL OR terminal_at >= created_at)
AND (
  terminal_at IS NULL
  OR available_at IS NULL
  OR terminal_at >= available_at
)
AND (
  deleted_at IS NULL
  OR (
    terminal_at IS NOT NULL
    AND deleted_at >= terminal_at
  )
)
```

### Forbidden schema surface

`object_files` has no column for original filename, submitted/claimed MIME,
source or sanitized bytes, EXIF/GPS/XMP/ICC/comment/thumbnail, public or
presigned URL, provider request/response/error body, provider ETag, endpoint,
access/secret key, public ACL, arbitrary JSON, `owner_type`, `owner_id`,
domain-parent attachment, customer/owner/application/document PII, retention,
or scheduler/outbox state. It has no polymorphic relationship and no generic
media ownership semantics.

The ORM and migration must reproduce these columns, types, nullability,
constraint expressions, names, index order, and FK action without weakening.

## Upload And Failure Protocol

### Exact upload coordinator API

The only M8 upload entry point is the internal asynchronous coordinator:

```python
async def ingest_sanitized_image(
    session_factory: sessionmaker[Session],
    *,
    source: AsyncImageSource,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
    settings: Settings,
    storage: ObjectStorageService,
) -> IngestedImageResult
```

`IngestedImageResult` contains exactly:

```python
object_file_id: UUID
content_type: Literal["image/jpeg", "image/png", "image/webp"]
size_bytes: int
width_px: int
height_px: int
checksum_sha256: ObjectChecksumSha256
```

Its `repr` redacts the checksum. It has no bucket, key, URL, filename,
provider value, actor/IP, raw/sanitized bytes, ORM entity, or domain-parent
field.

Input and ownership are exact:

| Input | Meaning and ownership |
|---|---|
| `session_factory` | A caller-composed `sessionmaker[Session]`. The coordinator owns every session it opens and uses `session_factory.begin()` as the commit/rollback/close boundary. It never accepts or reuses a request `Session`. |
| `source` | A borrowed `AsyncImageSource` passed to `read_bounded_image`; the request/multipart adapter owns and closes it. The coordinator reads it but never logs, persists, returns, or closes it. |
| `actor_user_id` | The authenticated user UUID copied only to `created_by_user_id` for audit/accountability and used in the user limiter. It is not domain ownership or read authorization. |
| `client_ip` | A trusted `ResolvedClientIp` already produced by `resolve_client_ip`. It is used only as HMAC limiter input and is never persisted raw, logged, or returned. |
| `now` | A caller-injected timezone-aware timestamp used for rate-limit and lifecycle writes; the coordinator does not read the wall clock. |
| `settings` | A borrowed immutable settings snapshot. The coordinator obtains one complete `StorageConfig` from it and uses its exact bucket, image limits, and rate policy. It never mutates or closes settings. |
| `storage` | A caller-owned injected `ObjectStorageService`, constructed from the same config snapshot. The coordinator invokes the narrow protocol but never constructs, retries, or closes the provider/client. |

The coordinator generates one `object_file_id` UUID4 and one independent
UUID4-backed `ObjectKey` after successful sanitization and before TX-S1.
Neither value comes from a filename, form field, actor, IP, domain entity, or
provider response.

### Exact order and transaction ownership

The complete order is:

```text
1. Validate typed inputs and require the complete storage config (no DB/provider I/O).
2. Run the storage user/IP limiter in its own short DB-only transaction.
3. Read max+1 and sanitize/re-encode the image (no Session exists).
4. Generate object UUID/key in memory.
5. TX-S1: with session_factory.begin():
     create PENDING_UPLOAD with actor_user_id and sanitized metadata.
   Context exit commits; rollback/close are owned by the context.
6. The TX-S1 context is fully exited and its Session is closed.
7. External phase:
     PUT exactly once with exact sanitized bytes;
     HEAD the same bucket/key when verification/reconciliation requires it;
     compare exact size/content-type/checksum in memory.
   No SQLAlchemy Session or transaction exists in this phase.
8. TX-S2: with a fresh session_factory.begin():
     lock the same row and write the verified lifecycle result.
   Context exit commits and closes the fresh Session.
9. Only after the required TX-S2 commit succeeds, materialize and return
   IngestedImageResult from safe in-memory metadata.
```

The limiter phase is not TX-S1: it creates no object capability and completes
before source decoding. Sanitization and key generation never occur inside a
SQLAlchemy transaction. TX-S1 and TX-S2 are different Session instances.
Repository primitives receive only a Session and continue to own no commit,
full rollback, or close. No SQLAlchemy session or transaction is open during
PUT, HEAD, DELETE, presign, or an HTTP fetch.

### Exact failure outcomes

| Failure point/outcome | Required result |
|---|---|
| Invalid/missing config | Fail closed as `FILE_STORAGE_ERROR` before source read, object row, or provider call. |
| Rate-limit denial | Raise the closed upload outcome with public code `RATE_LIMITED` before decode, object row, or provider call; return no object. |
| Bounded read/sanitization | Propagate its existing safe typed file error; create no row and call no provider. |
| TX-S1 flush/commit | Context rollback/close completes; make zero PUT/HEAD/DELETE calls and return no object. |
| Definite PUT rejection/no-send | Do not retry or HEAD. In a fresh TX-S2, lock `PENDING_UPLOAD` and mark `FAILED/STORAGE_PROVIDER_UNAVAILABLE`; then raise the closed file error. |
| Ambiguous PUT | Never issue a second PUT. HEAD the exact same key outside a session: exact metadata proceeds to AVAILABLE; missing records `UPLOAD_OUTCOME_UNKNOWN` while remaining pending; mismatch follows `DELETE_PENDING` and bounded delete; a HEAD failure remains pending/unknown. |
| PUT success, HEAD exact | Fresh TX-S2 locks the row and marks `AVAILABLE/available_at`; return only after commit. |
| PUT success, immediate HEAD missing | Never repeat PUT. Fresh TX-S2 locks the same row, keeps `PENDING_UPLOAD`, records `UPLOAD_OUTCOME_UNKNOWN` with injected `updated_at`, and returns no object for stale reconciliation. |
| PUT success, HEAD mismatch | Fresh result TX marks `DELETE_PENDING/OBJECT_METADATA_MISMATCH`; delete outside a session, then a fresh TX marks `DELETED`; return no object. |
| PUT success, HEAD provider failure | Never repeat PUT. Fresh TX records `UPLOAD_OUTCOME_UNKNOWN` while the row remains `PENDING_UPLOAD`; return no object for stale reconciliation. |
| Any required TX-S2 flush/commit | Raise the closed file error and return no object. Never repeat PUT. The last committed lifecycle state remains visible for bounded reconciliation. |
| Delete ambiguity while cleaning mismatch | Leave committed `DELETE_PENDING/DELETE_OUTCOME_UNKNOWN`; return no object and let internal delete reconciliation continue. |

Provider exceptions are classified before lifecycle handling and are never
chained, persisted, logged, or returned. Failure handling never generates a
new object UUID/key/row, presigns, or reports hidden success.

`ingest_sanitized_image` is an internal transport capability only. It does not
mount a route, parse a domain attachment, create a customer/document/owner
record, authorize a read, or persist polymorphic ownership. A future
domain-specific caller must attach the returned object UUID in its own
separate approved transaction and authorization model.

## Reconciliation And Delete Contract

`reconcile_stale_object_uploads(now, batch_size)`:

- accepts batch `1..5000`;
- selects stale `PENDING_UPLOAD` in deterministic order;
- uses `FOR UPDATE SKIP LOCKED` in a short claim transaction;
- advances `updated_at` as the claim marker and closes the session before HEAD;
- exact HEAD -> fresh TX `AVAILABLE`;
- stale missing -> fresh TX `FAILED/OBJECT_MISSING_AFTER_UPLOAD`;
- mismatch -> fresh TX `DELETE_PENDING`, external delete, fresh TX `DELETED`;
- never PUTs, schedules itself, or exposes a route.

Internal delete:

```text
AVAILABLE -> DELETE_PENDING -> external DELETE/HEAD -> DELETED
```

Provider missing is delete success. Timeout stays `DELETE_PENDING` with a safe
code for later bounded reconciliation. Delete and reconcile are idempotent and
concurrent-safe. M8 adds no public delete route and no automatic retention.

The main internal CLI exposes only:

```text
storage preflight | health
storage reconcile --batch-size 1..5000
storage delete --object-id <UUID>   # development/local/testing only
storage smoke --actor-id <UUID>     # development/local/testing only
```

Preflight/health requires the complete app-credential configuration and checks
only data-plane access to the configured bucket through the narrow provider
contract. It never creates the bucket, inspects or changes ACL/policy or
PublicAccessBlock state, or claims to prove privacy/anonymous denial.
Reconcile runs both bounded stale-upload and stale-delete coordinators. Delete
delegates to the internal lifecycle service and fails before dependency
construction in production. Output contains fixed status, counts, and
allowlisted safe codes only; it never prints object UUID/key, bucket, endpoint,
credential, provider response, or URL. There is no upload command for user
files, manual SQL, public route, scheduler, or automatic AVAILABLE purge.

Smoke accepts no file or filename. It generates a synthetic in-memory PNG,
runs sanitizer/upload/HEAD, authorizes a configured-TTL presigned GET, fetches
the URL inside the command without printing it, verifies content type/size and
SHA-256, performs internal delete, and proves the object is missing. Success
prints only `STORAGE_SMOKE_PASS checks=8`; failures use one fixed safe status
and attempt best-effort lifecycle cleanup.

Web startup and `/health` never construct a storage client and remain green
with missing or unreachable storage configuration. M6 Telegram worker and M7
OTP dispatcher compose no storage settings, client, or MinIO dependency.
Storage CLI commands own and close the S3 client callback and dispose their DB
engine in `finally`; the adapter remains single-attempt and no command adds a
tight retry loop.

## Adapter Contract

`ObjectStorageService` provides only:

```text
put_object(...)
head_object(...)
delete_object(...)
create_presigned_get_url(...)
check_bucket_access(...)
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

## S3 Adapter Exact API And Failure Classification

This appendix is the executable contract for M8.38–M8.42. Its SDK authority is
the frozen lock: `boto3==1.43.59` and `botocore==1.43.59`. Later adapter code
must not widen this API, exception surface, or retry policy without first
updating this contract.

### Client factory

The factory accepts one complete `StorageConfig`. It constructs exactly one
S3 client and performs no SDK operation:

```python
Config(
    signature_version="s3v4",
    connect_timeout=3,
    read_timeout=10,
    max_pool_connections=10,
    retries={"total_max_attempts": 1, "mode": "standard"},
    s3={"addressing_style": config.addressing_style},
    user_agent_extra="nasiya-m8-storage/1",
)

boto3.client(
    "s3",
    endpoint_url=<explicit endpoint>,
    region_name=config.region,
    aws_access_key_id=<explicit access key>,
    aws_secret_access_key=<explicit secret key>,
    use_ssl=config.use_ssl,
    config=<Config above>,
)
```

`total_max_attempts=1` includes the initial request and therefore permits zero
SDK retries. The adapter never selects adaptive/legacy retry mode. Credentials
and endpoint are revealed from redacted wrappers only in the immediate
`boto3.client` call. There is no session profile, environment/default
credential fallback, metadata lookup, assume-role path, proxy credential
provider, custom endpoint discovery, or network call at import/factory time.
The fixed user agent has no user, shop, object, host, environment, or request
identifier.

Production construction injects this client into one adapter instance. Tests
inject a botocore `Stubber` client or a narrow fake. Adapter methods do not
construct clients and no global client/fake state is allowed.

### Exact protocol and SDK calls

The implemented adapter must conform to the existing
`ObjectStorageService` protocol without extra generic methods:

```text
put_object(*, bucket: BucketName, key: ObjectKey,
           image: SanitizedImage) -> StorageProviderOperationResult
head_object(*, bucket: BucketName,
            key: ObjectKey) -> StoredObjectHead | None
delete_object(*, bucket: BucketName,
              key: ObjectKey) -> StorageProviderOperationResult
create_presigned_get_url(*, bucket: BucketName, key: ObjectKey,
                         ttl_seconds: int) -> PresignedObjectUrl
check_bucket_access(*, bucket: BucketName)
    -> StorageProviderOperationResult
```

`put_object` makes one `PutObject` call:

```text
Bucket=<validated internal bucket>
Key=<generated internal key>
Body=<exact sanitized bytes>
ContentLength=<exact sanitized size>
ContentType=image/jpeg | image/png | image/webp
Metadata={"checksum-sha256": <lowercase 64-hex digest>}
```

It passes no `ACL`, grant, public URL, filename/content-disposition, tag,
redirect, arbitrary metadata, multipart, or provider checksum derived from
ETag. Payload size is already bounded to `1..10_485_760`.

`head_object` makes one `HeadObject(Bucket=..., Key=...)`. HTTP `404` is the
only missing result and returns `None`; `403` is never collapsed to missing.
A present response must contain a positive integer `ContentLength`, an allowed
`ContentType`, and exactly one lowercase
`Metadata["checksum-sha256"]`. It returns only those values as
`StoredObjectHead`. Missing/malformed metadata is a sanitized provider failure.
The adapter ignores `ETag` and all other response fields. Exact
size/content-type/checksum comparison, including the typed mismatch outcome,
belongs to the coordinator and never mutates this result.

`delete_object` makes one `DeleteObject(Bucket=..., Key=...)`. S3 delete of an
already missing object is `SUCCESS`; no preliminary HEAD is required. An
ambiguous delete remains `DELETE_PENDING` for later HEAD/delete reconciliation.

`create_presigned_get_url` calls only:

```text
generate_presigned_url(
    ClientMethod="get_object",
    Params={"Bucket": ..., "Key": ...},
    ExpiresIn=<validated 60..900 seconds>,
    HttpMethod="GET",
)
```

Presigning makes no network call. There is no presigned PUT, POST, list, public
route, ACL, or URL persistence/logging path. The returned URL immediately
enters `PresignedObjectUrl`.

`check_bucket_access` makes exactly one
`HeadBucket(Bucket=<configured validated bucket>)` call with the application
identity. Success proves only that the configured bucket is reachable through
the app's bucket-scoped data-plane permission. Every `403`, `404`, or provider
failure is a sanitized definite failure. It never creates a bucket or invokes
`GetBucketAcl`, `GetBucketPolicy`,
`PutPublicAccessBlock`, `GetPublicAccessBlock`, or another administrative API.

The superseded M8.42 create/private-verification sequence is not part of the
runtime protocol or boto3 adapter. Create-if-missing, private anonymous policy,
and bucket-scoped application identity/policy provisioning remain in
`deploy/minio-init.sh`. Privacy/anonymous-deny verification remains in that
admin-plane script, designated real-MinIO acceptance tests, and the CI
provisioning check.

### Exact exception classification

Only class identity, operation name, safe HTTP status, and safe provider error
code participate in classification. Exception `str`/`repr`, response body,
headers, request ID, host, endpoint, bucket, key, credentials, and SDK context
are never logged, persisted, returned, or chained into a public error.

The pinned botocore classes used by the adapter are:

```text
ClientError
ParamValidationError
NoCredentialsError
PartialCredentialsError
EndpointConnectionError
ConnectTimeoutError
ProxyConnectionError
SSLError
ReadTimeoutError
ConnectionClosedError
HTTPClientError
BotoCoreError
```

Catch order is most-specific first: `ClientError`; read/closed HTTP outcomes;
pre-connect/configuration outcomes; remaining `HTTPClientError`; remaining
`BotoCoreError`. This matters because read/closed errors inherit
`HTTPClientError`, while validation/credential/connection errors inherit
`BotoCoreError`. `ClientError` is a separate `Exception` branch.

Classification is exact:

| Outcome | PUT | DELETE | object HEAD / bucket access check | Presign |
|---|---|---|---|---|
| `ParamValidationError`, `NoCredentialsError`, `PartialCredentialsError` | definite configuration failure | definite configuration failure | definite configuration failure | definite configuration failure |
| `EndpointConnectionError`, `ConnectTimeoutError`, `ProxyConnectionError`, `SSLError` | definite no-response failure | definite no-response failure | definite provider failure | not expected; definite if raised |
| `ReadTimeoutError`, `ConnectionClosedError`, remaining `HTTPClientError` | ambiguous | ambiguous | definite provider failure | not expected; definite if raised |
| `ClientError` HTTP `408`, `429`, or `5xx` | ambiguous | ambiguous | definite provider failure | not expected; definite if raised |
| other `ClientError` | definite rejection | definite rejection | object HEAD `404` is missing; bucket access `404` and every other status are definite provider failure | definite provider failure |
| remaining `BotoCoreError` | ambiguous, conservatively | ambiguous, conservatively | definite provider failure | definite provider failure |

All mapped operation failures use `StorageProviderError` with
`STORAGE_PROVIDER_UNAVAILABLE`; only the `kind` is `DEFINITE` or `AMBIGUOUS`.
Factory/configuration failures use
`STORAGE_CONFIGURATION_UNAVAILABLE/DEFINITE`. A successful or ambiguous PUT
triggers HEAD of the same row/key and never a second PUT. Immediate missing
remains `PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN` for stale reconciliation. No
adapter method performs an automatic retry or recursively calls itself.

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
bucket, removes and verifies anonymous access, creates/updates a bucket-scoped
application user, and attaches only required bucket/data-plane operations. Web
and storage CLI receive app credentials only when configured; their preflight
does not prove privacy. Database migration, web, M6 worker, and M7 dispatcher
do not depend on MinIO health, and root credentials are never supplied to
those application roles.

CI keeps the single `dependency-sync` job and PostgreSQL service. It adds a
bounded MinIO runtime and private-policy integration with test-only or
runtime-generated credentials masked before use. Generated root credentials
remain in a mode-`0600` runner-temp env file passed only to MinIO/init; app
integration steps inherit only the bucket-scoped identity. CI uses no real
cloud credential or network.

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

The operations/privacy checkpoint additionally proves:

- development/testing smoke runs eight synthetic ingest, authorized GET,
  checksum, delete, and post-delete checks and emits only a fixed safe result;
- missing or unreachable storage does not affect web health, login, M6 worker,
  or M7 dispatcher startup paths;
- the local backup/restore exercise uses temporary private synthetic buckets,
  verifies count/checksum/content/privacy, cleans them, and preserves the
  configured bucket and named volume;
- actual-byte/multipart adversarial handling rejects length, field, file,
  header, filename, CSRF, disconnect, and overflow abuse without a production
  upload route;
- the generated image corpus closes malformed, truncated, declared-dimension,
  trailing/polyglot, metadata, mode, animation, output-limit, and decoder
  exception cases without external downloads;
- static and runtime leakage guards cover bytes, metadata, storage identity,
  credentials, URL/provider detail, actor/IP/session identity, CLI, CI,
  Compose, runbook, and failure rendering;
- ORM metadata adds exactly `object_files`; runtime routes/templates add no
  file surface; M5–M7 containment, `app.main`, and TT remain unchanged;
- real MinIO stores and returns only the exact sanitized, metadata-free image
  while anonymous access remains denied.

Final M8 closure additionally requires a no-cache built-image codec/runtime
check, real local MinIO acceptance `16/16`, local backup/restore exercise,
eight exact checkpoint commits, exact pushed-SHA remote CI success, a
docs-only closeout, and clean synced `main`.
