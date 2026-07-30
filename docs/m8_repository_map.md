# Nasiya M8 Repository Map

Status: repository-aware map for M8 implementation.
Sources: M8.01 baseline audit, M8.02 contract reconciliation, M8.03 primitive
map, M8.04 feasibility audit, the M8 Final Scope Freeze, and the Recovery Fix
1–2 post-freeze Product Owner corrections.

The separate Product Gate audit is not a repository file. The Final Scope
Freeze states that it incorporates that audit; M8.03–M8.04 independently
rechecked its repository claims.

## M8.02 Contract Reconciliation

| Contract / decision | Authority | Inherited or M8-new | Repository evidence | M8 impact | Finding |
|---|---|---|---|---|---|
| Caller-owned transaction | M4/M6/M7; M8 freeze 7.1 | Inherited | `app/db.py:22` | Storage repositories do not commit/rollback/close. | OK |
| External I/O outside transaction | M6/M7; M8 PO-M8-6 | Inherited and M8-new | `app/telegram/update_processing.py:128`, `app/otp/dispatcher.py:425`, `app/otp/dispatcher.py:442`, `app/otp/dispatcher.py:465` | Reuse fresh DB phase, external phase, fresh DB phase. | OK |
| Web degraded startup | M6/M7; M8 PO-M8-4 | Inherited | `app/main.py:27`, `app/main.py:57`, `app/settings.py:59` | Storage bundle remains optional until operation. | OK |
| M6/M7 process isolation | M8 IN/OUT | Inherited | `compose.yaml:46`, `compose.yaml:72` | No storage env/dependency for worker or dispatcher. | OK |
| SecretStr/redaction | TT 8; M6/M7 | Inherited | `app/settings.py:6`, `app/settings.py:31`, `app/telegram/bot_api.py:63`, `app/otp/crypto.py:33` | Credentials and value wrappers redact by default. | OK |
| Trusted client IP | M4/M6/M7 | Inherited | `app/request_client_ip.py:19` | Storage IP limiter uses resolver output. | OK |
| Session CSRF | TT 8; M8 PO-M8-20 | Inherited and M8-new | `app/auth/deps.py:164`, `app/auth/deps.py:241`, `app/auth/deps.py:265` | Body guard must precede existing multipart form parse. | OK |
| No-store | TT 8 | Inherited | `app/security_headers.py:61` | Future domain response uses existing helper; no M8 route. | OK |
| Single-job CI | M6/M7; M8 PO-M8-22 | Inherited | `.github/workflows/ci.yml:12` | Add MinIO within `dependency-sync`. | OK |
| Exactly one M8 table | M8 persistence contract | M8-new | M7 head `e7f8a9b0c1d2` has 17 inherited tables; M8 head `f8a9b0c1d2e3` adds only `object_files` | Keep exactly one M8 table. | OK |
| No public route/domain consumer | M8 PO-M8-1/18 | M8-new boundary | `app/main.py:48` includes only auth/customer/shop routers; no storage code | Add internal service/CLI only. | OK |
| PO-M8 decisions | M8 freeze section 6 plus Recovery Fix 1–2 corrections | M8-new | `docs/m8_decisions.md` | The `24/24` remain frozen; provisioning/data-plane and immediate-missing-HEAD boundaries are corrected post-freeze. | OK |

The provisioning/data-plane and successful-PUT/immediate-missing-HEAD
contradictions are resolved by the explicit post-freeze Product Owner
corrections. No unresolved contradiction remains.

### Recovery Fix 1 Responsibility Map

| Plane | Credential owner | Exact responsibility | Repository evidence |
|---|---|---|---|
| Provisioning/admin | `minio` and `minio-init` only | create configured bucket, enforce/verify private anonymous access, create/update scoped app identity and policy | `deploy/minio-init.sh`, `compose.yaml` |
| Application data | web and storage CLI, app identity only | configured-bucket access check, PUT, HEAD, DELETE, presigned GET | `app/storage/contracts.py`, `app/storage/s3.py`, `app/cli.py` |
| Privacy proof | init plus designated acceptance/CI | verify anonymous denial and app admin-operation denial | `tests/test_storage_minio_init.py`, `tests/test_storage_minio_integration.py`, `.github/workflows/ci.yml` |
| Isolated roles | M6 worker and M7 dispatcher | no root/app storage credential and no MinIO dependency | `compose.yaml`, degraded-mode containment tests |

### Recovery Fix 2 Lifecycle Map

