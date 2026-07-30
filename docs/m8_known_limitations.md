# Nasiya M8 Known Limitations

Status: approved known limitations for the M8 implementation milestone.
Source authority: `/home/yalgashev/projects/m8_00_final_scope_freeze.md` and
M8.04 feasibility audit.

## KL-M8-01 — Foundation Has No Domain Route Or Consumer

M8 exposes internal protocols, services, and CLI acceptance only. It does not
create a production upload/download/delete route and does not attach an object
to a customer, owner application, shop news item, or other domain parent.

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
- Production rollout requires a separate provider and operations decision.

## KL-M8-04 — No Automatic PUT Retry

A timeout after PUT can mean the provider accepted the object even though the
client did not receive a response. M8 never blindly retries the PUT.

Impact:

- The coordinator performs HEAD against the same key and checksum.
- An exact object becomes `AVAILABLE`; a missing object remains pending for
  reconciliation; a mismatch is deleted through `DELETE_PENDING`.
- Provider or TX-S2 failures can leave a safe stale row until an operator runs
  bounded reconciliation.

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

## Closure Evidence

Remote implementation CI, real MinIO `16/16`, exact dependency versions,
Alembic M8 head, full-suite count, and checkpoint SHAs are intentionally not
claimed at scope-freeze time. They are added only by M8.73–M8.77 after the
corresponding local and remote gates succeed.
