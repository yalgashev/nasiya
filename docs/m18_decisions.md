# Nasiya M18 Decisions

## Authority evidence

M18.01–05 passed read-only. The protected M17 docs baseline is
`d341edf95511653d566726826304a74b3b3ffb60` /
`aedd8ef31a66e1bd15481e2f5079506f2bde61df`, clean and synced `0 0`, with
head `d8e9f0a1b2c3`. M17 implementation is
`5da0ee8d24e0f68f1597e4c96c66237909f8676c` /
`dbee3dfbe8663a8b1098d932b91325ae9821b35a`.

TT blob/SHA-256 is `d77c0f0f330a1330155a4aee3c46b05d97cf5561` /
`569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab`.
Product Gate, Scope Freeze, and microtask-guide SHA-256 values are respectively
`0b1c4b8135678ffd95db4249307dad8a11dfeccc28f1bcd5fca64ee2f43b03cc`,
`d676ba09836306f6fee6e007a1b4dd91e394e239edf7356013f26078ec46e5d6`, and
`4a77a4ee3c6d959383b6a0e185ac899e4d1fbada9d2dc477b9eed1f72e1cafed`.

## Product Owner decisions — final

| ID | Frozen decision |
| --- | --- |
| PO-M18-01 | Void authority is only an active Shop's live `OWNER|MANAGER`; CASHIER, Customer, platform-admin, and another Shop membership are not substitutes. |
| PO-M18-02 | Only the latest non-voided Payment is voidable; a Payment is voided at most once. |
| PO-M18-03 | Reasons are exactly `duplicate_payment|incorrect_amount|incorrect_method|payment_not_received|wrong_debt`; no free text or PII. |
| PO-M18-04 | Payment is immutable; one append-only `payment_voids` table records void facts. |
| PO-M18-05 | Partial void preserves status; terminal paid void becomes active or overdue, and terminal written-off-settled void becomes written-off. |
| PO-M18-06 | Only live `on_time_paid_voided=-5` and `written_off_settled_voided=-10` compensate exact source positives; new lawful source revisions may re-earn while +5 daily cap remains. |
| PO-M18-07 | Shop sees void status/time/reason label; Customer sees status/time only; actor, reason, raw rating, and other-Shop facts remain private. |
| PO-M18-08 | Refund/payout, unvoid, edit/delete, correction/forgiveness, override/settings, scheduler/job, notification, report/export, bulk/search/admin/API, and M19 code are OUT. |

## Implementation decisions fixed by M18.01–05

| ID | Frozen implementation decision |
| --- | --- |
| M18-D01 | Latest is the maximum non-voided `Payment.debt_revision_after` under the Debt lock. UUID and time never determine latest. |
| M18-D02 | Current total anti-joins PaymentVoid; as-of revision R includes payment revision `<= R` and excludes a void revision `<= R`. |
| M18-D03 | Every success increments Debt revision exactly once and uses one trusted UTC void instant; partial same-status void does not fabricate markers. |
| M18-D04 | Leaving paid clears only `paid_at`; leaving written-off-settled clears only current settlement marker pair. Earlier lifecycle evidence stays immutable. |
| M18-D05 | Paid-after-due terminal void atomically uses `DebtOverdueSource.PAYMENT_VOID`, `-15`, and canonical SYSTEM overdue/clawback audit names; only overdue audit receives `from_status=paid`. |
| M18-D06 | `source_revision` is positive for every RatingEvent. Existing events receive an explicit deterministic metadata backfill only; ambiguity is an upgrade error. |
| M18-D07 | `+5/-5` and `+10/-10` share their terminal Payment revision. No compensation occurs without its exact source benefit; `-15/-40` are never reversed. |
| M18-D08 | Fold order is `(occurred_at, debt_id, event_type, source_revision)` and clamp is applied after every event, not by SQL final sum. |
| M18-D09 | A compensated +5 keeps its historic pair/day slot; same-day re-payment may be lawful but no-bonus, while later eligible day may re-earn. +10 has no daily cap. |
| M18-LOCK-01 | Detached scalar discovery precedes the fixed forward graph `Shop -> ShopStaff -> User -> Customer -> ShopCustomer -> Key -> Debt -> Payment -> PaymentVoid -> RatingEvent -> AuditLog`; Customer is never re-locked. |
| M18-TX-01 | The caller owns the one transaction. Repositories/adapters borrow the Session, and no stage commits, rolls back, closes, retries, or uses a post-commit hook. |
| M18-KEY-01 | `shop.payments.void` v1 hash binds actor, Shop, Payment, server-resolved Debt, expected Debt revision, and normalized reason. Matching replay is zero clock/money/rating/mutation/audit. |
| M18-AUDIT-01 | New USER facts are `payment.voided`/PAYMENT and status-change-only `debt.reopened_after_payment_void`/DEBT; actor remains envelope-only. |
| M18-PRIV-01 | Only Shop receives a localized closed reason label. Customer/disclosure/error/log/repr/flash/audit payloads receive no reason, actor, raw score, source cause, key/hash, PaymentVoid ID, or other-Shop fact. |
| M18-WEB-01 | Exactly two new SSR routes exist: GET and POST `/shop/payments/{payment_id}/void`; no Customer/admin/API/JSON/fragment/search mutation route exists. |
| M18-MIG-01 | Child `e9f0a1b2c3d4` is the sole child of `d8e9f0a1b2c3`, adds exactly one table, and preserves predecessor business data. |
| M18-MIG-02 | Before source scan, writers drain and `debts, payments, rating_events, audit_log` receive the named SHARE ROW EXCLUSIVE lock. Old binaries may not restart afterward. |
| M18-DOWN-01 | Downgrade is fail-closed, never deletes or rewrites M18 evidence. |

## Stops and containment

Stop and return to Product Gate for a protected mismatch, second head, source
metadata ambiguity, need to mutate original Payment or old RatingEvent business
data, arbitrary earlier void, Customer re-lock/inverse graph, unpaired source
benefit, evidence deletion, privacy expansion beyond Shop reason label, refund/
unvoid/forgiveness/override, scheduler/notification/report/admin/API, new
dependency, M19 code, or any focused evidence nonpass.

M18.06 creates only the four tracked authority documents and focused static
tests. It creates no M18 product code, route, ORM mapping, or migration.