| Observation | Immediate durable result | Later bounded action | Repository evidence |
|---|---|---|---|
| PUT success or ambiguity, immediate HEAD missing | same row/key stays `PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN`; closed error, no object result | stale reconciler HEADs the same key without PUT | `app/storage/service.py`, `app/storage/repository.py` |
| stale HEAD exact | `AVAILABLE` | none | `app/storage/service.py:reconcile_stale_object_uploads` |
| stale HEAD missing | `FAILED/OBJECT_MISSING_AFTER_UPLOAD` | none | `app/storage/service.py:reconcile_stale_object_uploads` |
| stale HEAD mismatch | `DELETE_PENDING` | bounded DELETE, then `DELETED` | `app/storage/service.py:reconcile_stale_object_uploads` |

The test fake can return one non-mutating missing HEAD to model delayed
visibility. Workflow tests prove one row/key and one PUT across all three
stale outcomes; transaction and concurrency tests preserve the last committed
recoverable state and one legal final transition.

## M8.03 Primitive Map

### Settings, Errors, And Security

| Item | File / symbol | Status |
|---|---|---|
| Pydantic settings | `app/settings.py:40` `Settings` | REUSE / EXTEND |
| Secret values | `app/settings.py:58` rate key; `app/settings.py:60` bot token; `app/settings.py:61` OTP key | REUSE pattern |
| Optional require helper | `app/settings.py:248`, `app/settings.py:256` | REUSE pattern |
| Hidden validation inputs | `app/settings.py:80` `hide_input_in_errors=True` | REUSE |
| Stable error catalog | `app/auth/error_codes.py:9`, `app/auth/error_codes.py:32`, `app/auth/error_codes.py:111` | EXTEND minimally |
| Security headers | `app/security_headers.py:29` | REUSE |
| No-store | `app/security_headers.py:61` | REUSE |
| Authenticated user | `app/auth/deps.py:152` `require_user` | REUSE for future adapter only |
| Trusted IP | `app/request_client_ip.py:19` | REUSE |
| HMAC rate limiter | `app/auth/rate_limit.py:AuthRateLimiter`, `app/storage/rate_limit.py:StorageUploadRateLimitPolicy` | IMPLEMENTED with storage-only UUID/IP scopes |

### Upload Boundaries

| Item | File / symbol | Status |
|---|---|---|
| `python-multipart` | `pyproject.toml:15` | REUSE |
| Existing multipart CSRF parse | `app/auth/deps.py:241`, `app/auth/deps.py:252`, `app/auth/deps.py:265` | EXTEND via bounded helper |
| FastAPI automatic forms | `app/auth/router.py:155`, `app/shop/router.py:155` | Pattern exists; forbidden for future storage file parameter |
| ASGI middleware hook | `app/security_headers.py:43` | REUSE registration pattern |
| Actual-byte pre-parse guard | `app/storage/body_guard.py` | IMPLEMENTED; opt-in path set is empty in M8 production |
| Bounded one-file multipart helper | `app/storage/multipart.py` | IMPLEMENTED with cached existing session CSRF validation |
| Production storage route | Not present | KEEP ABSENT |

Installed Starlette `1.3.1` exposes
`Request.form(max_files, max_fields, max_part_size)`, so the bounded helper is
feasible without another parser dependency.

### Database And Transactions

| Item | File / symbol | Status |
|---|---|---|
| Declarative base | `app/db.py:10` `Base` | REUSE |
| Request transaction owner | `app/db.py:22` | REUSE |
| Session factory | `app/db.py:18` | REUSE |
| Short non-request TX | `app/otp/dispatcher.py:432`, `app/otp/dispatcher.py:472` | REUSE pattern |
| External boundary between DB phases | `app/otp/dispatcher.py:442` | REUSE pattern |
| Alembic metadata imports | `alembic/env.py:8`–`alembic/env.py:14` | EXTEND one storage import |
| Current Alembic head | `alembic/versions/f8a9b0c1d2e3_create_object_files.py` | IMPLEMENTED linear M8 head |
| Test DB `_test` guard | `tests/postgresql.py:29` | REUSE |
| Child-first cleanup | `tests/postgresql.py:8` | EXTEND with `object_files` first |
| Alembic head lookup | `tests/postgresql.py:44` | REUSE |
| `FOR UPDATE` | `app/auth/rate_limit.py:92` | REUSE pattern |
| `SKIP LOCKED` | `app/otp/repository.py` claim queries | REUSE pattern |
| Exactly one M8 model/table | `app/storage/models.py`, `object_files` | IMPLEMENTED |

### Typed And Sensitive Values

