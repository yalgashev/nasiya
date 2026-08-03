# Nasiya M11 Known Limitations

Status: authoritative CR-M11-03 recovery-amended residual boundary for M11.
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

Registration OTP uses an active self-phone-verified Telegram generation and
the existing durable dispatcher. A legacy/chat-control-only link is not an OTP
transport. There is no SMS, email, Web Push, alternate delivery,
fallback transport, sync web send, broker or second dispatcher. Dispatcher
degradation leaves activation incomplete while existing web/password behavior
continues safely.

## KL-M11-03 — No OTP Key-Rotation Grace

LOGIN and REGISTRATION reuse the existing purpose-bound OTP HMAC key. Changing
that key invalidates outstanding challenges; there is no multi-key verification
or grace keyring. Raw or reversible OTP persistence is absent.

## KL-M11-04 — Activation Requires Exact Point-In-Time Evidence

An outstanding challenge becomes invalid when verified link generation,
including same-phone re-verification, current offer
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

To preserve the active-with-verified-Telegram invariant, ordinary unlink is
denied for an active customer. Token-protected atomic relink/re-verification
requires matching self-contact and remains available; a mismatch preserves the
old verified link. M11 has no deactivation-before-unlink or alternate account-
recovery path.

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

Real Chrome and development/test Telegram acceptance uses synthetic identity
and document data plus a tester-controlled Nasiya phone and Telegram account
with the same number. That controlled phone is operation-only sensitive test
data and never report evidence. Random-phone fixtures cannot be activated with
an unrelated Telegram account. M11 neither requires nor permits production
PII, bot credentials in reports, production object storage or destructive
production testing.

## KL-M11-14 — Legacy Links Require Explicit Re-Verification

Every pre-recovery link remains visible but receives
`phone_verified_at = NULL`. It cannot issue or receive LOGIN/REGISTRATION OTP
and cannot activate a customer until the user completes the approved self-
contact flow. There is no administrative/shop bypass, fabricated backfill or
automatic trust upgrade. Users without the approved authenticated recovery
entry path require a later explicitly authorized support policy.

## KL-M11-15 — Contact Evidence Is Deliberately Minimal

Pending verification stores only a domain-separated binding MAC and request
timestamp. The raw Telegram phone, pending chat ID and pending sender ID are
not retained. A verified link stores only its existing chat relation and one
generation timestamp. M11 adds no contact history, phone digest, Telegram
profile, identity registry, support search or arbitrary bot-keyboard platform.

## KL-M11-16 — Recovery Downgrade Requires Explicit Cleanup

The CR-M11-02 revision refuses downgrade while a pending binding or verified
link exists. It never clears verification silently or marks legacy links
verified. The original M11 active-customer downgrade guard remains separate;
both guards require explicit safe operator cleanup before schema reversal.

## KL-M11-17 — Phone Mutation Remains Deferred

The current product has no `User.phone` mutation flow. Any future explicitly
authorized phone-change capability must atomically invalidate the current
phone-verified Telegram generation and stale every outstanding LOGIN and
REGISTRATION OTP under the FINAL global order. This recovery does not create a
phone-change route, support override, automatic relink or administrative
bypass.

## Explicit OUT Scope, Not Gaps To Fill

Debt, payment, rating, disclosure, notification, scheduler, public bootstrap,
lead, shop linkage, SMS/Web Push, OCR/biometric/registry, generic activation or
OTP/contact infrastructure, arbitrary reply markup, raw pending Telegram
identity persistence, new table/worker/dispatcher/broker and M12 product work
are OUT. Any need for them triggers the M11 stop condition.
