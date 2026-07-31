# M9 Result

Status: `M9 REMOTE GREEN — CLOSED`

Date: 2026-07-31

## Milestone

M9 — Legal Offer Lifecycle & Registration Acceptance Foundation

## Exact Implementation And Remote Evidence

| Evidence | Exact result |
|---|---|
| Implementation SHA | `e2cda04920964cf383a749e07504539ccdafa0ab` |
| GitHub Actions run | `30645425078` |
| Workflow / job | `CI` / `dependency-sync` |
| Run and job conclusion | `success` / `success` |
| Full remote pytest | `2540 passed` |
| Outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Alembic head | `a9b0c1d2e3f4` |
| Implementation checkpoints | `8/8` exact commits |
| M1–M8 containment | GREEN |

The exact pushed implementation SHA was verified by GitHub Actions run
`30645425078`. Frozen dependency sync, Ruff, real PostgreSQL migration,
security, concurrency, and inherited containment checks all succeeded.

## Eight Implementation Checkpoints

1. `4b4f90c6fefc2e655acb29339d5b47a4d75dfbeb` —
   `M9: freeze legal offer scope`
2. `211b5ee20a5a9520bab3f51de3658ec6fbfa2f8c` —
   `M9: add legal offer domain contracts`
3. `fc5e8c3e3d2a0c08206a8783c68aa990bf3d4337` —
   `M9: add legal offer persistence`
4. `5bda1f4419cce5f13edb4308c1917537e19ae6ef` —
   `M9: add legal offer lifecycle services`
5. `17294ddae3de7cc4431ead2969ee0ad26a163067` —
   `M9: add registration offer acceptance`
6. `eb394163e70d0dccf024857ae5d54146dd0c54c0` —
   `M9: expose legal offer web flows`
7. `9fba758a7d62213ba5aab3853f6e3e47899a6ddd` —
   `M9: harden legal offer security and concurrency`
8. `e2cda04920964cf383a749e07504539ccdafa0ab` —
   `M9: complete legal offer foundation`

## Delivered And Accepted Boundary

- The offer lifecycle is platform-admin-only, approval requires external
  legal-review evidence, and current replacement is concurrency-safe.
- Authenticated `REGISTRATION` offer acceptance is stale-form-safe and exact
  replay converges to one immutable acceptance and audit event.
- UZ-Latn and RU UI shells are complete. Legal content language is independent
  and renders with `uz-Latn`, `uz-Cyrl`, or `ru` tags under autoescape.
- `app.offers.router:router` is composed directly and exactly once in
  `app.main:create_app`; the auth router does not import or extend its routes.
- The CR-M9-01 append-only typed audit/redaction boundary and its one
  supporting `audit_log` table are implemented without a generic audit UI.

## Genuine Manual Chrome Acceptance

Headless Google Chrome exercised the local application against PostgreSQL
using only synthetic legal content.

1. A platform admin created a `REGISTRATION` draft, saved all three legal
   languages, supplied synthetic external-review evidence, approved the
   version, and made it current; lifecycle state and audits matched.
2. A separate authenticated account viewed the RU variant, accepted it, and
   replayed the same submission; exactly one acceptance and one audit remained.
3. With no current offer, the account flow failed closed with
   `OFFER_UNAVAILABLE`.
4. After a displayed form became stale because current changed, submission
   failed closed with `OFFER_CHANGED` and created no acceptance.

## Scope Closure

M9 adds no public registration, activation, new PII, `customer_document`,
`shop_customer`, debt runtime, payment, notification, scheduler, or generic
CMS capability. M10 implementation has not started.

At the time this file was authored, the docs-only closeout commit carrying it
had not yet been created. That commit is a documentation-only descendant of
the remote-green implementation SHA and is not a ninth implementation
checkpoint.

`M9 REMOTE GREEN — CLOSED`
