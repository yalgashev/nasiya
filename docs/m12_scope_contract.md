# Nasiya M12 Scope Contract

Status: authoritative M12 repository scope; implementation has not started.
Capability: **Shop–Customer Relationship & Credit Policy Foundation**.
Product Owner disposition: `PO-M12-01..25 — 25/25 FINAL APPROVED`.

This document, `docs/m12_decisions.md`, and
`docs/m12_repository_map.md` are the executable authority for every M12 task
after M12.08. A required deviation is a stop condition, not permission to infer
another product capability.

## Authority and exact baseline

Conflicts are resolved in this order:

1. `docs/tt_nasiya_web_v1.md`;
2. `/home/yalgashev/projects/nasiya_m12_00_final_scope_freeze.md`;
3. `/home/yalgashev/projects/nasiya_m12_product_gate.md`;
4. this contract, `docs/m12_decisions.md`, and
   `docs/m12_repository_map.md`;
5. M11 closeout and CR-M11 contracts;
6. inherited M2–M10 closed contracts;
7. repository implementation and tests as integration evidence.

| Evidence | Exact value |
|---|---|
| M11 implementation | `8741ffe7eeb710d05342b43473281d4a5f9c316b` |
| M11 recovery checkpoint | `6818b55cd51778270853eb72d63b8bd45f3b9884` |
| M11 checkpoints | `8/8`, intact ancestry |
| M11 implementation CI | run `31122632059`, job `92686276933`, success; `3252 passed` and zero nonpass |
| M11 docs-only closeout / current M12 parent | `7d8e14b2da2a77008cf3e999d77aabf277d72137` (`docs: close M11 remote evidence`) |
| M11 docs CI | run `31123113658`, success |
| Alembic current/head before M12 | `d2e3f4a5b6c7` |
| TT tracked blob | `d77c0f0f330a1330155a4aee3c46b05d97cf5561` |
| M12.01 verification | clean/synced `main`, divergence `0 0`, no M12 product code/schema/migration |

No M11 checkpoint is amended, rebased, squashed, relabelled, or otherwise
rewritten. The protected TT, M12 Product Gate, Final Scope Freeze, and planning
guide are not edited by M12 implementation tasks.

## One capability

An authenticated, auth-active current ShopStaff may link an existing active
customer to that current shop by an exact canonical phone. The server, never the
client, resolves the target User, its current self-phone-verified TelegramLink,
and its active Customer. A first link snapshots the locked shop's credit-policy
defaults into one tenant-owned `ShopCustomer`; an existing pair is idempotent
and untouched. OWNER changes future-link defaults; OWNER or MANAGER changes an
existing per-link policy with optimistic revision control.

The relationship is a future debt parent and policy foundation only. M12 creates
no debt, payment, balance, exposure, installment, rating, consent, or legal
state.

## Exact IN scope

- Existing auth-active User resolution by an exact canonical phone input.
- Current, active, self-phone-verified TelegramLink and active Customer live
  rechecks.
- Server-derived current shop actor, membership, role, and shop-status
  revalidation in the domain transaction.
- Exactly two new credit-policy columns on `shops` and exactly one new
  `shop_customers` table.
- Unique, sequentially and concurrently idempotent `(shop_id, customer_id)`
  link creation with a coherent default snapshot.
- OWNER future-link defaults; OWNER/MANAGER per-link credit limit,
  max-open-debts, and `normal|whitelisted|blacklisted` updates.
- Shop roster with masked phone and safe policy projection; customer own linked
  shop-name view only.
- HMAC-keyed PostgreSQL rate prephase, central typed audit, stable localized
  errors, CSRF/PRG/no-store/CSP/autoescape/mobile accessibility.
- One linear Alembic revision, real-PostgreSQL migration/integration/barrier
  tests, manual Chrome/PostgreSQL acceptance, exact-SHA CI, and docs-only
  remote closeout.

## Exact OUT scope

M12 must not create public/anonymous registration, User/Customer creation,
lead/onboarding, phone mutation or merge, activation/deactivation, Telegram
bootstrap/relink/unlink/contact-verification redesign, LOGIN/REGISTRATION OTP,
customer approval or invite, link deletion/transfer/history, debt/payment/
installment/exposure, rating, disclosure, M10 identity/document decrypt or
access, storage I/O, mute/notification/scheduler, admin-global customer or PII
UI, generic CRM/search/report platform, worker/dispatcher/broker/cache/advisory
lock framework, or a new direct runtime dependency.

`mute` is explicitly deferred even though TT conceptually mentions it. A local
list status is not a global rating or a debt enforcement mechanism.

## Authority, roles, privacy, and eligibility

### Server-derived authority

The link form accepts an exact phone only. It never accepts a client
shop/customer/user/TelegramLink UUID as authority. Current shop is derived from
the authenticated session's stored `active_shop_id` and live membership; the
domain transaction locks and rechecks Shop, actor ShopStaff, and actor User.
The platform-admin bit alone has no shop authority.

