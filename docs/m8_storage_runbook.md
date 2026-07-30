# M8 Storage Operations Runbook

## Scope And Safety

This runbook covers only the M8 private S3-compatible foundation: local/CI
MinIO provisioning, preflight, synthetic smoke, stale reconciliation, internal
delete, secret rotation, and a local backup/restore exercise.

It does not define a customer-document workflow, public file route, generic
upload UI, retention policy, production provider, or production recovery SLA.
Never print or paste an endpoint, bucket, object key, credential, presigned
URL, provider response, or private fixture into tickets, terminals captured by
CI, logs, or reports.

## Private Provisioning And Data-Plane Preflight

1. Supply MinIO root credentials only to `minio` and `minio-init`.
2. Supply the bucket-scoped app identity only to the web/storage CLI edge.
3. Start `minio`; wait for its bounded healthcheck.
4. Run `minio-init` twice. Both runs must succeed. It creates or verifies the
   bucket, forces and verifies private anonymous policy, and creates/updates
   only the bucket-scoped app identity and policy.
5. Run `python -m app.cli storage preflight`. Expected output is only
   `STORAGE_PREFLIGHT_OK`. This command uses the app identity and verifies only
   complete configuration plus data-plane access to the configured bucket.
6. In development/testing, run the synthetic acceptance command with an
   existing development user UUID:

   ```text
   python -m app.cli storage smoke --actor-id <existing-dev-user-uuid>
   ```

   Expected output is only `STORAGE_SMOKE_PASS checks=8`.

Do not grant public policy, ACL, wildcard admin actions, bucket creation, or
policy inspection to the app identity. Anonymous `GET` and `HEAD` must remain
denied.

The runtime application adapter exposes only configured-bucket access check,
PUT, HEAD, DELETE, and presigned GET. It has no bucket creation, ACL/policy,
PublicAccessBlock, identity, or other provisioning/admin methods.

Preflight does not create a bucket, inspect/modify ACL or policy, call
PublicAccessBlock APIs, or prove privacy/anonymous denial. That proof belongs
to `minio-init`, designated real-MinIO acceptance tests, and CI provisioning
checks. Never supply root credentials to web, storage CLI, M6 worker, or M7
dispatcher.

## Secret Rotation

Use a secure environment/secret store; never put secret values in command
arguments, source files, shell tracing, screenshots, or runbook evidence.

App identity rotation:

1. Generate a new app access-key/secret pair in the secure store.
2. Run the idempotent init procedure with the new identity and the same
   bucket-scoped policy.
3. Switch the web/storage CLI configuration to the new identity.
4. Run storage preflight and synthetic smoke.
5. Disable and remove the old app identity only after both checks pass.

Root rotation:

1. Schedule a local maintenance window and preserve the named MinIO volume.
2. Change the root credentials in the secure environment.
3. Restart only MinIO/init as required; rerun idempotent provisioning.
4. Verify private policy through init/designated acceptance, then run the
   separate app-identity data-plane preflight and smoke.

Credential errors must be recorded only as the fixed configuration/provider
code. Never copy the provider error body.

## Upload, Reconcile, And Delete Troubleshooting

| Safe observation | Action |
|---|---|
| `STORAGE_CONFIGURATION_UNAVAILABLE` | Verify the complete typed storage bundle at the composition edge; do not inspect it through a dump or log. |
| `STORAGE_PROVIDER_UNAVAILABLE` | Check bounded MinIO health and admin-plane provisioning, then run one app-credential data-plane preflight. Do not treat it as privacy proof or add a retry loop. |
| `UPLOAD_OUTCOME_UNKNOWN` | A successful or ambiguous PUT whose immediate HEAD is missing or fails stays on the same `PENDING_UPLOAD` row/key. Run `python -m app.cli storage reconcile --batch-size <1..5000>` only after the stale threshold. Never repeat PUT or generate a new key. |
| `OBJECT_MISSING_AFTER_UPLOAD` | Only a later stale-reconciliation missing HEAD terminalizes the pending row this way. Treat it as failed and investigate provider durability using safe counts only. |
| `OBJECT_METADATA_MISMATCH` | Let the lifecycle move through `DELETE_PENDING`; do not presign it. |
| `DELETE_OUTCOME_UNKNOWN` | Run bounded delete reconciliation. Do not automatically purge unrelated `AVAILABLE` rows. |
| `FILE_ACCESS_DENIED` | Verify the injected domain-parent authorization. Creator audit identity alone is not access proof. |

