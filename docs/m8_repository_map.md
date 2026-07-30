# Nasiya M8 Repository Map

Status: repository-aware map for M8 implementation.
Sources: M8.01 baseline audit, M8.02 contract reconciliation, M8.03 primitive
map, M8.04 feasibility audit, and the M8 Final Scope Freeze.

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
| Exactly one M8 table | M8 persistence contract | M8-new | Current head `e7f8a9b0c1d2`; `tests/postgresql.py:8` lists 18 existing tables | Add only `object_files`. | OK |
| No public route/domain consumer | M8 PO-M8-1/18 | M8-new boundary | `app/main.py:48` includes only auth/customer/shop routers; no storage code | Add internal service/CLI only. | OK |
| PO-M8 decisions | M8 freeze section 6 | M8-new | `docs/m8_decisions.md` | All `24/24` are frozen. | OK |

Unresolved contradiction: none.

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
| HMAC rate limiter | `app/auth/rate_limit.py:32`, `app/auth/rate_limit.py:183` | REUSE with storage scopes |

### Upload Boundaries

| Item | File / symbol | Status |
|---|---|---|
| `python-multipart` | `pyproject.toml:15` | REUSE |
| Existing multipart CSRF parse | `app/auth/deps.py:241`, `app/auth/deps.py:252`, `app/auth/deps.py:265` | EXTEND via bounded helper |
| FastAPI automatic forms | `app/auth/router.py:155`, `app/shop/router.py:155` | Pattern exists; forbidden for future storage file parameter |
| ASGI middleware hook | `app/security_headers.py:43` | REUSE registration pattern |
| Actual-byte pre-parse guard | Not present | MINIMAL NEW in `app/storage/body_limit.py` |
| Bounded one-file multipart helper | Not present | MINIMAL NEW in `app/storage/multipart.py` |
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
| Current Alembic head | `alembic/versions/e7f8a9b0c1d2_create_m7_otp_persistence_tables.py` | REUSE parent |
| Test DB `_test` guard | `tests/postgresql.py:29` | REUSE |
| Child-first cleanup | `tests/postgresql.py:8` | EXTEND with `object_files` first |
| Alembic head lookup | `tests/postgresql.py:44` | REUSE |
| `FOR UPDATE` | `app/auth/rate_limit.py:92` | REUSE pattern |
| `SKIP LOCKED` | `app/otp/repository.py` claim queries | REUSE pattern |
| Exactly one M8 model/table | Not present | MINIMAL NEW |

### Typed And Sensitive Values

| Item | File / symbol | Status |
|---|---|---|
| UUID defaults | `app/auth/models.py:30`, `app/otp/models.py:135` | REUSE pattern |
| SHA-256 hex values | `app/otp/crypto.py:14`, `app/otp/crypto.py:172` | REUSE pattern |
| Redacted dataclasses | `app/settings.py:31`, `app/auth/rate_limit.py:15` | REUSE pattern |
| Explicit secret reveal | `app/settings.py:98`, `app/auth/rate_limit.py:187` | REUSE only at narrow boundary |
| Storage protocols/wrappers | Not present | MINIMAL NEW in `app/storage/contracts.py` |

### CLI, Deployment, CI, And Tests

| Item | File / symbol | Status |
|---|---|---|
| Main argparse CLI | `app/cli.py:68`, `app/cli.py:508` | EXTEND for internal storage commands |
| Dedicated process CLI pattern | `app/telegram/worker.py:125`, `app/otp/dispatcher.py:116` | REUSE pattern only |
| Same runtime image | `Dockerfile:23`, `Dockerfile:32` | REUSE |
| Compose DB/migrate/web | `compose.yaml:2`, `compose.yaml:18`, `compose.yaml:29` | REUSE |
| M6 worker | `compose.yaml:46` | KEEP UNCHANGED |
| M7 dispatcher | `compose.yaml:72` | KEEP UNCHANGED |
| MinIO/init/volume | Not present | MINIMAL NEW |
| Single CI job | `.github/workflows/ci.yml:12` | REUSE / EXTEND |
| PostgreSQL CI service | `.github/workflows/ci.yml:35` | REUSE |
| Full-suite no skip guard | `.github/workflows/ci.yml:107` | REUSE |
| Containment tests | `tests/test_shop_containment_guard.py`, `tests/test_telegram_scope_regression.py`, `tests/test_otp_sensitive_data_audit.py` | REUSE / EXTEND |
| Real PostgreSQL fixture | `tests/conftest.py:15`, `tests/conftest.py:35` | REUSE |
| Fake storage adapter | Not present | MINIMAL NEW in test support |

### Storage/Image Primitives

| Item | Status | Minimal solution |
|---|---|---|
| Pillow | TOPILMADI | Add only after M8.07 due diligence. |
| boto3/botocore | TOPILMADI | Add boto3 direct; botocore transitive. |
| MinIO Python SDK | TOPILMADI | Keep absent; use boto3 adapter and pinned container/`mc`. |
| libmagic/python-magic | TOPILMADI | Keep absent; use Pillow fully decoded `Image.format`. |
| S3 adapter | TOPILMADI | Add narrow injected boto3 adapter. |
| Image sanitizer | TOPILMADI | Add bounded Pillow implementation. |
| Object lifecycle model/repository | TOPILMADI | Add one model/table and caller-owned primitives. |
| Public file endpoints | TOPILMADI | Keep absent. |

## M8.04 Feasibility Audit

| Requirement | Result | Evidence / minimal solution |
|---|---|---|
| Python 3.12 Pillow wheel | MINIMAL NEW feasible | Official package metadata exposes a CPython 3.12 manylinux x86_64 wheel and Python `>=3.10`. |
| Current slim codecs | ACCEPTANCE REQUIRED | A wheel should carry codec support, but only M8.08 no-cache JPEG/PNG/WebP encode/decode proves this repository image. No OS package is pre-approved. |
| boto3 on Python 3.12 | MINIMAL NEW feasible | Official package metadata exposes a universal wheel and Python `>=3.10`. |
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

## Missing Primitive Placement

```text
app/storage/
  __init__.py       only when the first real storage module is added
  contracts.py      protocols and redacted typed values
  errors.py         internal closed error mapping if not kept beside contracts
  body_limit.py     opt-in ASGI actual-byte guard
  multipart.py      bounded one-file/session-CSRF helper
  image.py          bounded reader and sanitizer
  models.py         ObjectFile only
  repository.py     caller-owned lifecycle primitives
  s3.py             boto3 factory and adapter
  authorization.py  domain-parent authorizer protocol/service seam
  service.py        upload/reconcile/delete/download coordinators
  cli.py            storage internal command adapters if main CLI delegation helps
```

Files are created only when their task contains real code. No empty package
scaffold, generic registry, base repository, event bus, or placeholder module
is approved.
