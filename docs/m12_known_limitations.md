# Nasiya M12 Known Limitations

Status: final-local limitations for M12; implementation and controlled local
acceptance are GREEN, while exact implementation-SHA remote CI and docs-only
closeout evidence remain pending. These are accepted limits, not missing work
that a later task may infer into M12.

## KL-M12-01 — Existing active customers only

M12 links only an existing auth-active User with an existing active Customer.
It cannot create a User, Customer, lead, account, draft, or onboarding record.
Public registration, shop-assisted PII capture, and customer conversion remain
separate future capabilities.

## KL-M12-02 — Bounded operational eligibility disclosure

An authorized current ShopStaff learns that an exact phone is link-eligible only
when a link succeeds. Invalid, missing, disabled, draft, missing/unlinked/
unverified TelegramLink, and inconsistent target states remain one generic
outcome. Exact-phone input, four HMAC rate buckets, masked roster, no preview,
and no global search bound this accepted disclosure.

## KL-M12-03 — No customer consent or relationship correction

M12 does not require a customer confirmation and does not provide self-unlink,
shop unlink, delete, transfer, archive, mistake correction, or history table.
A ShopCustomer is permanent until a separately authorized product decision
defines a safe correction lifecycle.

## KL-M12-04 — No phone, identity, document, or Telegram expansion

M12 has no phone mutation, account merge, Telegram bootstrap/relink/unlink,
contact verification, LOGIN/REGISTRATION OTP, M10 identity decrypt, name/
JSHSHIR/document view, document upload, object access, presign, or storage I/O.
Shop staff sees only a masked canonical phone and safe policy values.

## KL-M12-05 — Credit policy is debt-independent

M12 stores future policy inputs only. It creates no debt, payment, balance,
installment, exposure, overdue calculation, write-off, debt offer, acceptance,
or enforcement. `blacklisted` is a future debt-consumer signal; `whitelisted`
does not bypass any current or future hard gate by itself.

## KL-M12-06 — Defaults are prospective

Changing shop defaults does not alter existing links. A new link snapshots one
coherent locked default pair. There is no bulk propagation, historical policy
version, scheduled recalculation, or backfill beyond migration server defaults.

## KL-M12-07 — Customer own view is deliberately narrow

The own `/customer/shops` projection has linked shop names only. It exposes no
credit limit, max debt count, list status, staff, shop private phone/address,
other customer, audit, or relationship-management controls.

## KL-M12-08 — No global/admin CRM

Platform-admin without live shop membership has no M12 tenant authority. M12
adds no global customer list, search, export, report, support console, rating,
or cross-shop relationship history.

## KL-M12-09 — Local rate limiting only

Link lookup throttling reuses local PostgreSQL `auth_rate_limits` and the
existing HMAC key. M12 adds no Redis, cache, distributed counter, cross-region
coordination, provider abuse service, or new secret. The rate record is an
intentional separate write even if later domain validation fails.

## KL-M12-10 — Concurrency proof is PostgreSQL-bound

Correctness relies on the frozen row-lock order, unique constraint, narrow
expected-conflict savepoint, and deterministic real-PostgreSQL barriers. There
is no retry/sleep/timeout/NOWAIT/advisory-lock fallback and no SQLite evidence.

## KL-M12-11 — Localization is feature-local

M12 supplies UZ-Latn and RU feature-local presentation with safe fallback. It
does not create persisted locale preference, general i18n infrastructure, or a
new translation platform.

## KL-M12-12 — Manual acceptance is controlled development evidence

Manual Chrome/PostgreSQL checks use synthetic or operator-controlled data only,
rebuild/recreate the exact checkpoint image without deleting volumes, and report
only safe statuses/counts. Production PII, secrets, raw phones, UUIDs, tokens,
Telegram IDs, and destructive database repair are not accepted evidence.

The approved M12.54 correction assigns disabled-target and active-unverified
TelegramLink cases to the deterministic real-PostgreSQL integration matrix
because no approved UI/development provisioning flow can create those states.
Reachable eligible, draft, missing, tenant, role, platform-admin, suspension,
defaults, policy, stale, no-op, masked-roster, and own-view cases remain real
Chrome acceptance evidence. No direct database fixture was used.

## Explicit OUT scope, not a backlog shortcut

Debt/payment/rating/disclosure, notification/scheduler, public bootstrap,
lead/onboarding, customer consent OTP, global/admin PII, storage, identity or
document access, generic CRM/search/report, new table beyond `shop_customers`,
new runtime dependency, and new infrastructure remain OUT. Any need for one is
a formal M12 stop condition, not an implementation extension.
