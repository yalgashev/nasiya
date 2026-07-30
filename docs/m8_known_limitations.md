# Nasiya M8 Known Limitations

Status: approved known limitations for the M8 implementation milestone.
Source authority: `/home/yalgashev/projects/m8_00_final_scope_freeze.md`,
M8.04 feasibility audit, and the Recovery Fix 1–2 post-freeze Product Owner
corrections.

## KL-M8-01 — Foundation Has No Domain Route Or Consumer

M8 exposes internal protocols, services, and CLI acceptance only. It does not
create a production upload/download/delete route and does not attach an object
to `customer_document`, an owner application, a shop news attachment, or any
other domain parent.

Impact:

- M8 cannot be used as a generic user file vault or media library.
- A later milestone must supply a concrete domain-parent authorizer and route
  adapter.
- `created_by_user_id` is accountability only and never grants read access.

## KL-M8-02 — Color Profiles Are Removed

The sanitizer intentionally strips ICC profiles together with EXIF, GPS, XMP,
comments, thumbnails, and other source metadata.

Impact:

- Images authored in non-sRGB or device-specific color spaces can have a
  visible color shift after fresh pixel conversion and re-encode.
- M8 does not preserve or transform arbitrary source color profiles.
- Privacy and deterministic metadata removal take precedence in this
  foundation.

## KL-M8-03 — Production Provider Is Deferred

The runtime contract is generic S3-compatible. MinIO is used only for local and
CI acceptance.

Impact:

- Production vendor, endpoint, credentials, availability objectives, lifecycle
  features, multi-region behavior, and provider-specific recovery are not
  selected by M8.
- No real cloud credential or network belongs in automated tests or CI.
- Production rollout requires a separate provider and operations decision plus
  acceptance proving strong object-and-metadata read-after-write visibility
  through HEAD.

## KL-M8-04 — Immediate Missing Is Reconciled Without PUT Retry

A timeout after PUT can mean the provider accepted the object even though the
client did not receive a response. A successful PUT can also be followed by a
briefly missing HEAD on an incompatible or degraded S3-compatible provider.
M8 treats neither immediate missing result as definitive absence and never
blindly retries the PUT.

Impact:

- The coordinator performs one HEAD for the same bucket/key and compares the
  returned size, content type, and checksum metadata with the pending row.
- An immediate missing object remains
  `PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN`; no object result is returned.
- Bounded stale reconciliation HEADs that same key: exact becomes
  `AVAILABLE`, stale missing may become
  `FAILED/OBJECT_MISSING_AFTER_UPLOAD`, and mismatch is deleted through
  `DELETE_PENDING`.
- Provider or TX-S2 failures can leave a safe stale row until an operator runs
  bounded reconciliation.
- The rollout compatibility requirement reduces this window; it does not turn
  the immediate missing result into a terminal failure or authorize a sleep
  loop.

## KL-M8-05 — Legal Retention Is Deferred

M8 supports explicit internal delete and delete reconciliation but no
domain/legal retention period and no automatic `AVAILABLE` purge.

Impact:

- A later domain milestone must decide when an object may be deleted.
- Terminal-row metadata purge is not part of M8.
- Operators must not infer a retention policy from MinIO defaults or local
  backup exercises.

## KL-M8-06 — Hard Resource Limits Are Fixed

M8 accepts only JPEG, PNG, and WebP with:

```text
input/output <= 10_485_760 bytes
request envelope <= 11_010_048 bytes
pixels <= 40_000_000
each dimension <= 16_384
frames = 1
```

Impact:

- Larger images are rejected; there is no resize, quality fallback, or
  multipart S3 upload.
- Animated images and unsupported formats are rejected rather than converted.
- The fixed limits are abuse boundaries, not a general media-processing
  platform.

## KL-M8-07 — MinIO Backup Exercise Is Local

M8 documents and executes a local MinIO backup/restore exercise using temporary
private storage and sanitized evidence.

Impact:

- It proves the local workflow, object count, checksum metadata/content, and
  cleanup behavior.
- It does not establish production RPO/RTO or replace provider-native backup
  and recovery testing.
- The persistent MinIO volume must not be removed with `down -v` during normal
  operation or acceptance.

## KL-M8-08 — Storage Is Optional For General Runtime

Web startup, `/health`, password authentication, the M6 worker, and the M7 OTP
dispatcher do not require storage configuration or a running MinIO instance.

Impact:

- General health can be green while storage operations are unavailable.
- Storage-specific preflight is the authoritative readiness check for the
  capability.
- A missing/partial configuration or provider outage fails the storage
  operation closed as `FILE_STORAGE_ERROR`; there is no local-disk fallback.

## Validation State

Local evidence now includes the exact dependency resolution, the single M8
Alembic head/table, focused image and transaction matrices, migration/runtime
validation, real local MinIO `16/16`, and the local backup/restore exercise.
These results do not select a production provider or establish production
RPO/RTO.

M8.74 completed both required full local pytest runs at exactly `2167 passed`
with no failed, skipped, xfailed, or xpassed outcomes. Remote CI, a pushed
implementation SHA/run, and milestone closure are not claimed here.
