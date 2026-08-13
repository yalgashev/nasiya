# Nasiya M17 Scope Contract

M17 is the **Written-Off Debt & Recovery Foundation**: a coherent persisted
overdue Debt may be written off by a platform administrator and later recovered
through the existing Shop payment flow. It is not an accounting, notification,
scheduler, or broad admin platform.

## Authority and frozen start

Precedence is TT, external M17 Scope Freeze, external M17 Product Gate, these
four tracked M17 documents, M16 evidence, then repository evidence. External
planning files and TT remain read-only.

M17.01–05 start evidence: `fdfe7258da70b4ad8c948f8e5dfd2ce7e6117057`, tree
`4349b7fbdbc6ee1c7ba08a756a6b6fb647cdf30c`, clean and synced `0 0`, one
Alembic head `c7d8e9f0a1b2`; workflow CI run/job `31613580874/94170729270`,
`4253 passed in 224.37s`, Ruff green, zero non-pass. TT SHA-256 is
`569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab`; Product
Gate is `e64ae346601475a411a7a2a74d3ad4780c95f878825ce48235d0181383260d8d`;
Scope Freeze is `ff5fd0269e1fa9cc80aeaa2ff2dbf5f27fdba85908aecbb52717a0924317774c`;
task guide is `9b7af60dcd08a81b75b3e74ec081e3759eeaaec056553c1d0a9f3f0690d5d631`.
The completed M16 implementation/docs anchors are respectively
`1c8423023be0d3bbb7f388b9e341d99e7117ed62` /
`03252df69a970d5a60af984f8d7749c27b463727` and
`188b122832c371be7ccec8e9b158b7700dcd1d0d` /
`28d1130470be87b8beffdbf54ab09d14ecdb2f1b`.

## State, money, and source contracts

```text
persisted overdue -- platform-admin write-off --> written_off
written_off      -- partial original-basis Payment --> written_off
written_off      -- exact full original-basis Payment --> written_off_settled
```

An effective-but-unmaterialized active past-due Debt is not writable off.
`written_off_settled` does not return to `written_off`. `paid_at` remains NULL
for both written-off states; accepted/overdue history stays immutable.

`posted_total` is the immutable Payment sum; `remaining = original_amount_uzs -
posted_total`. Write-off needs coherent persisted overdue and
`0 < remaining <= original_amount_uzs`. Recovery accepts exact positive whole
UZS up to remaining; partial preserves written-off/block and exact full creates
the settlement marker. Written-off statuses contribute zero to inherited
exposure/open-count; their unresolved block is separate.

Write-off source requires overdue markers/revision, unique `RatingEvent(overdue,
-15)`, and matching SYSTEM/Debt `debt.overdue` plus `debt.clawback_applied`
audits at the same source instant/revision. Settlement requires immutable
write-off evidence, live `-40`, its USER/Debt audit, and terminal Payment total.
Contradiction fails closed. The trusted UTC clock is captured once after replay
and locks; marker chronology is nondecreasing and revisions strict.

## Vocabulary, rating, block, and privacy

M17 persists `written_off|written_off_settled`; closed reasons are
`collection_exhausted|customer_unreachable|insolvency_or_deceased|legal_or_compliance|fraud_or_abuse`.
No free text, PII, client clock, or backdate exists.

Rating adds only `written_off -> -40` and `written_off_settled -> +10`, both
live-only. Existing `+5/-15`, thresholds, sequential clamp, and order remain:

```text
score_0 = 60
ORDER BY occurred_at ASC, debt_id ASC, event_type ASC
score_i = min(100, max(0, score_(i-1) + delta_i))
```

Lawful per-Debt families are `{on_time_paid}`, `{overdue}`,
`{overdue,written_off}`, and `{overdue,written_off,written_off_settled}` only.
Hard block is effective/persisted unpaid overdue OR unresolved `written_off`;
settlement removes only its Debt overlay. Reason, actor, score, delta, count,
balance, cause, other-Shop facts, PII, keys/hashes, and internal IDs are absent
from generic errors, repr/logs, audits, Shop/Customer, and disclosure output.

## Authority, transactions, and web boundary

Only active `User.is_platform_admin` writes off; Shop membership is no
substitute. Admin may resolve a suspended-Shop/inactive-Customer exposure but
cannot impersonate or pay. Recovery remains active-Shop
`OWNER|MANAGER|CASHIER`; suspended/revoked/foreign mutation denies.

```text
admin:    Shop -> actor User -> Customer -> ShopCustomer -> IdempotencyKey
          -> Debt -> RatingEvent -> AuditLog
recovery: Shop -> ShopStaff -> actor User -> Customer -> ShopCustomer
          -> IdempotencyKey -> Debt -> Payment -> RatingEvent -> AuditLog
```

Customer is the serialization point and never re-locks. Repositories borrow the
caller Session without commit/rollback/close. Exactly these SSR routes are IN:

```text
GET  /admin/debts/write-off-candidates
GET  /admin/debts/{debt_id}/write-off
POST /admin/debts/{debt_id}/write-off
```

The queue is persisted-overdue only, `(overdue_at, id)`, page 50, not search.
POST uses CSRF, canonical raw key, revision, reason, confirmation, PRG, and
no-store. Completed admin detail may show selected reason label, never actor.

Admin write-off idempotency endpoint is exactly `admin.debts.write_off` with
existing result type `debt`. Its v1 request hash binds actor User, Debt locator,
expected revision, and closed reason. Same key/hash replays the original Debt
result before clock or source work; different hash conflicts with zero writes.
Raw key is transient and redacted; only digest/request hash may persist in the
idempotency row.

Exact new audit events are `debt.written_off` and
`debt.written_off_settled`, both USER actor/Debt object. Write-off payload is
exactly `{reason_provided,from_status,to_status,written_off_revision}` with
`reason_provided=true`; settlement payload is exactly
`{source,from_status,to_status,debt_revision_after}` with `source=payment`.
Actor ID remains the audit-envelope field and exact reason remains Debt evidence;
neither is duplicated into payload.

## Exact IN, OUT, and proof

IN: typed state/reason/source contracts; original-basis recovery; `-40/+10`;
block/disclosure extension; three routes; one schema-only migration; PG/static/
web/privacy/race/manual evidence. OUT: void/refund/reversal/correction/
compensation; scheduler/worker/CLI/retry; notification; reports; override/
settings/raw rating; bulk/search; self-pay; API/JSON/HTMX; caches; historical
`-40/+10`; source DML; new dependency; M18 work.

Proof requires named constraints, no-backfill canonical source preservation,
guarded downgrade, deterministic Barrier/Event races with no sleep/retry,
rollback faults, IDOR/privacy tests, and sanitized manual booleans. M17.06 adds
authority only: no product code, migration, or checkpoint commit.

| Threat | First required evidence |
| --- | --- |
| Unauthorized admin, IDOR, or foreign chain | Detached discovery plus forward-lock real-PG and generic-web tests. |
| Effective-only or incoherent source | Core unit/PG tests reject before key, clock, or mutation. |
| Double write-off, stale revision, or two recoveries | Same/different-key, unique-wait, and terminal-payment Barrier tests. |
| Partial commit or mixed snapshot | Event/audit/key fault rollback and disclosure old-or-new barrier tests. |
| Migration loss or OUT creep | Source-projection/downgrade matrix and source-scoped static containment. |
