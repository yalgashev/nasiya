# Nasiya M10 Known Limitations

Status: authoritative M10 limitations, pre-implementation.
Baseline: M9 remote-green/docs-only closeout
`f96b9f0a6d6b506f6715aa354cb4346199f1f5c5`.
Amendment: `CR-M10-01 — FINAL APPROVED` amends only PO-M10-14/15 and is
binding on KL-M10-01.

The amendment freezes the full corrected boundary, not only the deferred
cleanup: a short early `check_storage_upload_rate_limit` performs no record;
M8 `ingest_sanitized_image` alone calls `record_storage_upload_attempt`
exactly once and finally enforces before source/provider work; TX-B locks the
server-returned `object_file` as the attach/compensation serialization pivot;
TX-C atomically proves global nonattachment and claims `DELETE_PENDING`; and
the existing M8 reconciliation performs eventual provider cleanup. This does
not create a second limiter, request delete, or M10 scheduler.

M10.08 freezes scope and repository decisions; it does not claim that M10
product code, migration, dependency, tests, checkpoints, or remote evidence
exists. These limitations are the permitted residual boundaries after a
future valid M10 closure. They do not authorize their implementation in M10.

## KL-M10-01 — Orphan Cleanup Is Eventual, Not Immediate

TX-Bdan keyingi orphan object request ichida darhol o‘chirilmaydi. U atomik
`DELETE_PENDING` claimdan keyin existing M8 reconciliation orqali eventual
tarzda o‘chiriladi; M10 scheduler yaratmaydi.

CR-M10-01 requires TX-C to lock the `object_file`, prove in the same
transaction that no `customer_documents` row references it, and then call
`mark_object_file_delete_pending(failure_code=None)`. The request performs no
S3/MinIO DELETE. Existing `reconcile_stale_object_deletes` later performs
provider cleanup: a definite result becomes `DELETED`; an ambiguous result
remains reconcilable `DELETE_PENDING` with `DELETE_OUTCOME_UNKNOWN`.

This delay is intentional race safety. An attached/replayed object is a TX-C
no-op and can never be claimed or deleted by compensation. M10 adds no worker,
cron, scheduler, command, retry policy, or replacement reconciliation path;
operators retain the existing M8 reconciliation mechanism.

## KL-M10-02 — JSHSHIR Is Not Government-Verified

M10 validates exactly 14 ASCII digits and enforces keyed blind-index
uniqueness. It does not validate an official checksum, query a government
registry, or claim that a person/document is genuine. Registry verification
requires a separately approved integration and disclosure/privacy contract.

## KL-M10-03 — Document Images Are Not Parsed Or Authenticated

The passport/ID image is only a bounded, decoded, metadata-stripped M8 image
attachment. M10 performs no OCR, MRZ parsing, field extraction, document
authenticity check, selfie capture, face match, liveness, or biometric work.
No extracted PII is persisted.

## KL-M10-04 — One Current Image, Not A Document Set

M10 supports one current customer document image plus private superseded
history. It does not model front/back pairs, several documents, page sets,
document approval/rejection, expiry reminders, or history UI. A later design
must define those states without weakening one-current and object uniqueness.

## KL-M10-05 — Crypto Is Rotation-Ready But Has No KMS Or Rotation Runtime

The envelope stores schema version and key ID, and settings may contain
historical decrypt keys. M10 does not select a production KMS/vendor, generate
keys, rotate rows, hot-reload a keyring, expose key administration, or build a
generic KMS abstraction. Operators must supply reviewed secret material
through the frozen environment/secret-management boundary.

## KL-M10-06 — Blind-Index Key Has No Online Rotation

The JSHSHIR blind index uses one dedicated 32-byte lookup key. Online dual-key
lookup, reindexing, lookup-key rotation, or an operator migration is absent.
Changing this key therefore requires a future privacy/concurrency/data-
migration decision rather than an M10 fallback.

## KL-M10-07 — Superseded Evidence Has No Retention Or Erasure Workflow

Historical document references use restrictive foreign keys and M10 exposes
no delete, purge, archival, retention, legal-hold, or erasure flow. A later
legal/operational decision must reconcile privacy obligations with historical
evidence and M8 object lifecycle before any destructive capability is added.

## KL-M10-08 — No Cross-Customer PII Administration

M10 has only an authorized own-user summary and own-current document access.
Shop owner/manager/cashier roles and `is_platform_admin` grant no other-
customer PII access. There is no PII admin UI, support impersonation, customer
search, document history UI, access-audit UI, export, or correction workflow
for active customers.

## KL-M10-09 — Identity Completeness Is Not Registration Or Activation

An authenticated account may have complete encrypted identity and a current
document while its M3 customer remains exactly `draft`. M10 does not create a
customer, make a `draft -> active` transition, enroll Telegram, execute a
registration OTP, evaluate full registration eligibility, or create a
`shop_customer` relationship.

## KL-M10-10 — Public And Shop-Assisted Onboarding Remain Deferred

Public registration, `REGISTRATION` OTP, customer lead, shop-assisted PII
capture, existing-customer linking after duplicate JSHSHIR, and shop/customer
association are future milestone work. M10's `DUPLICATE_JSHSHIR` is deliberately
non-disclosing and performs no auto-link.

## KL-M10-11 — UI Localization Remains Feature-Local

The identity/document shell supports UZ-Latn and RU with UZ-Latn fallback.
There is no persisted account-wide locale, generic i18n framework, mandatory
UZ-Cyrl UI, or inference from M9's accepted legal language. This follows the
inherited M9 localization limitation.

## KL-M10-12 — Production Storage Selection And Recovery Targets Remain M8 Work

M10 consumes M8's private S3-compatible contract. Approved local/CI MinIO is
test evidence, not a production provider selection. Production provider,
backup/restore, RPO/RTO, storage availability policy, and legal object
retention remain the inherited M8 limitations. There is no local-disk fallback.

## Explicitly Not Limitations That Widen Scope

Debt, payment, rating, disclosure, notification, scheduler, OCR/MRZ,
biometrics, registry integration, generic attachment/CMS/admin/KMS, public
registration, activation, and shop linkage are OUT scope, not gaps to fill in
M10. Any request to add them triggers the scope stop condition.

At M10.08, implementation and remote-green closure are still pending. These
entries may be updated only by an authorized later M10 limitation/closeout
task and may not silently weaken the scope, CR-M10-01, or inherited M3/M8/M9
contracts.
