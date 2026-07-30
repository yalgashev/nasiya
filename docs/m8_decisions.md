# Nasiya M8 Decisions

Status: authoritative M8 repository decision log.
Source authority: `/home/yalgashev/projects/m8_00_final_scope_freeze.md`.
Product Owner disposition: `24/24 FINAL APPROVED`.

## Baseline

M8 starts from M7 docs-only closeout
`1afa4af92fc905fe48af41e03506053619945445`, with implementation baseline
`2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3`, successful CI runs
`30453909901` and `30454876820`, Alembic parent `e7f8a9b0c1d2`, and
`1666 passed` with no failed/skipped/xfailed/xpassed tests.

M8.01 confirmed clean synced `main`, divergence `0 0`, exact ancestry and CI,
one Alembic head, and an unchanged TT blob.

## Decision Table

| ID | Final decision | Implementation consequence |
|---|---|---|
| PO-M8-1 | M8 ends at storage abstraction, sanitizer, `object_files`, private MinIO, and service/CLI acceptance. | No domain consumer, generic vault, or production file UI. |
| PO-M8-2 | Upload is backend-mediated. | Raw browser upload never goes directly to storage. |
| PO-M8-3 | Production contract is generic S3-compatible; MinIO is local/CI only. | Production provider and SLA are deferred. |
| PO-M8-4 | Web starts without storage config; no local-disk fallback. | Only a storage operation fails as `FILE_STORAGE_ERROR`; `/health` is independent. |
| PO-M8-5 | Lifecycle is `PENDING_UPLOAD/AVAILABLE/FAILED/DELETE_PENDING/DELETED`. | Only `AVAILABLE` may be presigned. |
| PO-M8-6 | Order is sanitize -> TX-S1 -> external PUT/HEAD -> TX-S2. | No DB transaction/session remains open during network I/O. |
| PO-M8-7 | Key is `v1/objects/<uuid4-hex>.<canonical_ext>`. | Filename and PII never enter a key. |
| PO-M8-8 | One configured private bucket. | Versioned prefix; public policy is forbidden. |
| PO-M8-9 | Input and output maximum are `10_485_760` bytes. | No resize/fallback; limit+1 is `FILE_TOO_LARGE`. |
| PO-M8-10 | Maximum is `40_000_000` pixels and `16_384` per dimension. | Compressed-image memory bombs are rejected. |
| PO-M8-11 | Actual fully decoded format must be JPEG, PNG, or WebP. | Extension and claimed MIME are ignored. |
| PO-M8-12 | Re-encode in the same family from fresh pixels. | JPEG is RGB; PNG/WebP preserve alpha as RGB/RGBA. |
| PO-M8-13 | Every `n_frames != 1` image is rejected. | GIF, animated WebP, and APNG are unsupported. |
| PO-M8-14 | Apply orientation, then remove all metadata. | EXIF/GPS/XMP/ICC/comment/thumbnail are absent from stored bytes. |
| PO-M8-15 | Checksum is lowercase SHA-256 over exact sanitized bytes. | ETag is not the checksum. |
| PO-M8-16 | Presigned GET default TTL is `300s`; no presigned PUT. | Every refresh requires authorization again. |
| PO-M8-17 | Internal delete exists; no public delete or automatic `AVAILABLE` retention. | Domain/legal retention is deferred. |
| PO-M8-18 | Read access uses a domain-parent authorizer; no route in M8. | Creator UUID is accountability, not ownership; missing/denied are identical. |
| PO-M8-19 | Reuse HMAC limiter: user `5`, IP `20`, window `900s`; no byte table. | File/envelope caps are the byte-abuse boundary. |
| PO-M8-20 | Pre-parse ASGI guard and bounded multipart helper are mandatory; no CSRF exemption. | Future route cannot parse an automatic `UploadFile` before the guard. |
| PO-M8-21 | Real local built-image MinIO smoke is mandatory. | Init/designated acceptance proves private/anonymous deny; app acceptance proves PUT/HEAD/presign/delete. App preflight is not privacy proof. |
| PO-M8-22 | MinIO integration is mandatory in the existing single CI job. | Test credentials are generated or test-only and masked before use. |
| PO-M8-23 | Ambiguous PUT is reconciled with HEAD; never blindly resent. | Exact becomes available, missing stays pending then reconciles, mismatch is deleted. |
| PO-M8-24 | Storage runbook and local backup/restore exercise gate closure. | Production provider-specific recovery remains deferred. |

## Post-Freeze Product Owner Correction — Provisioning And Data Plane

Recovery Fix 1 supersedes the earlier M8.42 runtime
`ensure_private_bucket`/private-policy-preflight decision; the frozen source is
not edited. Provisioning/admin work belongs to `deploy/minio-init.sh` and
root/admin credentials: create the configured bucket if missing, enforce and
verify private anonymous access, and create/update the bucket-scoped app
identity and policy.

Web and storage CLI receive only that app identity. Their runtime
`ObjectStorageService` contains `check_bucket_access`, PUT, HEAD, DELETE, and
presigned GET only. The access check is one `HeadBucket` against the configured
bucket; no bucket creation, ACL/policy inspection, PublicAccessBlock call, or
other admin operation is permitted. Preflight proves complete configuration
and app data-plane access only and emits a fixed safe status. Privacy and
anonymous-deny proof is owned by `minio-init`, designated real-MinIO
acceptance, and CI provisioning. Root credentials are never supplied to web,
storage CLI, M6 worker, or M7 dispatcher.

## Post-Freeze Product Owner Correction — Immediate Missing HEAD

