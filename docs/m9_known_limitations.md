# Nasiya M9 Known Limitations

Status: authoritative limitations after `M9 REMOTE GREEN — CLOSED`.
Implementation: `e2cda04920964cf383a749e07504539ccdafa0ab`.
Remote CI: GitHub Actions `30645425078`, `success`.

The earlier readiness gaps for the central audit/redaction foundation,
platform-admin identity, offer-local UI presentation, bounded acceptance
browser evidence, product implementation, migration, and automated tests are
resolved in the remote-green implementation. They are not open limitations.

## KL-M9-01 — M9 Does Not Supply Production Legal Text

M9 supplies legal-offer lifecycle and evidence mechanics. It does not ship,
author, approve, or certify production legal wording.

- Automated and manual acceptance uses synthetic content only.
- A production version becomes approved/current only after real external legal
  review evidence is supplied through the approved platform-admin flow.
- M9 closure is technical evidence, not legal approval of any offer text.

## KL-M9-02 — Acceptance Is Not Registration Or Activation

M9 records acceptance of a current `REGISTRATION`-purpose offer for an already
authenticated active account. That acceptance is neither public registration
nor customer activation.

- No public registration or registration OTP flow exists.
- Acceptance does not create or activate a customer.
- It does not collect identity PII or create `customer_document` or
  `shop_customer` records.

## KL-M9-03 — Debt Purpose Has No Runtime

`DEBT_ACCEPTANCE` remains future-purpose vocabulary only. M9 has no debt
object, debt acceptance runtime, money movement, payment, void, receipt, or
debt disclosure flow. Future debt work requires its own approved contracts.

## KL-M9-04 — Historical Evidence Has No Retention Or Delete Workflow

Approved/current offer history and acceptance evidence are immutable and use
restrictive references. M9 exposes no delete, purge, archival, or retention
workflow. A later legal and operational decision must define retention and
erasure behavior without rewriting historical evidence.

## KL-M9-05 — Platform-Admin Management Is Deliberately Minimal

M9 implements the minimum tenant-independent platform-admin identity, guard,
and first-admin bootstrap required for offer administration. It does not add a
full admin-management suite: there is no general grant/revoke UI,
impersonation, last-admin policy, or global admin console.

## KL-M9-06 — Audit Support Is Append-Only And Has No Admin Surface

The CR-M9-01 typed event registry, safe-key redaction, and same-transaction
append port are implemented. M9 intentionally has no audit read/search UI,
export, arbitrary event ingestion, retention, purge, or generic audit platform.

## KL-M9-07 — UI Locale Is Feature-Local

The M9 offer UI supports UZ-Latn and RU, independently from the three legal
content languages. There is still no persisted account-wide locale preference
or generic application i18n framework; M9 does not infer UI locale from the
accepted legal language.

## KL-M9-08 — M10 And M11 Capabilities Remain Deferred

M9 does not pull forward activation, PII/identity proofing, customer documents,
shop linking, owner application, debt/payment, notification, scheduler, or
other M10/M11 capabilities. Those milestones require their own approved scope,
implementation, evidence, and closure. M10 implementation has not started.

## Closure State

M8 remains `M8 REMOTE GREEN — CLOSED`. M9 is
`M9 REMOTE GREEN — CLOSED` from exact implementation SHA
`e2cda04920964cf383a749e07504539ccdafa0ab`, GitHub Actions run `30645425078`,
Alembic head `a9b0c1d2e3f4`, and remote `2540 passed` with no
failed/skipped/xfailed/xpassed outcomes.