| Item | File / symbol | Status |
|---|---|---|
| UUID defaults | `app/auth/models.py:30`, `app/otp/models.py:135` | REUSE pattern |
| SHA-256 hex values | `app/otp/crypto.py:14`, `app/otp/crypto.py:172` | REUSE pattern |
| Redacted dataclasses | `app/settings.py:31`, `app/auth/rate_limit.py:15` | REUSE pattern |
| Explicit secret reveal | `app/settings.py:98`, `app/auth/rate_limit.py:187` | REUSE only at narrow boundary |
| Storage protocols/wrappers | `app/storage/contracts.py` | IMPLEMENTED with narrow provider/authorization protocols and redacted values |

### CLI, Deployment, CI, And Tests

| Item | File / symbol | Status |
|---|---|---|
| Main argparse CLI | `app/cli.py:build_parser`, `app/cli.py:main` | IMPLEMENTED with app-credential data-plane preflight/health, reconcile, dev-only delete, and synthetic smoke commands |
| Dedicated process CLI pattern | `app/telegram/worker.py:125`, `app/otp/dispatcher.py:116` | REUSE pattern only |
| Same runtime image | `Dockerfile:23`, `Dockerfile:32` | REUSE |
| Compose DB/migrate/web | `compose.yaml:2`, `compose.yaml:18`, `compose.yaml:29` | REUSE |
| M6 worker | `compose.yaml:46` | KEEP UNCHANGED |
| M7 dispatcher | `compose.yaml:72` | KEEP UNCHANGED |
| MinIO/init/volume | `compose.yaml`, `deploy/minio-init.sh` | IMPLEMENTED admin plane with pinned images, create-if-missing, private/anonymous-deny verification, scoped app identity, and persistent named volume |
| Single CI job | `.github/workflows/ci.yml:12` | REUSE / EXTEND |
| PostgreSQL CI service | `.github/workflows/ci.yml:35` | REUSE |
| Full-suite no skip guard | `.github/workflows/ci.yml:107` | REUSE |
| Containment tests | `tests/test_shop_containment_guard.py`, `tests/test_telegram_scope_regression.py`, `tests/test_otp_sensitive_data_audit.py` | REUSE / EXTEND |
| Real PostgreSQL fixture | `tests/conftest.py:15`, `tests/conftest.py:35` | REUSE |
| Fake storage adapter | `tests/storage_fake.py` | IMPLEMENTED in test support only |

### Storage/Image Primitives

| Item | Status | Minimal solution |
|---|---|---|
| Pillow | IMPLEMENTED dependency | Direct `Pillow>=12.3.0,<13`, resolved `12.3.0`; bounded sanitizer is implemented in `app/storage/image.py`. |
| boto3/botocore | IMPLEMENTED dependency | Direct boto3 `1.43.59`; botocore `1.43.59` transitive; single-attempt adapter is implemented in `app/storage/s3.py`. |
| MinIO Python SDK | TOPILMADI | Keep absent; use boto3 adapter and pinned container/`mc`. |
| libmagic/python-magic | TOPILMADI | Keep absent; use Pillow fully decoded `Image.format`. |
| S3 adapter | IMPLEMENTED | `app/storage/s3.py` is data-plane only: one configured-bucket access check plus object PUT/HEAD/DELETE/presigned GET; it has no provisioning/admin API. |
| Image sanitizer | IMPLEMENTED | `app/storage/image.py` performs bounded decode, fresh-pixel render, and deterministic re-encode. |
| Object lifecycle model/repository | IMPLEMENTED | `app/storage/models.py` and `app/storage/repository.py`; one table and caller-owned primitives. |
| Public file endpoints | TOPILMADI | Keep absent. |

## M8.04 Feasibility Audit

