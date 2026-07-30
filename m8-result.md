# M8 Result

Status: `M8 REMOTE GREEN — CLOSED`

Date: 2026-07-30

## Milestone

M8 — Secure Object Storage Foundation

## Exact Implementation And Remote Evidence

| Evidence | Exact result |
|---|---|
| Final implementation / recovery SHA | `af611b0d546479d1f21075d9b37fac748a71fc1e` |
| Eighth implementation checkpoint SHA | `3481be0491f87a2ad64d1a65d6d41eedbb00a8a3` |
| Recovery subject | `fix: isolate MinIO backup credentials in CI` |
| GitHub Actions run | `30565830042` |
| Workflow / job | `CI` / `dependency-sync` |
| Conclusion | `success` |
| Full pytest | `2167 passed` |
| Outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Alembic head | `f8a9b0c1d2e3` |
| M8-added tables | exactly one: `object_files` |
| Pillow | `12.3.0` |
| boto3 | `1.43.59` |
| botocore | `1.43.59` |
| Real local MinIO acceptance | `16/16` |
| Remote MinIO integration | `16 passed` |
| Backup/restore | GREEN; source/backup/restored `1/1/1` |
| Implementation baseline sync | `HEAD == origin/main`, divergence `0 0`, clean worktree |

The eighth checkpoint is an ancestor of the final implementation SHA. The
later recovery commit is a separately audited fix; it does not replace,
rewrite, or relabel any of the eight implementation checkpoints.

The checkpoint CI exposed that the full-suite backup exercise no longer had
admin authority after the intentionally short-lived root material had been
removed. The recovery commit moved the real exercise into a dedicated
admin-plane step, published only sanitized evidence, and kept application and
full-pytest steps root-free.

## Delivered Boundary And Recovery Corrections

- Recovery Fix 1 separates provisioning/admin authority from the application
  data plane. Provisioning and identity administration stay in the init
  operation; the dedicated backup drill is separately isolated in the admin
  plane. Web and storage CLI use only the scoped app identity for configured
  storage access checks, PUT, HEAD, DELETE, and presigned GET.
- Recovery Fix 2 keeps a successful or ambiguous PUT followed by an immediate
  missing HEAD on the same `PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN` row. It
  returns no premature success, performs no blind second PUT, and delegates
  the exact/missing/mismatch decision to bounded stale reconciliation.
- There is no public file route or domain consumer, no public bucket or
  presigned PUT, and no generic file vault.
- Web startup, the M6 worker, and the M7 dispatcher remain independent of
  storage configuration and runtime availability.
- Local and remote containment, privacy, secret/key/URL, metadata, payload,
  and provider-detail leakage checks are GREEN.

## Eight Implementation Checkpoints

1. `a4933c467cd26397c71c9e72022fd8d8695281fc` —
   `M8: freeze secure object storage scope`
2. `9144f086bb1ab5361125669e375051638958ab25` —
   `M8: add storage settings and upload boundaries`
3. `104adf3f13af357289b2418a43caf3e16d73c784` —
   `M8: add secure image sanitization`
4. `c12f0ec3760bf0fc663a6df746d6b41d5e3c6e6c` —
   `M8: add object file persistence`
5. `cf3ddcc12bc344b40d0d8fc907fb80fb13c770a6` —
   `M8: add private S3-compatible storage`
6. `ee0a4aad4a0474f884c0dd7e6c4690d1b65bbb1d` —
   `M8: add resilient object storage workflow`
7. `4c602e268d7606ee22b37ce2c1410f15488c626b` —
   `M8: harden storage operations and privacy`
8. `3481be0491f87a2ad64d1a65d6d41eedbb00a8a3` —
   `M8: complete secure object storage foundation`

The recovery subject `fix: isolate MinIO backup credentials in CI` is a
separate descendant of these checkpoints and was validated independently by
the exact-SHA remote run.

## Closure And Deferred Work

M8 is closed from its exact pushed implementation and successful remote CI
evidence. This closure does not select a production provider, establish legal
retention, or claim production RPO/RTO. Those decisions and provider-specific
acceptance remain deferred.

M9 has not started.

`M8 REMOTE GREEN — CLOSED`
