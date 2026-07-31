# Nasiya M9 Known Limitations

Status: authoritative M9 readiness limitations after FINAL `CR-M9-01`.
Baseline: `5429e950d0ef25dcb99617e7ca109b1aa08fc697`.
These are not claims that M9 product code exists or that M9 is closed.

## KL-M9-01 — Central Audit/Redaction Foundation Is Approved But Unimplemented

The TT and approved freeze require append-only, centrally redacted lifecycle
and acceptance events in the same transaction as each mutation. The baseline
has narrow shop, Telegram, and OTP event journals, but no central audit
service, redaction service, `audit_log` model, or `audit_log` table.

FINAL CR-M9-01 resolution:

- Logs or existing domain journals cannot substitute for the required
  authority.
- M9 may add exactly one `audit_log` supporting table and the narrow
  transaction-aware append/redaction boundary frozen in the scope contract.
- The three offer-domain tables remain unchanged.
- Audit viewing, query APIs, update/delete, retention, export, arbitrary
  event ingestion, and generic admin audit UI remain unavailable.

Impact:

- Scope/readiness is no longer blocked by a missing authority decision.
- Persistence and lifecycle checkpoints remain red until the approved
  foundation, atomicity tests, and leakage canaries are implemented.
- Any implementation broader than CR-M9-01 is a new blocker and scope-review
  trigger.

## KL-M9-02 — Platform-Admin Identity Is Not Implemented

`User` has no platform-admin field, no production platform-admin dependency
exists, and `ShopRole` is tenant-scoped. The repository also has no
first-admin bootstrap.

Impact:

- No existing user can safely authorize `/admin/offers`.
- Shop owner/manager/cashier membership cannot grant platform authority.

Approved minimal resolution:

- Add one non-null `users.is_platform_admin` boolean defaulting false.
- Add an offer-scoped typed route/service guard.
- Add a first-admin-only operator CLI promotion for one existing active user
  when the current platform-admin count is zero.

This gap is already bounded by PO-M9-07; it does not authorize admin creation
UI, second-admin management, revocation, impersonation, or a full admin suite.

## KL-M9-03 — No Shared Profile Locale Exists

The baseline contains feature-local UZ-Latn/RU presentation maps, but no
locale field on `User`, customer, or session and no shared application i18n
service.

Impact:

- M9 cannot truthfully claim a persisted account-wide UI language preference.
- UI locale must not be inferred from accepted legal language.

Approved minimal resolution:

- Use an offer-local pure UZ-Latn/RU resolver following the existing
  presentation-map style, with `Accept-Language` fallback and UZ-Latn default.
- Keep legal `UZ_LATN/UZ_CYRL/RU` selection explicit and independent.
- Do not add a generic i18n framework or profile capability in M9.

## KL-M9-04 — Existing User-Agent Helper Is Only A Length Bound

`app.auth.user_agent:truncate_user_agent` limits values to 512 characters but
does not remove control characters. Calling that output “normalized” would be
an inaccurate repository claim.

Impact:

- It is insufficient by itself for immutable legal acceptance evidence.
- Raw or control-bearing UA must not reach audit, log, error, report, or repr.

Approved minimal resolution:

- Add one offer-specific standard-library normalizer with the exact
  512-character/control/whitespace semantics in the scope contract.
- Persist only the normalized result and add empty/max/max+1/control tests.

## KL-M9-05 — M9 Does Not Supply Production Legal Text

M9 supplies lifecycle and evidence mechanics, not authoritative legal copy.
Automated tests may use synthetic text, and optional development seed may
create drafts only.

Impact:

- No fixture, migration, AI output, or admin button proves legal approval.
- Production cannot have an approved/current version until a real external
  legal review supplies the required evidence.
- Manual acceptance must label its text synthetic and must not be presented
  as production legal approval.

## KL-M9-06 — Acceptance Is Not Registration Or Activation

M9 accepts a current registration offer only for an already authenticated,
active account. It creates immutable acceptance evidence and a reusable
current-acceptance query; it does not create or activate a customer.

Impact:

- Public registration and `purpose=REGISTRATION` OTP remain unavailable.
- Acceptance alone does not create `customer`, `customer_document`,
  `shop_customer`, or any object-domain link.
- M10/M11 must provide their separately approved PII/document and
  registration/activation capabilities.

## KL-M9-07 — Debt Purpose Has No Runtime Acceptance

`DEBT_ACCEPTANCE` is an allowed future purpose so version persistence and the
current resolver do not need redesign later. M9 runtime acceptance is
hard-coded to `REGISTRATION`.

Impact:

- No debt can be created, shown, accepted, rejected, or expired in M9.
- A `DEBT_ACCEPTANCE` row cannot be accepted through the M9 account endpoint.
- Future debt work needs its own authorization, idempotency, money, and
  transaction decisions.

## KL-M9-08 — Historical Evidence Has No Delete Or Retention Workflow

Offer versions, text variants, and acceptances use restrictive historical
references. M9 exposes no delete, purge, archival, or legal-retention route.

Impact:

- Administrators correct content by creating a new draft, not editing or
  deleting approved/current history.
- Database retention/erasure policy requires a later explicit legal and
  operational decision.
- M8 object-storage retention is unrelated and is not pulled into M9.

## KL-M9-09 — Scope Checkpoint Is Not Product Implementation

At this baseline there is no `app/offers` or `app/audit` package, M9 model,
migration, route, template, or M9 test. M9.07 and the M9.08 correction define
and freeze repository scope only.

Impact:

- The frozen threat matrix is a required future test contract, not green test
  evidence.
- No product migration, targeted product pytest, manual browser acceptance,
  push, or remote CI claim belongs to the scope checkpoint.
- M9 cannot be called technical green or closed.

## Validation State

M8 remains `M8 REMOTE GREEN — CLOSED`. M9 documentation is internally
defined and CR-M9-01 resolves the former authority blocker. The approved
platform-admin and central-audit foundations are still unimplemented and must
pass their later task gates.
No public registration, activation, PII, document, shop link, debt, payment,
rating, disclosure, notification, scheduler, or generic CMS work has begun.
