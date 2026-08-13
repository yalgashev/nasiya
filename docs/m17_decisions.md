# Nasiya M17 Decisions

## Authority evidence

M17.01–05 passed read-only. Start is workflow commit
`fdfe7258da70b4ad8c948f8e5dfd2ce7e6117057`, tree
`4349b7fbdbc6ee1c7ba08a756a6b6fb647cdf30c`, clean/synced `0 0`, with head
`c7d8e9f0a1b2`. TT SHA-256 is
`569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab`.
External Product Gate, Scope Freeze, and task-guide hashes are respectively
`e64ae346601475a411a7a2a74d3ad4780c95f878825ce48235d0181383260d8d`,
`ff5fd0269e1fa9cc80aeaa2ff2dbf5f27fdba85908aecbb52717a0924317774c`, and
`9b7af60dcd08a81b75b3e74ec081e3759eeaaec056553c1d0a9f3f0690d5d631`.

## Product Owner decisions — final

| ID | Frozen decision |
| --- | --- |
| PO-M17-01 | M17 is controlled written-off Debt and recovery, not a broad collections platform. |
| PO-M17-02 | Runtime graph is persisted `overdue -> written_off -> written_off_settled`; effective-only overdue is ineligible. |
| PO-M17-03 | Only authenticated active platform-admin writes off directly; Shop membership is no substitute. |
| PO-M17-04 | Lawful persisted overdue has no additional age threshold. |
| PO-M17-05 | Reasons are exactly `collection_exhausted|customer_unreachable|insolvency_or_deceased|legal_or_compliance|fraud_or_abuse`. |
| PO-M17-06 | Write-off stores immutable aware time, positive revision, closed reason, and actor User. |
| PO-M17-07 | Settlement stores separate aware time and positive revision. |
| PO-M17-08 | `paid_at` remains NULL in both written-off states. |
| PO-M17-09 | Write-off neither forgives nor changes balance and deletes no Debt/Payment. |
| PO-M17-10 | Coherent persisted overdue and positive original-basis remaining are mandatory. |
| PO-M17-11 | Remaining is exact `original_amount_uzs - posted_total` whole UZS. |
| PO-M17-12 | Partial recovery preserves status, markers, block, and writes no rating event. |
| PO-M17-13 | Exact full recovery alone settles; overpayment is rejected. |
| PO-M17-14 | Rating adds `written_off=-40` and `written_off_settled=+10`. |
| PO-M17-15 | `-40/+10` append to and never erase the existing `-15` history. |
| PO-M17-16 | M17 rating events are live-only; there is no historical M17 backfill. |
| PO-M17-17 | `-40` time equals write-off marker; `+10` time equals settlement and terminal Payment. |
| PO-M17-18 | Existing uniqueness, append-only law, total order, sequential clamp, and bands remain. |
| PO-M17-19 | Global block is effective/persisted overdue OR unresolved written-off; written-off exposure/open-count is zero. |
| PO-M17-20 | Settlement removes only its Debt overlay; another blocker keeps `BLOCKED`. |
| PO-M17-21 | Write-off requires coherent overdue marker/revision, `-15`, and both SYSTEM audits. |
| PO-M17-22 | Settlement requires write-off markers/`-40`/audit and exact terminal Payment chain. |
| PO-M17-23 | Durable key precedes Debt; write-off/recovery use the frozen atomic stage orders. |
| PO-M17-24 | Customer remains global serialization; repositories borrow Session ownership. |
| PO-M17-25 | Admin hash binds actor, Debt, revision, reason; replay is zero-write and changed hash conflicts. |
| PO-M17-26 | Existing Payment POST/replay is extended; platform-admin cannot receive Payment. |
| PO-M17-27 | Admin route inventory is exact +3 SSR routes with no API/JSON/fragment. |
| PO-M17-28 | Queue is persisted-overdue only, `(overdue_at,id)`, page 50, without global search. |
| PO-M17-29 | Queue/form show exact safe summary; completed detail additionally shows selected reason label. |
| PO-M17-30 | Reason/actor are admin-bounded; Shop/Customer/disclosure expose neither nor raw rating. |
| PO-M17-31 | Admin may write off a suspended-Shop/inactive-Customer lawful exposure without impersonation/payment. |
| PO-M17-32 | Recovery requires active Shop live OWNER/MANAGER/CASHIER; inactive Customer is allowed, suspended/revoked/foreign mutation denied. |
| PO-M17-33 | One child of `c7d8e9f0a1b2`, zero new table, Debt/registry schema extensions only. |
| PO-M17-34 | Migration is schema-only/no-backfill/no source DML with drain/restart ban and guarded downgrade. |
| PO-M17-35 | Void/compensation, scheduler, notification, reports, override, bulk/search/API/delete are OUT. |
| PO-M17-36 | Real-PG, barrier, privacy, Chrome, eight-checkpoint, and exact-SHA closure evidence is mandatory. |

## Implementation decisions fixed by M17.01–05

- `DebtStatus.WRITTEN_OFF` and `DebtStatus.WRITTEN_OFF_SETTLED` **EXIST** as
  dormant Python vocabulary only. They are not M15 persisted/runtime capability.
- Write-off requires exact marker/revision, `RatingEvent(overdue,-15)`, SYSTEM
  `debt.overdue`, and SYSTEM `debt.clawback_applied`; the audit pair is never
  optional and any mismatch fails closed.
- The v1 endpoint is exactly `admin.debts.write_off`, using existing result
  type `debt`. Existing Payment v2 hash/endpoint stays; no v3 is introduced.
- Exact new audit events are `debt.written_off` and
  `debt.written_off_settled`, both USER/Debt. Payloads are exactly
  `{reason_provided,from_status,to_status,written_off_revision}` and
  `{source,from_status,to_status,debt_revision_after}`. Actor identity stays in
  the audit envelope, not payload.
- `audit_log` is the actual table. The planned `(status, overdue_at, id)` index
  is required; existing due-date indexes do not satisfy that queue contract.
- Current one-event-per-Debt coherence is intentionally extended only to the
  four lawful M17 chain families; all other chain shapes are corruption.

## Stops and containment

Stop and seek a new Product Gate if work needs void/reversal, compensation,
notification, scheduler, report/loss accounting, broad search, free-text reason,
effective-only write-off, source rewrite/backfill, second head, inverse Customer
lock, or a privacy exception beyond completed-detail reason label. A protected
hash or baseline mismatch also blocks work.

M17.06 creates only the four tracked authority documents and focused static
tests. It adds no M17 product code, migration, route, or checkpoint commit.
