# Nasiya M14 Decisions

This is the fixed decision record produced by M14.01–07. It interprets the
frozen M14 scope; it does not broaden or amend it.

## Baseline decision

M14 begins at `b12a8b23335a5aad6290b8ca96007decd59cb4d1` / Alembic
`f4a5b6c7d8e`, with the M13 3,643-pass green evidence recorded in
[`m14_scope_contract.md`](m14_scope_contract.md). All implementation must
preserve the M13 contract surface unless this document names the bounded M14
extension.

## Product Owner decisions — final

1. Payment means append-only whole-UZS repayment of an active, on-time Debt.
2. Partial payment leaves the Debt active; exact remaining payment marks it paid.
3. Payment creates one immutable receipt/history record, not a printable fiscal document.
4. Only `cash`, `card`, `transfer`, and `other` are M14 methods.
5. The live current Shop session establishes tenancy; client IDs never do.
6. OWNER, MANAGER, and CASHIER create/read for their active Shop; suspended Shop is read-only.
7. Customer reads are server-resolved ownership only and remain read-only.
8. Target Customer active/Telegram/rating/credit policy is not a repayment gate.
9. Server time captured after Debt lock determines inclusive Tashkent due-date payability.
10. A past-due active Debt is denied as `DEBT_NOT_PAYABLE`, not transitioned or paid late.
11. Decimal `NUMERIC(18,0)` and strict ASCII integer parsing are mandatory.
12. Balance and exposure are ledger formulas, never persisted cache columns.
13. Original amount drives exposure; discounted amount drives repayment balance.
14. One Payment carries the next unique Debt revision; a Debt lock serializes all appends.
15. Existing idempotency storage is extended only by typed `payment` result support.
16. Replay happens before current target status validation, but only after current actor/shop/result visibility.
17. Same key/different request conflicts; stale different key is `DEBT_CHANGED` and consumes no key.
18. Audit is exactly `payment.recorded`, with conditional `debt.paid`; no event/outbox abstraction.
19. A single reversible-only-on-empty migration follows Alembic `f4a5b6c7d8e`.
20. ShopCustomer is the shared predecessor lock for repayment and new-Debt exposure.
21. Existing M13 debt code remains payment-import-free; a narrow injected read port/adapter carries payment-aware exposure.
22. All M14 pages are SSR with CSRF + PRG; no API, admin, customer self-pay, or browser financial state.
23. Absence/foreign payment access is generic `PAYMENT_UNAVAILABLE`.
24. Void/refund/correction, overdue/written-off, notifications, rating, scheduler, and gateway work are deferred.
25. M14 is a foundation milestone, not completion of the collections lifecycle or MVP.

## Implementation decisions fixed by audits

- The new package is `app/payment/`; it owns payment persistence, payment-aware
  aggregate/read projections, service policy, targeting, HTML presentation, and
  routes. It is not a generic financial platform.
- `app/debt/models.py:Debt` and `app/debt/enums.py:DebtStatus` are extended for
  six persisted statuses and `paid_at`; `app/debt/contracts.py:DebtAggregate`
  and `DebtProjection` gain only data required for the M14 transition/read
  contract.
- `app/idempotency/contracts.py` and repository result accessors become result-
  type-aware without changing the established debt create request hash/replay.
- `app/audit/contracts.py`, model/repository/redaction seams gain only the two
  declared events and safe payload handling; no broad dispatch mechanism.
- `app/debt/business_time.py:tashkent_business_date` is reused/extended; no
  second timezone helper is allowed. `app/debt/values.py` is the strict Decimal
  parser pattern to reuse rather than replace.
- `app/payment/repository.py` supplies the payment-aware exposure/open-set
  adapter used by the M13 creation-eligibility seam. `app.debt` must not import
  `app.payment`; `tests/test_m13_contract_scope.py` remains a source-scoped
  guard and may not be removed or weakened.
- The payment mutation has a short auth/CSRF TX-A and one transaction-owning
  route-coordinated TX-B. A repository or service never owns the transaction.
- The existing named idempotency unique-conflict savepoint is the only expected
  savepoint. New generic retry, locking infrastructure, or lock hints are not
  authorised.

## Stable outcome decisions

The required schema constraints, formulae, global lock order, exact routes and
error vocabulary are normative in [`m14_scope_contract.md`](m14_scope_contract.md).
The placement and symbol-level append proof is normative in
[`m14_repository_map.md`](m14_repository_map.md). A later task may refine an
internal name only if it preserves all three documents and updates them in the
same change; it may not invent functionality outside them.

## Closure decision

M14.08 creates tracked repository authority only. It neither declares M14
implemented nor substitutes docs/static checks for product, PostgreSQL,
browser, migration, and remote-CI evidence required by later tasks.
