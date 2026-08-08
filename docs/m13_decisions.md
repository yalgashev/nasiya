# Nasiya M13 Decisions

Status: authoritative M13 decision log; implementation has not started.
Authority: TT, M13 Final Scope Freeze, M13 Product Gate, then the three tracked
M13 executable documents. Product Owner disposition: `PO-M13-01..25 — 25/25
FINAL APPROVED`.

These decisions are closed. Later tasks implement only their assigned slice;
they may not reopen capability, tenant authority, privacy, transaction, lock,
schema, or test scope.

## Baseline decision

M13 starts at M12 docs-only closeout `7eb138571b1e990b87b6810a05524ad32986bbab`,
after exact remote-tested tree `d9c80a272a1df86c5aefa1a7e0ff81e68e65c13d`,
checkpoint `4a36e96c887c5bda51317a80a13d5aeda9384278`, CI run/job
`31238158808/93054450292` (`3472 passed`, zero nonpass), and single Alembic
head `e3f4a5b6c7d8`. The TT blob is `d77c0f0f330a1330155a4aee3c46b05d97cf5561`.
M13.01–07 read-only gates passed before these documents were created.

## Product Owner decisions — 25/25 FINAL

| ID | Binding repository consequence |
|---|---|
| PO-M13-01 | M13 is only tenant-scoped pending debt proposal and legal decision foundation. |
| PO-M13-02 | Runtime writes only create-pending and pending-to-active/rejected/cancelled/expired. |
| PO-M13-03 | Domain recognizes TT future statuses, but M13 persistence/service/router writes only five M13 statuses. |
| PO-M13-04 | Active OWNER, MANAGER, and CASHIER create and reason-cancel pending debt. |
| PO-M13-05 | Platform-admin never replaces live membership; no global debt console/cancel exists. |
| PO-M13-06 | Client IDs are locators, never authority; Shop comes from session and ShopCustomer is tenant-scoped. |
| PO-M13-07 | Customer reads/decides only server-resolved own active Customer debts. |
| PO-M13-08 | Original amount is whole UZS `1..1_000_000_000_000`; float/fraction/scientific inputs are rejected. |
| PO-M13-09 | Discount is `0..100.00%`, persisted `0..10000` bps, server-calculated `ROUND_HALF_UP`, minimum result one UZS. |
| PO-M13-10 | Due date is Asia/Tashkent business DATE and is not before expiry's business date. |
| PO-M13-11 | Pending expiry is exact `created_at + 72h`; `now >= expiry` wins every pending action. |
| PO-M13-12 | Pending/active open exposure is full original amount until a future payment port replaces it. |
| PO-M13-13 | Blacklist denies; whitelist bypasses no policy, limit, legal, or lifecycle gate. |
| PO-M13-14 | Create reads live policy/exposure/count under ShopCustomer lock; acceptance adds neither new exposure nor count. |
| PO-M13-15 | Acceptance rechecks active Shop/Customer/User, verified Telegram, local/global blocks, unexpired debt, and exact current legal offer. |
| PO-M13-16 | M9 current-offer semantics are reused; Debt stores no offer body/snapshot at create, only acceptance stores legal evidence. |
| PO-M13-17 | Exactly one immutable debt acceptance per Debt; registration evidence/replay remains unchanged. |
| PO-M13-18 | Financial create requires server-issued form key or canonical header UUID; raw key is not stored. |
| PO-M13-19 | Same actor/endpoint/key and canonical request replays; differing request conflicts; failures do not consume key. |
| PO-M13-20 | Completed key result, pre-generated Debt, and created audit are one transaction; M13 never purges keys. |
| PO-M13-21 | Reject reason is optional; cancel reason is required; bounded raw reason never leaks to audit/log/error/repr. |
| PO-M13-22 | Suspended Shop denies create/accept/cancel; own customer reject and time-driven expiry remain allowed. |
| PO-M13-23 | M13 supplies inline and bounded batch-candidate expiry service, not a scheduler process/job/worker/retry UI. |
| PO-M13-24 | Only five typed debt audits exist; replay/no-op creates no duplicate audit. |
| PO-M13-25 | One linear real-PostgreSQL migration, deterministic barriers, eight checkpoints, exact-SHA CI, and docs-only closeout are mandatory. |

## Implementation decisions fixed by M13.01–07 audits

- M13 starts its mutable route with a closed detached auth/session/CSRF phase,
  then owns one domain transaction. No ORM object crosses phases.
- Debt create discovers only an internal tenant candidate, then locks Shop,
  Staff, actor/target User rows in UUID order, TelegramLink, Customer,
  OfferVersion/current text, and ShopCustomer before policy/open-debt checks.
  The locked ShopCustomer must be verified to belong to the locked Customer.
- The existing `lock_shop_customer_by_tenant_locator` is not by itself a
  Customer-predecessor token. M13 adds debt-specific predecessor validation
  instead of treating a path locator as customer authority.
- M13's debt-offer resolver locks both current OfferVersion and selected
  current OfferText in the Offer stage. It does not weaken the M9
  registration-only resolver or contract.
- Debt accept never locks an existing OfferAcceptance after ShopCustomer/Debt.
  It locks/revalidates Debt, inserts a new acceptance only while debt is still
  pending, then transitions debt. Active replay reads the Debt projection. If
  recovery is required, only the named debt-only acceptance index may converge
  to that projection; every other integrity error is raised.
- A new IdempotencyKey is inserted after ShopCustomer and immediately before
  pre-generated Debt insert. Expected conflict recovery is only the named key
  unique constraint in a nested savepoint; it compares a canonical hash and
  never converts an unexpected integrity error into replay.
- Every Debt FK target is already locked by the actor's transaction. The
  acceptance append has only self-held Debt and earlier User/Offer FK checks;
  its debt uniqueness is serialized by the Debt lock. No inverse M9/M11/M12
  path was found.
- `DebtlessShopCustomerPolicyProjection` is the M12 policy input. M13 adds its
  own SQL adapter after its locked parent and does not turn M12 into a generic
  debt or rating module.
- Global hard block is a read-only, locked-Customer-scoped port. M13 creates no
  score, event, flag, disclosure, table, or cross-shop UI; fake projections
  prove consumption until a later authoritative producer exists.
- Feature-local value modules use stdlib `Decimal`, `hashlib`, `uuid`, and
  `zoneinfo`. No new secret or direct runtime dependency is required.

## Stable outcome decisions

`DEBT_UNAVAILABLE` converges missing, cross-tenant, and non-owned debt
locators. `CUSTOMER_NOT_ACTIVE`, `CUSTOMER_BLACKLISTED`, and
`CUSTOMER_RATING_BLOCKED` are specific safe gates; `CREDIT_LIMIT_EXCEEDED` and
`MAX_OPEN_DEBTS` are safe policy results. Pending-terminal mismatch returns
`DEBT_NOT_PENDING`; exact expiry returns `DEBT_EXPIRED`; same key/different
request returns `IDEMPOTENCY_CONFLICT`.

No result, audit, log, flash, repr, URL, or browser storage may disclose raw
key/request hash/reason/UA/offer body/phone/PII/internal identifier/session/
CSRF/SQL/provider detail. Failed validation, eligibility, policy, offer, key,
and terminal-gate paths create no Debt, key, acceptance, or audit.

## Closure decisions

The eight M13 implementation checkpoint subjects and final docs-only subject
are frozen in `docs/m13_scope_contract.md`. M13.09 may create the first
checkpoint commit only after its audit is green; push begins only at M13.72.
No amend, force push, rebase, or squash may rewrite checkpoint evidence.