Recovery Fix 2 supersedes the earlier terminal interpretation of a successful
PUT followed by an immediate missing HEAD; the frozen source is not edited.
Successful PUT + immediate missing and ambiguous PUT + immediate missing use
one durable recovery surface. A fresh locked transaction keeps the same
row/key `PENDING_UPLOAD`, records `UPLOAD_OUTCOME_UNKNOWN`, advances
`updated_at` with the injected time, and returns the closed file error without
an object result.

There is no second PUT, new key/row, DELETE, presign, sleep, scheduler, or
outbox. Later bounded stale reconciliation uses the same row/key: exact HEAD
becomes `AVAILABLE`; stale missing becomes
`FAILED/OBJECT_MISSING_AFTER_UPLOAD`; mismatch moves through
`DELETE_PENDING`, bounded delete, and `DELETED`. Production rollout requires
provider acceptance proving object-and-metadata read-after-write visibility,
but immediate missing remains nonterminal even for a supported provider.

## Dependency Decision

The M8 start baseline already had `python-multipart` and had no Pillow, boto3,
botocore, MinIO SDK, or libmagic dependency.

M8.07 added exactly two direct runtime dependencies:

| Direct package | Bound | Resolved | License | Python/wheel evidence |
|---|---|---:|---|---|
| `Pillow` | `>=12.3.0,<13` | `12.3.0` | `MIT-CMU` | Python `>=3.10`; CPython 3.12 manylinux x86_64 wheel |
| `boto3` | `>=1.43.59,<2` | `1.43.59` | `Apache-2.0` | Python `>=3.10`; universal `py3-none-any` wheel |

Resolved boto3 transitives:

| Package | Resolved | License | Python requirement |
|---|---:|---|---|
| `botocore` | `1.43.59` | `Apache-2.0` | `>=3.10` |
| `jmespath` | `1.1.0` | `MIT` | `>=3.9` |
| `s3transfer` | `0.19.2` | `Apache License 2.0` | `>=3.10` |
| `python-dateutil` | `2.9.0.post0` | dual Apache/BSD distribution license | `>=2.7`, excluding Python `3.0..3.2` |
| `six` | `1.17.0` | `MIT` | Python 3.12 compatible |
| `urllib3` | `2.7.0` | `MIT` | `>=3.10` |

`uv sync --dev --frozen` checks `47` packages after this resolution. Import
smoke reports Pillow `12.3.0`, boto3 `1.43.59`, and botocore `1.43.59`.
M8.08 proved JPEG/PNG/WebP encode/decode and boto3 client construction in the
no-cache project image. No native OS package was required or added.

## Fixed Runtime Decisions

| Item | Decision |
|---|---|
| Image input maximum | `10_485_760` bytes |
| Multipart envelope maximum | `11_010_048` bytes |
| Pixel maximum | `40_000_000` |
| Dimension maximum | `16_384` |
| JPEG encoder | RGB, quality `90`, optimize, non-progressive |
| PNG encoder | RGB/RGBA, optimize, compression level `9` |
| WebP encoder | RGB/RGBA, lossless, method `6` |
| Key | `v1/objects/<uuid4-lowerhex>.<jpg|png|webp>` |
| Presigned GET TTL | default `300s`, allowed `60..900` |
| Upload rate limit | user `5`, trusted IP `20`, window `900s` |
| Stale threshold | default `60s` |
| Reconcile batch | `1..5000` |
| PUT retry | disabled; one PUT only |
| Immediate missing HEAD | same row/key remains `PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN`; stale reconciliation decides the final state |
| Storage health | app-credential configured-bucket data-plane preflight only; not privacy proof or web `/health` |

## Repository Reconciliation Decisions

- Existing `Settings`/`SecretStr` conventions are extended rather than adding
  a storage-specific environment loader.
- Existing `AuthRateLimiter` and trusted-IP resolver are reused; no new rate
  table or raw IP/user key persistence.
- The ASGI guard is new because existing CSRF multipart handling calls
  `request.form()` and therefore cannot be the pre-parse byte boundary.
- The multipart helper explicitly parses/caches the form before validating its
  session-bound CSRF token.
- Starlette `1.3.1` applies `max_part_size` to scalar form parts but does not
  enforce it on `UploadFile` data. The helper therefore also checks the parsed
  file size against `10_485_760`; the outer actual-byte ASGI guard bounds the
  parser before that check.
- Request-owned transactions keep current `app/db.py` behavior. Non-request
  coordinators follow the M7 `session_factory.begin()` DB phase pattern.
- `updated_at` is the stale-row short-claim marker; no extra claim column or
  table is approved.
- One new `app/storage` package is permitted. Empty framework modules,
  registries, base repositories, generic DTO layers, or event infrastructure
  are not.
- M8 imports one model module into Alembic metadata and adds one revision
  whose exact parent is `e7f8a9b0c1d2`.
- MinIO root credentials stay in MinIO/init only, including CI where they are
  isolated from application test steps. Web and storage CLI use a
  bucket-scoped app identity. Worker and dispatcher receive neither root nor
  storage credentials.

## Stable Error Decision

Public:

```text
FILE_ACCESS_DENIED
FILE_STORAGE_ERROR
FILE_TOO_LARGE
UNSUPPORTED_FILE_TYPE
```

Internal:

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

Provider exceptions, endpoint, bucket, object key, credentials, query
signature, original filename, raw bytes, and metadata never enter public
errors, logs, repr, reports, or database error fields.

## Stop Conditions

M8 stops before commit/push if any frozen baseline, TT, Alembic, one-table,
two-dependency, private-policy, transaction-order, no-retry, authorization, or
real PostgreSQL/MinIO requirement cannot be met without changing scope. It
also stops if implementation would require secret/key/URL/raw-image leakage,
a production domain route, direct presigned PUT, generic scheduler/outbox,
SQLite, `create_all`, skip/xfail, or assertion weakening.
