# Nasiya M12 Decisions

Status: authoritative M12 decision log.
Authority: TT, M12 Final Scope Freeze, M12 Product Gate, then the three tracked
M12 executable documents. Product Owner disposition: `PO-M12-01..25 — 25/25
FINAL APPROVED`.

These decisions are closed. Later tasks implement only their assigned slice;
they may not reopen capability, privacy, transaction, lock, schema, or test
scope.

## Baseline decision

M12 starts at M11 docs-only closeout `7d8e14b2da2a77008cf3e999d77aabf277d72137`,
with M11 implementation `8741ffe7eeb710d05342b43473281d4a5f9c316b`, recovery
checkpoint `6818b55cd51778270853eb72d63b8bd45f3b9884`, remote run `31122632059`
(`3252 passed`, zero nonpass), docs run `31123113658` (success), and single
Alembic head `d2e3f4a5b6c7`. The TT blob is
`d77c0f0f330a1330155a4aee3c46b05d97cf5561`.

## Product Owner decisions — 25/25 FINAL

| ID | Binding repository consequence |
|---|---|
| PO-M12-01 | M12 is only the existing active Customer ↔ current Shop relationship and debt-independent credit-policy foundation. |
| PO-M12-02 | Active OWNER/MANAGER/CASHIER may face-to-face link by exact phone; no customer confirmation, debt, or legal consent is created. |
| PO-M12-03 | Client sends no target UUID authority; phone is transient; actor/current shop come from session and target follows User→Customer ownership. |
| PO-M12-04 | Target requires auth-active User, current active self-phone-verified TelegramLink, and `Customer(active)`; identity/document/offer rows are not opened. |
| PO-M12-05 | Invalid/missing/disabled/draft/missing-link/unverified/inconsistent target states share `CUSTOMER_LINK_UNAVAILABLE`. |
| PO-M12-06 | All roles read/link; OWNER defaults; OWNER/MANAGER per-link policy; platform-admin never substitutes for membership. |
| PO-M12-07 | Suspended shop retains role-scoped reads and denies every M12 mutation with `SHOP_SUSPENDED`. |
| PO-M12-08 | Persistence is exactly two `shops` default columns plus one `shop_customers` table; no event/history/rate/settings table. |
| PO-M12-09 | `UNIQUE(shop_id, customer_id)` gives idempotent existing-pair success, no reset/new row/duplicate audit, and exactly one concurrent insert/audit. |
| PO-M12-10 | Default credit limit is `1_000_000` UZS and default max open debts is `2`; every new link snapshots the locked pair. |
| PO-M12-11 | Money is whole `Decimal`/`NUMERIC(18,0)`, never float; limit `0..1_000_000_000_000`, max debts `1..100`. |
| PO-M12-12 | Defaults are prospective only; concurrent link sees a complete old or new pair, never a mixed pair. |
| PO-M12-13 | List state is exactly normal/whitelisted/blacklisted; blacklist is future debt input and whitelist bypasses no gate. |
| PO-M12-14 | Link revision starts at 1 and changes only for a real policy change; stale link/default submits require refresh; no-op changes no audit/revision/timestamp. |
| PO-M12-15 | A ShopCustomer is a permanent M12 tenant parent; unlink/delete/transfer/correction require a later product decision. |
| PO-M12-16 | Customer own view shows only linked shop names; no policy/list/staff/other customer/shop-private fields. |
| PO-M12-17 | Roster has masked canonical phone plus safe policy/list only; no M10 decrypt, name, JSHSHIR, document, or Telegram identifier. |
| PO-M12-18 | Every link attempt uses the existing HMAC rate table/key in the four frozen 900-second buckets; success never clears a bucket. |
| PO-M12-19 | Link has closed auth/CSRF, rate, and domain transactions; borrowed sessions are never committed, fully rolled back, or closed by repository/service code. |
| PO-M12-20 | The combined forward global order and M12 hot path in the scope contract are mandatory; retries, sleeps, timeouts, NOWAIT, and advisory locks are not correctness. |
| PO-M12-21 | Only the three frozen central audit events exist; payload excludes identifiers, phone, PII, session, and request metadata; no-op/replay emits none. |
| PO-M12-22 | Add three stable M12 errors and reuse frozen inherited errors without internal detail. |
| PO-M12-23 | Only the six frozen routes exist; tenant-scoped path locator is not authority; forms/redirects are no-store and identifier-safe. |
| PO-M12-24 | One real-PostgreSQL linear revision `e3f4a5b6c7d8` follows `d2e3f4a5b6c7`; no SQLite/create_all/manual DDL/sleep/skip/xfail/xpass. |
| PO-M12-25 | Historical guards stay meaningful by source-scoped reframing plus exact M12 guards; eight checkpoint history, exact CI, and docs-only closure are immutable. |

## Implementation decisions fixed by audits

- `get_detached_mutation_session_context` is reused for TX-A. M12 adds a
  detached current-shop context in the bounded shop-customer integration layer:
  it resolves the server-stored active shop inside TX-A but carries only trusted
  actor and shop identifiers into later phases. It carries no ORM instance,
  role, or status authority.
- TX-C locks Shop, actor ShopStaff, actor User, and all target Users in UUID
  ascending order. Target Link and Customer are rechecked after the User locks.
  Existing M5 multi-Staff mutation helpers are made same-class UUID-ascending
  where a path locks more than one staff row; their Shop-first semantics remain.
- Candidate discovery may return only an internal trusted User ID. A new
  target-active Customer resolver is separate from own-customer helpers; shop
  staff never gains cross-customer authority by reusing an own-user API.
- The inherited limiter blocks at its threshold. M12 uses the existing
  `allowed_attempts + 1` adapter used by M11 registration so the configured
  values mean the stated number of allowed attempts.
- Invalid phone input still receives actor/shop/IP rate treatment. Its
  transient submitted value may form the phone-bucket HMAC input but is never
  persisted or exposed; valid input uses canonical phone.
- Historical guards that pin M11 table/head inventory are not deleted or
  weakened. They become historical-source assertions in the same change that
  adds exact M12 metadata/head/cleanup assertions.

## Stable outcome decisions

`CUSTOMER_LINK_UNAVAILABLE` is indistinguishable across all target failures.
`SHOP_CUSTOMER_UNAVAILABLE` is indistinguishable for missing and cross-tenant
per-link locators. `SHOP_CUSTOMER_CHANGED` means an optimistic stale form;
refresh is required. Role/membership denial is `FORBIDDEN`; active-shop write
denial is `SHOP_SUSPENDED`; prephase denial is `RATE_LIMITED`; bad policy input
is `VALIDATION_ERROR`. No stable public result includes a raw phone, UUID,
constraint, SQL, provider, or state-gate detail.

## Closure decisions

The eight implementation commit subjects, exact order, and the one docs-only
closeout subject are recorded in `docs/m12_scope_contract.md`. Push occurs only
at M12.59; M12.60 modifies only final evidence/limitations/result documents
after exact implementation-SHA CI success. No amend, force push, rebase, or
squash changes checkpoint evidence.
