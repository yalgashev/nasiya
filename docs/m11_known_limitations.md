# Nasiya M11 Known Limitations

Status: authoritative pre-implementation residual boundary for M11.
Baseline: M10 docs-only closeout
`17ebbe166d63a32e3b7eaa3eb3838f578d9b7780`.

These are intentional limits of the one M11 capability. They do not authorize
additional implementation.

## KL-M11-01 — Existing Accounts And Customers Only

M11 cannot create a user or customer and has no anonymous/public registration
or Telegram-link bootstrap. An authenticated user without an existing customer
is not eligible. Customer lead, shop-assisted onboarding and `shop_customer`
remain outside M11.

## KL-M11-02 — Telegram Is The Only Registration OTP Transport

Registration OTP uses the already-linked active Telegram chat and the existing
durable dispatcher. There is no SMS, email, Web Push, alternate delivery,
fallback transport, sync web send, broker or second dispatcher. Dispatcher
degradation leaves activation incomplete while existing web/password behavior
continues safely.

## KL-M11-03 — No OTP Key-Rotation Grace

LOGIN and REGISTRATION reuse the existing purpose-bound OTP HMAC key. Changing
that key invalidates outstanding challenges; there is no multi-key verification
or grace keyring. Raw or reversible OTP persistence is absent.

## KL-M11-04 — Activation Requires Exact Point-In-Time Evidence

An outstanding challenge becomes invalid when link generation, current offer
or acceptance, identity revision, current document, or object availability no
longer matches. M11 requires a new code; it has no automatic reissue, retry,
notification or recovery workflow.

## KL-M11-05 — Later Offer Changes Are Not Retroactive

Activation records that exact current evidence at verification time. A later
offer switch neither deactivates the customer nor requests re-consent. M11 does
not define deactivation, re-consent or legal-notification policy.

## KL-M11-06 — Customer Activation Is One-Way

M11 supports only `draft -> active`. It has no `active -> draft`, deactivation,
account closure, reactivation, correction, appeal or administrative override.
Downgrade refuses to run while active customers exist instead of rewriting
them.

## KL-M11-07 — Active Identity And Document Correction Is Absent

M10 identity/document mutation remains draft-only. After activation, M11 does
not add a correction flow, document renewal, approval, OCR/MRZ, biometric or
registry verification. Current evidence remains private under M10 rules.

## KL-M11-08 — Active Customers Cannot Ordinarily Unlink Telegram

To preserve the active-with-Telegram invariant, ordinary unlink is denied for
an active customer. Token-protected atomic relink remains available; M11 has no
deactivation-before-unlink or account-recovery alternative.

## KL-M11-09 — Session Rotation Is Current-Browser Only

First activation rotates the current authenticated session and CSRF secret.
Other sessions are deliberately preserved, as is `active_shop_id`. M11 does
not provide global logout, device trust, session risk scoring or a generic
session-management redesign.

## KL-M11-10 — Localization Is Feature-Local

Activation pages have UZ-Latn and RU copy with UZ-Latn fallback. There is no
persisted account-wide locale, generic i18n framework or required UZ-Cyrl UI.
The accepted legal-text language does not select the UI locale.

## KL-M11-11 — Audit Is Not A Separate Activation History Product

The PII-free customer row and exact central `customer.activated` event are the
only activation history evidence added. M11 has no activation table, audit UI,
export, analytics, support console or administrative search.

## KL-M11-12 — Rate Limits Use Existing Local Persistence

Registration rate limits reuse the existing PostgreSQL `auth_rate_limits`
table and HMAC keying. M11 adds no distributed counter, Redis, cross-region
coordination or provider-level abuse service.

## KL-M11-13 — Manual Acceptance Uses Synthetic Development Data

Real Chrome and development/test Telegram acceptance must use synthetic
identity and document data. M11 neither requires nor permits real PII,
production bot credentials in reports, production object storage or
destructive production testing.

## Explicit OUT Scope, Not Gaps To Fill

Debt, payment, rating, disclosure, notification, scheduler, public bootstrap,
lead, shop linkage, SMS/Web Push, OCR/biometric/registry, generic activation or
OTP infrastructure, new table/worker/dispatcher/broker and M12 product work are
OUT. Any need for them triggers the M11 stop condition.