| Condition | Roster | Link | Defaults | Per-link policy |
|---|---:|---:|---:|---:|
| active OWNER | allow | allow | allow | allow |
| active MANAGER | allow | allow | deny | allow |
| active CASHIER | allow | allow | deny | deny |
| revoked/no membership | deny | deny | deny | deny |
| platform-admin without membership | deny | deny | deny | deny |
| suspended shop with valid role | allow | deny | deny | deny |
| other-shop locator | unavailable | unavailable | unavailable | unavailable |

### Target chain and generic outcome

1. Normalize the transient form phone with `normalize_uzbekistan_phone`.
2. Perform non-locking discovery of a candidate User ID without a public result.
3. In the domain transaction lock actor and target User rows in UUID ascending
   order, then recheck target `is_active` and exact stored canonical phone.
4. Lock the target's TelegramLink and apply
   `is_otp_eligible_telegram_link` with the target user ID.
5. Lock the target Customer through a dedicated target-active resolver and
   require `onboarding_status == active`.
6. Lock/create the current-shop `(shop_id, customer_id)` relation.

Invalid phone, missing User/Customer, disabled User, draft Customer, missing,
unlinked, unverified, stale, or inconsistent TelegramLink all return the same
`CUSTOMER_LINK_UNAVAILABLE` public outcome. Those failures create no
ShopCustomer, audit, Customer, TelegramLink, policy, or AuthSession mutation.
The separate rate-attempt write is the only intentional exception.

Raw phone is transient and may be an HMAC input only. It is absent from URL,
flash, HTML after POST, audit, log, error, report, repr, and browser storage.
The roster reuses `mask_phone_for_display`; it exposes no M10 identity,
document, ciphertext, blind index, or object metadata. Successful link is the
accepted bounded face-to-face eligibility disclosure to authorized staff.

## Persistence and value contract

M12's only schema revision is `e3f4a5b6c7d8`, parent
`d2e3f4a5b6c7`. It adds the following and nothing else:

| Parent/table | Exact addition |
|---|---|
| `shops` | `default_credit_limit_uzs NUMERIC(18,0) NOT NULL DEFAULT 1000000`, bounded `0..1000000000000` |
| `shops` | `default_max_open_debts SMALLINT NOT NULL DEFAULT 2`, bounded `1..100` |
| `shop_customers` | UUID id; `shop_id`, `customer_id`, `created_by_user_id` RESTRICT FKs; `credit_limit_uzs`, `max_open_debts`, `list_status`, `revision`, timestamps |

`shop_customers` has `UNIQUE(shop_id, customer_id)`, named checks for whole
bounded policy values, list status, positive revision, and timestamp order;
indexes are `(shop_id, created_at, id)` and `(customer_id, created_at, id)`.
It contains no phone, name, JSHSHIR, document, Telegram identifier, identity or
object metadata, debt value, mute, deletion, unlink, or free-text reason.

Money is `Decimal` / `NUMERIC(18,0)` only. HTML accepts ASCII base-10 whole UZS
without separators, fractions, float, or scientific notation. Credit limit is
inclusive `0..1_000_000_000_000`; max open debts is inclusive `1..100`.
`list_status` is exactly `normal`, `whitelisted`, or `blacklisted`. Revision
starts at 1 and increases only after a real policy change.

New links snapshot the complete locked default pair. Later defaults affect only
future links; an existing relation is never reset or bulk-updated. Existing
`Shop.updated_at` is the default-form stale token; a no-op default update leaves
timestamp and audit unchanged. Per-link stale submission returns
`SHOP_CUSTOMER_CHANGED`; a no-op leaves revision and audit unchanged.

Upgrade adds bounded defaults, creates the one table and indexes, then extends
central-audit constraints atomically, leaving zero initial link rows. Downgrade
fails closed before destructive DDL unless `shop_customers` is empty and every
shop default remains `1000000` and `2`.

## Transaction and lock contract

Link POST has three closed phases:

```text
TX-A: authenticate, touch session, resolve current shop, validate CSRF; commit/close
TX-B: HMAC rate check-and-record; commit/close
TX-C: locks, live rechecks, link/default snapshot, audit; route/coordinator owns commit/rollback
```

Policy/default POST uses closed TX-A followed by one domain transaction. No ORM
instance crosses a phase. Repositories and services never call `commit()`, full
`rollback()`, or `close()` on a borrowed Session; an expected uniqueness race may
use only a narrow nested savepoint. No external network or storage I/O belongs
to M12.

The forward total lock order is:

```text
Shop -> ShopStaff -> TelegramLinkToken -> OtpDispatch -> OtpChallenge -> User
-> TelegramLink -> Customer -> OfferVersion -> OfferAcceptance
-> CustomerIdentity -> ObjectFile -> CustomerDocument -> ShopCustomer
-> AuthSession
```

