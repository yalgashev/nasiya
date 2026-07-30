# Nasiya M8 Final Report

Status: `M8 REMOTE GREEN — CLOSED`
Date: 2026-07-30

This report records the completed M8 implementation, focused local acceptance,
the separately audited CI recovery, exact-pushed-SHA remote CI success, and
formal milestone closure. It does not claim production-provider readiness or
production recovery objectives.

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
authority. Provisioning and identity administration remain confined to
`minio-init`; a dedicated backup/restore drill is separately isolated in the
admin plane. Web, storage CLI, application tests, the M6 worker, and the M7
dispatcher never receive root authority. The runtime adapter contains only
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

## Exact-SHA Remote CI Evidence

| Evidence | Exact result |
|---|---|
| Final implementation / recovery SHA | `af611b0d546479d1f21075d9b37fac748a71fc1e` |
| Eighth implementation checkpoint | `3481be0491f87a2ad64d1a65d6d41eedbb00a8a3` — `M8: complete secure object storage foundation` |
| Recovery subject | `fix: isolate MinIO backup credentials in CI` |
| GitHub Actions run | `30565830042` |
| Workflow / job | `CI` / `dependency-sync` |
| Conclusion | `success` |
| Full pytest | `2167 passed` |
| Outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Alembic / PostgreSQL | head `f8a9b0c1d2e3`; migration, head, and integration checks GREEN |
| Remote MinIO | private integration `16 passed` |
| Admin backup/restore | dedicated step GREEN; sanitized source/backup/restored `1/1/1` evidence |
| Containment / leakage | M5–M8 containment and leakage checks GREEN |
| Exact-SHA ancestry / sync | checkpoint remains an ancestor; implementation `HEAD == origin/main`, divergence `0 0`, clean main |

The final implementation SHA is the recovery commit. The preceding checkpoint
CI showed that full pytest no longer had admin authority after intentional
root-material cleanup. Recovery moved the real backup exercise into a
dedicated admin step and exposed only sanitized evidence to root-free
application and full-pytest steps. It neither replaced nor rewrote the eight
M8 implementation checkpoints.

The tracked workflow retains one `dependency-sync` job with PostgreSQL,
root/app authority separation, exact Alembic-head validation, Ruff, designated
private MinIO integration, M5–M8 containment coverage, full pytest, and
explicit rejection of skip/xfail/xpass outcomes. It uses no real cloud
credential. The successful run validated the exact pushed final implementation
SHA.

## Closure And Deferred Production Work

M8 is closed as `M8 REMOTE GREEN — CLOSED`. M9 has not started.

Closure does not select a production provider or establish provider-specific
rollout acceptance, legal retention, or production RPO/RTO. The local
backup/restore exercise and its remote CI execution remain synthetic
acceptance evidence, not a production recovery design.