| Requirement | Result | Evidence / minimal solution |
|---|---|---|
| Python 3.12 Pillow wheel | IMPLEMENTED dependency | Pillow `12.3.0`, `MIT-CMU`, CPython 3.12 manylinux x86_64 wheel, Python `>=3.10`. |
| Current slim codecs | PROVED | M8.08 no-cache built-image acceptance encoded and decoded JPEG/PNG/WebP, including alpha for PNG/WebP. No OS package was required or added. |
| boto3 on Python 3.12 | IMPLEMENTED dependency | boto3 `1.43.59`, `Apache-2.0`, universal wheel, Python `>=3.10`; botocore resolves to `1.43.59`. |
| Constructor without network | MINIMAL NEW feasible | Explicit endpoint/region/credentials plus injected client and disabled metadata/default credential lookup avoid discovery; Stubber verifies no constructor call. |
| `python-multipart` | REUSE | Already direct in `pyproject.toml:15`. |
| No MinIO SDK/libmagic | REUSE absence | boto3 and Pillow cover the frozen boundaries. |
| Web startup without storage | REUSE / EXTEND | All storage fields default absent; `create_app` does not construct storage clients. |
| Pre-parse guard before CSRF | MINIMAL NEW feasible | ASGI receive wrapper counts bytes before the bounded helper caches form data for CSRF. |
| TX-S1/I-O/TX-S2 | REUSE pattern | M7 dispatcher already closes one DB phase before external send and opens a fresh result phase. |
| No raw image persistence | MINIMAL NEW feasible | Source and sanitized bytes stay in bounded in-memory wrappers; DB stores metadata only. |
| Stale upload claim | MINIMAL NEW feasible | Short `FOR UPDATE SKIP LOCKED` claim updates `updated_at`, commits, then HEAD occurs without a session. |
| No automatic PUT retry | MINIMAL NEW feasible | Configure botocore retry attempts to zero and classify ambiguous outcomes for HEAD. |
| Compose private MinIO | MINIMAL NEW feasible | Docker `29.6.x` and Compose `5.3.x` are available; add pinned service/init/volume without changing DB dependencies. |
| CI MinIO same job | MINIMAL NEW feasible | Existing job has Docker-capable Ubuntu runner and one PostgreSQL service; add bounded runtime steps. |

Feasibility result: Pillow+boto3, private MinIO, two-phase storage, bounded
multipart, and no-retry reconciliation are achievable without prohibited
dependency, table, process-role, or persistence changes. No blocker found.

## M8.37 S3 Adapter Placement And SDK Reconciliation

The lock resolves `boto3==1.43.59` and `botocore==1.43.59`. Local inspection
of that exact botocore package confirms the exception inheritance used by the
scope appendix: `ClientError` is separate from `BotoCoreError`;
`ReadTimeoutError` and `ConnectionClosedError` inherit `HTTPClientError`; and
validation, credential, endpoint, connect, proxy, SSL, HTTP-client, and other
SDK failures inherit `BotoCoreError`.

The minimal placement remains one `app/storage/s3.py` module:

| Symbol / responsibility | Placement | Dependency direction |
|---|---|---|
| fixed connect/read/pool/no-retry constants | `app/storage/s3.py` | no settings expansion |
| `create_s3_client(StorageConfig)` | `app/storage/s3.py` | explicit config to injected boto3 client |
| narrow adapter implementing `ObjectStorageService` | `app/storage/s3.py` | contracts inward; boto3 client outward |
| exception classifier | private helper in `app/storage/s3.py` | exact pinned botocore classes |
| fake programmable adapter | test support only | same contracts; no production global state |
| app data-plane preflight coordinator | `app/cli.py:storage_preflight_command` | calls `check_bucket_access` once with app credentials and emits one fixed safe status |

No repository, ORM model, router, settings field, provider registry, base
adapter, plugin framework, background process, or second storage dependency is
needed. The exact factory kwargs, HeadBucket access check,
PutObject/HeadObject/DeleteObject/presign calls, checksum metadata key, missing
semantics, and failure table are recorded in `docs/m8_scope_contract.md`.

## M8.49 Upload Coordinator Placement And Transaction Patterns

`ingest_sanitized_image` belongs in the existing
`app/storage/service.py` module beside the authorization-gated presign
coordinator. It is an internal coordinator, not a repository primitive,
router, dependency, provider base class, or domain attachment service.

