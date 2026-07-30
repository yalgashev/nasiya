# Nasiya M8 Final Report

Status: `M8 TECHNICAL GREEN — REMOTE CI PENDING`
Date: 2026-07-30

This report records the completed M8 implementation and focused local
acceptance evidence available through M8.74, including the completed full
local technical validation. It does not claim a pushed M8 implementation,
remote CI success, or milestone closure.

## Baseline And Authority

- M8 inherits the closed M7 baseline: `M7 REMOTE GREEN — CLOSED`.
- The inherited Alembic parent is `e7f8a9b0c1d2`.
- The inherited M7 full suite was `1666 passed` with no
  failed/skipped/xfailed/xpassed outcomes.
- M8 authority is `docs/m8_scope_contract.md`, `docs/m8_decisions.md`,
  `docs/m8_repository_map.md`, `docs/m8_known_limitations.md`, and the
  post-freeze Product Owner corrections recorded for Recovery Fix 1 and
  Recovery Fix 2.
- The Product Owner decision set remains `24/24 FINAL APPROVED`.

## One Delivered Capability

M8 is one Secure Object Storage Foundation capability. Backend-mediated JPEG,
PNG, and WebP images are bounded, fully decoded, sanitized, re-encoded from
fresh pixels in the same format family, and stored in one private
S3-compatible bucket. PostgreSQL records a safe object lifecycle; ambiguous
storage outcomes reconcile without blind PUT retry; authorized reads use a
presigned GET only after an injected domain-parent authorizer allows access.

M8 adds no production file route, generic vault, or customer/owner/shop-news
domain consumer.

## Dependencies, Migration, And Schema

| Item | Exact result |
|---|---|
| Pillow | `12.3.0` |
| boto3 | `1.43.59` |
| botocore | `1.43.59` |
| Alembic head | `f8a9b0c1d2e3` |
| M8-added tables | exactly one: `object_files` |

The M8 revision is linear over the inherited M7 head. No second M8 table,
PostgreSQL enum, sequence, trigger, function, view, or data migration is
introduced.

## Recovery Corrections

Recovery Fix 1 separates provisioning/admin from application data-plane
authority. Only `minio-init` uses root/admin credentials to create or verify
the private bucket and maintain the bucket-scoped app identity/policy. Web and
storage CLI receive only app credentials; their runtime adapter contains
configured-bucket access check, PUT, HEAD, DELETE, and presigned GET, with no
bucket provisioning or policy/ACL administration.

Recovery Fix 2 keeps a successful or ambiguous PUT followed by an immediate
missing/failed HEAD on the same
`PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN` row and key. It returns no object
success, performs no second PUT, and lets bounded stale reconciliation decide
the exact available, missing, or metadata-mismatch outcome.

## Focused Local Evidence

The focused sets overlap and are not added together as a full-suite total.

| Evidence | Result |
|---|---|
| M8.69 final image matrix | `135 passed` |
| M8.70 transaction/reconcile/delete/authorization matrix | `104 passed` |
| M8.71 Alembic walk and migration/runtime validation | GREEN |
| M8.71 no-cache application-image builds and codec/import checks | GREEN |
| M8.72 real local MinIO manual acceptance | `16/16` |
| Local synthetic backup/restore exercise | GREEN |
| Web startup and M6/M7 storage/process isolation | GREEN |
| Secret, key, URL, metadata, payload, provider-detail leakage audit | GREEN |
| M8.74 full local pytest | `2167 passed` in each of two runs |
| M8.74 no-skip matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| M8.74 Alembic graph, empty/head, and M7/M8 walk | GREEN |
| M8.74 four-image no-cache rebuild and runtime codec/import probe | GREEN |
| M8.74 real MinIO checklist and backup/restore rerun | `16/16`, GREEN |

The real MinIO result proves the local private provisioning and application
data-plane lifecycle only. The backup/restore result is a local synthetic
exercise, not production recovery evidence.

The two full runs were the normal quiet suite and the required
`--durations=10` repeat. Each completed with the same exact total of
`2167 passed`, exceeding the inherited M7 baseline of `1666 passed`; neither
run reported a skipped, xfailed, or xpassed outcome. Focused evidence above
overlaps the full suite and is not added to that total.

## CI State

The tracked workflow retains one `dependency-sync` job with PostgreSQL,
runtime-generated or test-only masked MinIO credentials, root/app credential
separation, exact Alembic-head validation, Ruff, designated MinIO integration,
M5–M8 containment coverage, full pytest, and explicit rejection of
skip/xfail/xpass outcomes. It uses no real cloud credential.

This is a static/local workflow consistency result. Remote CI has not yet been
run for this local M8 implementation, so no M8 workflow run number, pushed
implementation SHA, or remote-green claim is recorded.

## Pending Gates

- Remote CI evidence is recorded only after an authorized push of the exact
  implementation and an actual successful workflow run.
- Production provider selection, provider-specific acceptance, legal
  retention, and production RPO/RTO remain deferred.

M8 is not closed. Current status remains:
`M8 TECHNICAL GREEN — REMOTE CI PENDING`.
