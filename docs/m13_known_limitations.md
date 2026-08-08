# Nasiya M13 Known Limitations

Status: authoritative accepted M13 scope limitations; implemented and manually
verified locally through the seventh checkpoint. The eighth implementation
checkpoint and all remote evidence remain pending. These are frozen boundaries,
not work that a later M13 task may infer into scope.

## Local manual evidence

The exact seventh-checkpoint image was exercised with Google Chrome 150 and a
real PostgreSQL database using synthetic data only. The shop matrix covered all
three live roles, idempotent replay and conflict, limit/count/list gates,
wrong-tenant access, suspended and revoked authority, and safe list/detail
rendering. The customer matrix covered own legal views, accept replay, optional
reject reason, required cancel reason, wrong-customer access, current-offer
switch and refresh, suspended-shop decisions, exact reachable expiry, and a
single terminal winner. Safe aggregate counts confirmed one acceptance/audit
per successful accept and no payment, rating, notification, or scheduler
surface. Temporary browser profiles and session-bearing fixtures were removed
after the run and are not repository evidence.

## KL-M13-01 — Pending foundation, not a payment ledger

M13 creates pending and active Debt only. It has no payment method, partial
payment, receipt, void, balance, remaining amount, paid status, overdue,
clawback, write-off, settlement, or discount reversal. Pending and active
original amounts remain open exposure until a later payment capability lawfully
replaces the narrow exposure adapter.

## KL-M13-02 — No rating producer or disclosure

M13 consumes a locked-Customer-scoped global hard-block read port but creates no
rating score, band, event, override, farming control, hard-block mutation,
cross-shop list, or disclosure. Fake/injected projections are test seams, not
an authoritative rating system.

## KL-M13-03 — Expiry is domain capability, not a scheduler

Any pending action expires a debt at the exact boundary, and M13 exposes a
bounded batch-candidate service. It creates no cron, worker, `job_runs`, queue,
retry policy, monitoring UI, alert, or automatic process. Delayed batch
invocation cannot be represented as a timely notification guarantee.

## KL-M13-04 — Legal evidence is limited to one acceptance per debt

M13 reuses M9 OfferVersion/Text and extends existing OfferAcceptance. It does
not create a second legal table, signature workflow, document renderer,
legal-review process, offer edit/history beyond inherited M9, or customer
consent for unrelated capabilities. Registration acceptance remains independent
and byte-for-byte replay-compatible.

## KL-M13-05 — Tenant and customer views are deliberately narrow

Shop views are limited to the current Shop's debts for its linked customers.
Customer views are limited to own Customer debts. There is no platform-admin
debt/customer console, global search, export, report, impersonation, bulk
action, cross-shop history, or public debt API. Opaque locators never become
authority.

## KL-M13-06 — No customer/identity/Telegram/storage expansion

M13 requires existing active Customer and current self-phone-verified Telegram
state but does not create users/customers/leads, mutate phones, bootstrap/relink
Telegram, redesign OTP, decrypt PII, read identity/document contents, attach
objects, presign storage, or send a Telegram message. Raw phone, PII, offer
body, UA, reason, key, request hash, session, and CSRF material remain outside
safe projections.

## KL-M13-07 — Idempotency is one financial-create scope

The reusable key model has exactly one M13 endpoint consumer,
`shop.debts.create`. M13 does not add generic request middleware, distributed
idempotency, key purge, key rotation, cross-endpoint replay, or an external
key-service. A key is durable only for completed successful M13 create results;
failed eligibility and validation consume nothing.

## KL-M13-08 — Financial values are intentionally single-currency and immutable

Only whole UZS original amount, bounded discount basis points, server-calculated
discounted amount, and Tashkent business due date exist. There is no
multi-currency, installment schedule, interest/penalty, tax, product catalog,
price negotiation, debt amount edit, due-date edit, merge, transfer, delete,
reopen, or manual correction lifecycle.

## KL-M13-09 — PostgreSQL-only concurrency evidence

Correctness depends on the frozen row-lock order, parent serialization, exact
unique constraints, narrow expected-conflict savepoint, and deterministic real
PostgreSQL barriers. There is no SQLite evidence or fallback based on retry,
sleep, NOWAIT, SKIP LOCKED, lock timeout, advisory locks, cache, broker, or
distributed lock.

## KL-M13-10 — Localized web surface remains feature-local

M13 supplies only UZ-Latn/RU feature-local debt messages and templates with
CSRF, PRG, no-store, CSP, and autoescape. It does not create a general i18n
platform, persisted locale preference, API/JSON surface, browser-side financial
calculation, local/session storage, or inline JavaScript.

## KL-M13-11 — Manual acceptance is controlled evidence

Manual Chrome/PostgreSQL verification uses synthetic or operator-controlled
data and reports only safe statuses/counts. It never accepts production PII,
raw UUIDs, raw idempotency keys, request hashes, reasons, offer bodies,
credentials, Telegram identifiers, sessions, or destructive database repair as
evidence.

## Explicit OUT scope is not a backlog shortcut

Payment, rating, notification, scheduler process, reporting, public onboarding,
PII/storage, platform-admin debt operations, generic infrastructure, and any
new direct runtime dependency remain OUT. If M13 implementation requires one,
the task stops for a new product decision rather than expanding this milestone.
