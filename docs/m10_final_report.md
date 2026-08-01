# M10 Final Technical Report

Status: `M10 TECHNICAL GREEN — REMOTE CI PENDING`
Date: 2026-08-01

This report records the final local technical baseline for M10. It does not
claim remote CI success or formal milestone closure. Those claims require the
exact implementation SHA to be pushed and verified by GitHub Actions.

## Baseline And Authority

- Closed parent: `M9 REMOTE GREEN — CLOSED` at the documented M9 closeout
  baseline.
- M10 authority: `nasiya_m10_00_final_scope_freeze.md`, `CR-M10-01 — FINAL
  APPROVED`, `docs/m10_scope_contract.md`, and `docs/m10_decisions.md`.
- Repository integration evidence: `docs/m10_repository_map.md`.
- Residual boundaries: `docs/m10_known_limitations.md`.
- Current implementation checkpoint before the final technical commit:
  `db8b4818248fef019d9f3d7dbee082eed7d410ea`.
- The seven existing M10 checkpoint subjects are intact. The eighth and final
  implementation checkpoint is intentionally pending M10.86.

## Delivered Capability

M10 supplies an authenticated own-customer identity and concrete customer
document attachment foundation for an existing `draft` customer. Six identity
fields are encrypted with the approved AES-256-GCM dependency boundary;
JSHSHIR uniqueness uses the separate keyed blind index. Identity and current
document updates use revision/current-token stale protection. Document upload,
sanitization, object lifecycle, reconciliation, and authorized temporary read
reuse M8 without a parallel storage implementation.

M10 does not activate a customer or add public registration, REGISTRATION OTP,
customer lead, `shop_customer`, shop-assisted PII capture, debt, payment,
rating, disclosure, notification, scheduler, OCR/MRZ, biometric, registry,
generic attachment/CMS/admin, or KMS capabilities.

## Dependency, Migration, And Schema

| Item | Local result |
|---|---|
| Frozen dependency sync | GREEN; 48 packages |
| Approved direct crypto dependency | `cryptography` 50.0.0, frozen |
| Alembic parent | `a9b0c1d2e3f4` |
| Alembic head/current | `b0c1d2e3f4a5` / `b0c1d2e3f4a5` |
| Empty database `base -> head` | GREEN |
| M9/M10 downgrade-upgrade walk | GREEN; inherited data preserved |
| PostgreSQL schema inventory | 25 tables including `alembic_version` |
| M10 tables | exactly `customer_identities` and `customer_documents` |

## CR-M10-01 Definition Of Done

| Boundary | Verified local evidence |
|---|---|
| Early rate denial | Read-only check; no attempt record, source read, object row, provider call, TX-A, attachment, or audit |
| Successful upload | M8 ingest is the only recorder; exactly one existing user/IP attempt increment |
| Final rate race | M8 authoritative record blocks before source/provider/object work |
| Object authority | Only the server-returned ingest result reaches TX-B; no client object/customer authority |
| TX-B serialization | Customer, object-file pivot, and current-document lock order; only `AVAILABLE` and globally unattached objects attach |
| TX-C compensation | Fresh transaction atomically locks, proves global nonattachment, and claims `DELETE_PENDING` |
| Attach/claim race | Attach winner makes compensation `NOOP`; claim winner makes attachment zero-write |
| Request cleanup | No provider delete occurs in the request; existing M8 reconciliation owns eventual cleanup |
| Ambiguous delete | Remains `DELETE_PENDING/DELETE_OUTCOME_UNKNOWN` and is reconcilable |
| Session boundary | Source, sanitizer, PUT, HEAD, DELETE, presign, and fetch run with no open SQLAlchemy session/transaction |
| Metadata containment | Audit/error/log/repr/browser evidence contains no protected PII, crypto material, storage metadata, or temporary URL |

## Local Validation

| Check | Result |
|---|---|
| `uv sync --dev --frozen` | GREEN |
| `uv run ruff check .` | GREEN |
| `uv run ruff format --check .` | GREEN; 412 files |
| `docker compose config --quiet` | GREEN |
| Alembic empty/head/current/history and M9/M10 walk | GREEN |
| Focused M10 crypto/concurrency/storage/containment matrix | 232 passed |
| Focused matrix repeat after both full runs | 232 passed |
| CI/dependency/leak/scope matrix | 45 passed |
| Full PostgreSQL/MinIO `pytest -q -ra` | 2735 passed, 0 skip/xfail/xpass, 2 dependency warnings |
| Full validation repeat `pytest -q -ra` | 2735 passed, 0 skip/xfail/xpass, same 2 warnings |
| Separate `pytest -q -ra --durations=10` | 2735 passed, 0 skip/xfail/xpass, same 2 warnings |
| `git diff --check` before report | GREEN |

The focused sets overlap the full suite and are not additive totals. The two
warnings are dependency deprecation notices already visible in the full run;
they are not failures, skips, xfails, or xpasses.

## Browser And Real MinIO Acceptance

All manual acceptance data was synthetic. No credential, session, token,
identity plaintext, crypto material, storage identifier, object metadata, or
temporary access URL is recorded here.

1. M10.81 — real Chrome identity acceptance passed create, masked own view,
   blank sensitive update inputs, successful update, stale-revision rejection,
   duplicate-JSHSHIR rejection, `no-store`, and scope containment. Both test
   customers remained `draft`.
2. M10.82 — real Chrome used backend multipart upload for JPEG, PNG, and WebP
   against local PostgreSQL and MinIO. Three objects were `AVAILABLE`; exactly
   one attachment was `CURRENT` and two were `SUPERSEDED`. Reopened provider
   bytes had EXIF/GPS/XMP/ICC/comment metadata removed. Authorized fetch was
   HTTP 200 with a 300-second grant; raw anonymous access was 403; anonymous
   and cross-user application access were denied without provider access.
3. M10.83 — two real Chrome tabs opened the same current-document form. The
   first replacement attached; the stale second form returned
   `CUSTOMER_DOCUMENT_CHANGED` without provider work or partial writes. A fresh
   replacement preserved exactly one `CURRENT` attachment and superseded the
   prior current attachment.
4. M10.83 compensation — a real MinIO orphan remained present when fresh TX-C
   atomically claimed `DELETE_PENDING`; the request performed zero provider
   delete calls. Existing operator CLI reconciliation made it `DELETED`.
   Attached-object compensation was `NOOP`. Approved local ambiguous-delete
   injection produced `DELETE_PENDING/DELETE_OUTCOME_UNKNOWN`, and a later
   existing CLI reconciliation completed it as `DELETED`.

## Checkpoint And Remote State

Local technical validation is GREEN and this report is intentionally
remote-pending. M10.86 must create the exact final implementation checkpoint,
M10.87 must prove the eight-checkpoint pre-push state, and M10.88 must verify a
successful GitHub Actions run for that exact pushed SHA. Until then neither
this report nor any other document may claim formal remote closure.

`M10 TECHNICAL GREEN — REMOTE CI PENDING`