| Required behavior | Existing repository evidence | Exact reuse |
|---|---|---|
| Engine-to-factory composition | `app/db.py:create_database_session_factory` | Composition roots create one `sessionmaker[Session]` and inject it; the upload coordinator does not create an engine. |
| Request composition stores the factory | `app/main.py:create_app` | A future adapter obtains the existing application session factory; M8 adds no route. |
| CLI creates engine/factory and disposes engine | `app/cli.py:storage_reconcile_command`, `app/cli.py:storage_delete_command` | The internal storage CLI constructs dependencies at the edge, disposes the engine, and does not pass an open Session across provider I/O. |
| Short outer-owned DB phase | `app/otp/dispatcher.py:_prepare_next_item` | `with session_factory.begin()` supplies a fresh Session and owns commit/rollback/close. |
| External work after the DB context | `app/otp/dispatcher.py:_send_prepared_otp` | PUT/HEAD uses a detached, redacted in-memory envelope after TX-S1 has closed. |
| Fresh result transaction | `app/otp/dispatcher.py:_record_delivery_result` | TX-S2 opens a different `session_factory.begin()` context and locks/transitions the row. |
| Factory-owned transaction helper | `app/telegram/update_processing.py:process_telegram_update_tx_a` | The coordinator returns detached safe values only after the context commit succeeds. |
| Caller-owned persistence primitives | `app/storage/repository.py:create_pending_object_file`, `app/storage/repository.py:mark_object_file_available`, `app/storage/repository.py:mark_object_file_failed` | Repositories flush only; coordinator contexts own transaction completion. |
| Borrowed bounded source and sanitizer | `app/storage/image.py:read_bounded_image`, `app/storage/image.py:sanitize_bounded_image` | Both complete before TX-S1; the multipart/request owner closes the borrowed source. |
| Trusted IP value | `app/request_client_ip.py:resolve_client_ip`, `app/telegram/client_ip.py:ResolvedClientIp` | Coordinator accepts only the resolved wrapper and passes its raw form solely to the HMAC limiter. |
| Upload attempt limiter | `app/storage/rate_limit.py:record_storage_upload_attempt` | Stable user-then-IP HMAC buckets are recorded in a short caller-owned transaction before source decode. |
| Injected storage boundary | `app/storage/contracts.py:ObjectStorageService` | Coordinator invokes one PUT and bounded HEAD/delete operations; it never constructs or closes the provider. |

Exact dependency direction:

```text
future request adapter / implemented internal CLI adapter
  -> Settings + session_factory + authenticated UUID + ResolvedClientIp
  -> ingest_sanitized_image
       -> existing HMAC rate-limit primitive (short DB-only phase)
       -> image reader/sanitizer (no Session)
       -> storage repository primitives (TX-S1/TX-S2)
       -> injected ObjectStorageService (between closed DB phases)
  -> IngestedImageResult (UUID + safe metadata only)
```

The coordinator owns every Session it opens and no provider/client/source it
receives. No repository symbol gains `commit()`, full `rollback()`, or
`close()`. No route, domain-owner field, generic DTO layer, event, outbox,
scheduler, second table, or generic framework module is needed for this API.

For both successful and ambiguous PUT, an immediate missing HEAD takes the
same result path: a fresh locked transaction leaves the existing row
`PENDING_UPLOAD/UPLOAD_OUTCOME_UNKNOWN` and returns no object. Only later
stale reconciliation may interpret another missing HEAD as
`FAILED/OBJECT_MISSING_AFTER_UPLOAD`; reconciliation never PUTs or generates a
new key.

## M8.07 Resolved Dependency Map

| Package | Direct/transitive | Exact version | License |
|---|---|---:|---|
| `Pillow` | direct | `12.3.0` | `MIT-CMU` |
| `boto3` | direct | `1.43.59` | `Apache-2.0` |
| `botocore` | transitive | `1.43.59` | `Apache-2.0` |
| `jmespath` | transitive | `1.1.0` | `MIT` |
| `s3transfer` | transitive | `0.19.2` | `Apache License 2.0` |
| `python-dateutil` | transitive | `2.9.0.post0` | dual Apache/BSD distribution license |
| `six` | transitive | `1.17.0` | `MIT` |
| `urllib3` | transitive | `2.7.0` | `MIT` |

`pyproject.toml` contains only the two approved new direct requirements.
`uv.lock` is the exact resolver authority. Host import smoke and frozen sync
are green. M8.08 also proved the no-cache built-image codec acceptance.

## Missing Primitive Placement

```text
app/storage/
  __init__.py       only when the first real storage module is added
  contracts.py      protocols and redacted typed values
  errors.py         internal closed error mapping if not kept beside contracts
  body_guard.py     opt-in ASGI actual-byte guard
  multipart.py      bounded one-file/session-CSRF helper
  image.py          bounded reader and sanitizer
  models.py         ObjectFile only
  repository.py     caller-owned lifecycle primitives
  s3.py             boto3 factory and adapter
  service.py        upload/reconcile/delete/authorized-GET coordinators
  smoke.py          development/test-only synthetic acceptance coordinator
app/cli.py           bounded internal storage command adapters
deploy/
  minio-init.sh      idempotent private local/CI provisioning
  minio-backup-restore-exercise.sh
                     local synthetic backup/restore exercise
```

The authorization seam is the narrow protocol in `contracts.py` plus its
coordinator in `service.py`; no empty `authorization.py` placeholder is
needed. Files are created only when their task contains real code. No generic
registry, base repository, event bus, or placeholder module is approved.
