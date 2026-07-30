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

## Private Provisioning And Preflight

1. Supply MinIO root credentials only to `minio` and `minio-init`.
2. Supply the bucket-scoped app identity only to the web/storage CLI edge.
3. Start `minio`; wait for its bounded healthcheck.
4. Run `minio-init` twice. Both runs must succeed. It creates or verifies the
   bucket, forces private anonymous policy, and attaches only the bucket-scoped
   app policy.
5. Run `python -m app.cli storage preflight`. Expected output is only
   `STORAGE_PREFLIGHT_OK`.
6. In development/testing, run the synthetic acceptance command with an
   existing development user UUID:

   ```text
   python -m app.cli storage smoke --actor-id <existing-dev-user-uuid>
   ```

   Expected output is only `STORAGE_SMOKE_PASS checks=8`.

Do not grant public policy, ACL, wildcard admin actions, bucket creation, or
policy inspection to the app identity. Anonymous `GET` and `HEAD` must remain
denied.

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
4. Verify private policy, app-identity preflight, and smoke.

Credential errors must be recorded only as the fixed configuration/provider
code. Never copy the provider error body.

## Upload, Reconcile, And Delete Troubleshooting

| Safe observation | Action |
|---|---|
| `STORAGE_CONFIGURATION_UNAVAILABLE` | Verify the complete typed storage bundle at the composition edge; do not inspect it through a dump or log. |
| `STORAGE_PROVIDER_UNAVAILABLE` | Check bounded MinIO health and private provisioning, then run one preflight. Do not add a retry loop. |
| `UPLOAD_OUTCOME_UNKNOWN` | Run `storage reconcile --batch-size <1..5000>` after the stale threshold. Never repeat PUT or generate a new key. |
| `OBJECT_MISSING_AFTER_UPLOAD` | Treat the row as failed; investigate provider durability using safe counts only. |
| `OBJECT_METADATA_MISMATCH` | Let the lifecycle move through `DELETE_PENDING`; do not presign it. |
| `DELETE_OUTCOME_UNKNOWN` | Run bounded delete reconciliation. Do not automatically purge unrelated `AVAILABLE` rows. |
| `FILE_ACCESS_DENIED` | Verify the injected domain-parent authorization. Creator audit identity alone is not access proof. |

All DB phases must close before PUT, HEAD, DELETE, presign, or HTTP fetch.
Reconcile output is counts and allowlisted codes only. The web health endpoint,
Telegram worker, and OTP dispatcher remain independent of storage health.

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
provider before production claims are made.