Unused classes are skipped; same-class rows are UUID ascending. The M12 link
hot path is `Shop -> actor ShopStaff -> actor+target User (UUID ascending) ->
target TelegramLink -> target Customer -> ShopCustomer`. Policy/default paths
are `Shop -> actor ShopStaff -> [ShopCustomer]`. AuthSession and rate locks are
closed prephases and never overlap the domain transaction. Retry, sleep, NOWAIT,
lock timeout, and advisory locks are not correctness mechanisms.

## Rate, audit, error, and web contract

The existing `AuthRateLimit` table and `rate_limit_hmac_key` are reused. Every
link attempt is checked and recorded before target discovery under a 900-second
window in these exact scopes and allowed-attempt settings:

| Scope | Setting | Allowed attempts |
|---|---|---:|
| `shop_customer_link_actor` | `shop_customer_link_rate_limit_actor_attempts` | 30 |
| `shop_customer_link_shop` | `shop_customer_link_rate_limit_shop_attempts` | 100 |
| `shop_customer_link_phone` | `shop_customer_link_rate_limit_phone_attempts` | 5 |
| `shop_customer_link_ip` | `shop_customer_link_rate_limit_ip_attempts` | 200 |

`shop_customer_link_rate_limit_window_seconds` is 900. Actor/shop UUID, phone,
and trusted IP are HMAC inputs only. M12 uses the inherited `allowed_attempts +
1` adapter around the limiter's threshold semantics, so the configured number
of attempts is allowed. Success never clears a bucket. A blocked bucket returns
`RATE_LIMITED` and opens no domain transaction.

Only these central audit events are added: `shop_customer.linked`,
`shop_customer.policy_updated`, and `shop.customer_defaults_updated`. Their
payload contains only outcome and bounded policy values/revisions; typed actor
and object columns remain the only identifier location. Audit failure rolls back
the enclosing domain mutation. No-op or idempotent replay emits no event.

M12 adds exactly `CUSTOMER_LINK_UNAVAILABLE`, `SHOP_CUSTOMER_UNAVAILABLE`, and
`SHOP_CUSTOMER_CHANGED`; existing `FORBIDDEN`, `SHOP_SUSPENDED`,
`RATE_LIMITED`, and `VALIDATION_ERROR` are reused. UZ-Latn and RU text is
feature-local and cannot disclose a target gate, raw identifier, phone, SQL, or
provider detail.

| Route | Authority and projection |
|---|---|
| `GET /shop/customers` | current active staff; suspended read allowed; masked roster and role-safe forms |
| `POST /shop/customers/link` | active OWNER/MANAGER/CASHIER; exact-phone idempotent link |
| `POST /shop/customers/{shop_customer_id}/policy` | active OWNER/MANAGER; tenant-scoped locator only |
| `GET /shop/settings/credit` | current staff; owner edit controls only |
| `POST /shop/settings/credit` | active OWNER only |
| `GET /customer/shops` | own authenticated active Customer; linked shop names only |

Forms use CSRF and only safe hidden revision/timestamp values. Phone and policy
input never enter URL/query/flash and phone is blank after PRG. Roster ordering
is stable `(created_at,id)` with page size 50; no autocomplete, partial search,
or preview exists. Every response, error, and redirect is `Cache-Control:
no-store`, with inherited CSP and Jinja autoescape; no browser storage or inline
JavaScript is introduced.

## Required evidence and closure

Automated evidence uses real PostgreSQL for schema, migration, repository, and
barrier concurrency tests; Telegram transport remains injected fake only where
inherited tests require it. Tests cover exact metadata/constraints/RESTRICT
FKs, upgrade/walk/guarded downgrade, tenant predicates, eligible/ineligible
target matrix, idempotency one-row/one-audit, default snapshot coherence,
revision stale winner, suspend/revoke/activation/relink races, audit rollback,
CSRF/PRG/no-store/CSP/XSS/mobile, rate persistence, no decrypt, and no
debt/activation/Telegram/storage mutation. Static guards ban SQLite,
`create_all`, manual DDL, skipped/xfail/xpass tests, dependency/process growth,
session overlap, lock inversion, and historical-guard weakening.

M12 implementation commits, in order, are:

1. `M12: freeze shop customer scope`
2. `M12: add shop customer contracts`
3. `M12: add shop customer persistence`
4. `M12: add active customer linking`
5. `M12: add customer credit policy`
6. `M12: expose shop customer web flows`
7. `M12: harden tenant customer security and concurrency`
8. `M12: complete shop customer foundation`

After exact implementation-SHA CI success, the only docs-only closeout subject
is `docs: close M12 remote evidence`. Manual tasks M12.54 and M12.55 use only
synthetic/operator-controlled data and safe status/count evidence. M12 closes
only after all eight checkpoints, single head `e3f4a5b6c7d8`, zero-nonpass local
and remote evidence, manual acceptance, docs-only closeout, clean/synced main,
and no M13 code.
