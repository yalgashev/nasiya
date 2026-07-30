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
| PO-M8-21 | Real local built-image MinIO smoke is mandatory. | Private bucket, PUT/HEAD/presign/deny/delete are proven. |
| PO-M8-22 | MinIO integration is mandatory in the existing single CI job. | Test credentials are generated or test-only and masked before use. |
| PO-M8-23 | Ambiguous PUT is reconciled with HEAD; never blindly resent. | Exact becomes available, missing stays pending then reconciles, mismatch is deleted. |
| PO-M8-24 | Storage runbook and local backup/restore exercise gate closure. | Production provider-specific recovery remains deferred. |

## Dependency Decision

The baseline already has `python-multipart` and has no Pillow, boto3,
botocore, MinIO SDK, or libmagic dependency.

Exactly two new direct runtime dependencies are approved:

1. `Pillow`;
2. `boto3`.

M8.07 selects compatible bounds and records the exact resolver output only
after checking Python 3.12 metadata, wheel availability, license, and
transitives. M8.08 then proves JPEG/PNG/WebP codecs and boto3 import/client
construction in the no-cache project image. No native OS package is
pre-approved.

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
| Storage health | operation/preflight only; not web `/health` |

## Repository Reconciliation Decisions

- Existing `Settings`/`SecretStr` conventions are extended rather than adding
  a storage-specific environment loader.
- Existing `AuthRateLimiter` and trusted-IP resolver are reused; no new rate
  table or raw IP/user key persistence.
- The ASGI guard is new because existing CSRF multipart handling calls
  `request.form()` and therefore cannot be the pre-parse byte boundary.
- The multipart helper explicitly parses/caches the form before validating its
  session-bound CSRF token.
- Request-owned transactions keep current `app/db.py` behavior. Non-request
  coordinators follow the M7 `session_factory.begin()` DB phase pattern.
- `updated_at` is the stale-row short-claim marker; no extra claim column or
  table is approved.
- One new `app/storage` package is permitted. Empty framework modules,
  registries, base repositories, generic DTO layers, or event infrastructure
  are not.
- M8 imports one model module into Alembic metadata and adds one revision
  whose exact parent is `e7f8a9b0c1d2`.
- MinIO root credentials stay in MinIO/init only. Web uses a bucket-scoped app
  identity. Worker and dispatcher receive neither.

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