The exact reconciliation command is
`python -m app.cli storage reconcile --batch-size <1..5000>`. Development-only
explicit deletion is
`python -m app.cli storage delete --object-id <object-uuid>`; it is not a
public route or a retention scheduler.

All DB phases must close before PUT, HEAD, DELETE, presign, or HTTP fetch.
Reconcile output is counts and allowlisted codes only. The web health endpoint,
Telegram worker, and OTP dispatcher remain independent of storage health.

Before production rollout, the selected S3-compatible provider must pass
acceptance proving strong read-after-write visibility of the object and its
checksum metadata through HEAD. This is a provider compatibility gate, not an
authorization to classify an immediate missing HEAD as terminal. Runtime still
keeps that row `PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN` until bounded stale
reconciliation; do not add a sleep loop or repeat PUT.

## Degraded Mode And Safe Operator Output

If MinIO is unavailable, web startup, `/health`, password authentication, the
M6 Telegram worker, and the M7 OTP dispatcher remain operational and
storage-independent. Storage preflight fails with only
`STORAGE_PROVIDER_UNAVAILABLE`; a storage operation fails closed as
`FILE_STORAGE_ERROR`. There is no local-disk fallback, tight restart loop,
sleep-based probe, or automatic PUT retry.

Recover by restoring provider health without removing the named volume,
rerunning idempotent `minio-init`, then running one app-credential preflight
and the synthetic smoke. Do not treat general web health as storage readiness.

Operator and CI evidence is limited to fixed status strings, safe counts, and
allowlisted lifecycle codes. Never enable shell tracing or print storage
configuration, provider exceptions, object metadata, or database rows. Local
Compose also keeps PostgreSQL error verbosity terse so a rejected row is not
rendered in server-log `DETAIL` output.

## Local Backup/Restore Exercise

**Never run `docker compose down -v` during normal operation or this drill.**
The named MinIO volume is persistent state; removing it is destructive.

The exercise script uses the pinned `mc` image and root credentials supplied
through environment variables. It never includes credential values in Docker
arguments. Inject the required `MINIO_ENDPOINT`, `MINIO_ROOT_USER`,
`MINIO_ROOT_PASSWORD`, and `MINIO_BUCKET` through the local secure
environment, select the correct Docker network, then run:

```text
./deploy/minio-backup-restore-exercise.sh
```

The exercise deliberately avoids mirroring real application objects:

1. Verify the configured bucket exists and has private anonymous policy.
2. Create a unique temporary private source bucket.
3. Store one generated synthetic PNG with SHA-256 metadata.
4. `mc mirror` that bucket to a `mktemp` directory under the workspace.
5. Keep the checksum as the local metadata manifest.
6. Create a unique temporary private restore bucket.
7. Restore with `mc mirror`, applying the manifest metadata.
8. Compare source/backup/restored object counts.
9. Verify restored checksum metadata, content type, and byte-for-byte content.
10. Verify the restore bucket remains private.
11. Remove both temporary buckets and the temporary backup directory through
    the exit trap. The configured bucket and named volume remain untouched.

The only success output is:

```text
STORAGE_BACKUP_RESTORE_PASS source=1 backup=1 restored=1 checksum=VERIFIED privacy=PRIVATE
```

Failures emit only `STORAGE_BACKUP_RESTORE_FAILED code=<safe-stage>`.

## Sanitized Local Evidence

| Date | Exercise | Safe evidence | Result |
|---|---|---|---|
| 2026-07-30 | Shell syntax | no shell syntax error | GREEN |
| 2026-07-30 | Private configured bucket check | `privacy=PRIVATE` | GREEN |
| 2026-07-30 | Temporary source → local mirror → temporary restore | `source=1 backup=1 restored=1` | GREEN |
| 2026-07-30 | Metadata and content verification | `checksum=VERIFIED` | GREEN |
| 2026-07-30 | Cleanup | temporary buckets/directory removed; named volume preserved | GREEN |

## Deferred Production Recovery

This local exercise is not a production backup design. Production provider
selection, versioning/immutability, encryption/KMS, replication, retention,
monitoring, ownership, RPO, RTO, and a real provider restore drill remain
explicitly deferred. They must be approved and tested against the selected
provider before production claims are made, including the read-after-write
compatibility gate above.
