# Nasiya M15 Repository Map

This map fixes M15 placement and identifies which seams already exist.
`EXISTS` means the symbol exists at M14 baseline; `EXTEND` means it exists but
needs a bounded M15 change; `PLANNED` means no M15 symbol is claimed to exist.

## Existing authority and extension seams

| State | Location | Symbol/seam | M15 responsibility |
| --- | --- | --- | --- |
| EXTEND | `app/debt/enums.py` | `DebtStatus`, persisted set | Add `overdue`; retain inherited written-off vocabulary without wiring it. |
| EXISTS | `app/debt/models.py` | `Debt` M15 metadata | Marker fields and exact constraints are implemented by M15.15. |
| EXTEND | `app/debt/contracts.py` | `DebtAggregate`, projections | Carry effective/persisted overdue and marker data only. |
| EXTEND | `app/debt/business_time.py` | `tashkent_business_date` | Reuse the one timezone helper for due-past predicate. |
| EXISTS | `app/debt/repository.py` | debt mapping/hard-block reader | Marker mapping and locked-Customer boolean read are implemented by M15.16. |
| EXTEND | `app/debt/creation_eligibility.py` | hard-block reader seam | Replace `_LockedCustomerNoHardBlockReader` after Customer lock. |
| EXTEND | `app/debt/customer_accept_service.py` | hard-block reader seam | Replace `_NoReachableCustomerHardBlock` with same authority. |
| EXISTS | `app/debt/targeting.py` | `lock_debt_target_before_offer` | Preserve proposal predecessor order. |
| EXISTS | `app/debt/customer_decision_targeting.py` | `lock_customer_debt_predecessors` | Preserve acceptance predecessor order. |
| EXISTS | `app/debt/overdue_ports.py` | overdue/materialization ports | Narrow boolean/date protocols; no payment import into debt. |
| EXISTS | `app/debt/overdue_targeting.py` | candidate discovery and locks | Scalar discovery and `Shop -> Customer -> ShopCustomer -> Debt` locks are implemented by M15.16. |
| PLANNED | `app/debt/overdue_service.py` | inline/bounded batch transition | Exactly-once rollover/clawback, caller-owned session. |
| EXTEND | `app/payment/contracts.py` | v1 payment hash contracts | Add v2 domain/hash and typed basis contract. |
| EXTEND | `app/payment/commands.py` | payment command assembly | Parse mutation-v2 versus legacy-replay-v1 sum type. |
| EXTEND | `app/payment/service.py` | `record_debt_payment` | Materialize after Debt lock, capture clock there, late amount/status. |
| EXTEND | `app/payment/repository.py` | open-set adapter/history reads | Overdue exposure/count is implemented; marker receipt basis remains later work. |
| EXTEND | `app/payment/router.py` | `create_shop_debt_payment` | Pass post-lock callable clock, never `lambda: request_now`. |
| EXTEND | `app/payment/read_service.py` / templates | payment GET/form/receipt | Emit hidden basis and overdue copy without client money logic. |
| EXISTS | `app/audit/models.py` | registry metadata | Safe SYSTEM `debt.overdue` and `debt.clawback_applied` schema shapes are implemented by M15.15. |
| EXTEND | `app/db.py`, `alembic/env.py`, `tests/postgresql.py` | registration/cleanup | Register existing metadata delta and keep FK-safe cleanup. |
| EXISTS | `alembic/versions/b6c7d8e9f0a1_add_overdue_persistence.py` | one migration | One child of `a5b6c7d8e9f0`; guarded downgrade. |

The planned `app/debt/overdue_*` boundary is debt-owned. Payment repository
implements an adapter for its narrow port; `app.debt` must not import
`app.payment`.

## Lock and transaction proof

Global order is `Shop -> ShopStaff -> TelegramLinkToken -> OtpDispatch ->
OtpChallenge -> User -> TelegramLink -> Customer -> OfferVersion ->
OfferAcceptance -> CustomerIdentity -> ObjectFile -> CustomerDocument ->
ShopCustomer -> IdempotencyKey -> Debt -> Payment -> AuthSession`; unused
classes are skipped and same-class UUIDs ascend.

Proposal/acceptance retain predecessors through Customer, then capture
hard-block time and read effective/persisted overdue before ShopCustomer/Debt.
Payment does completed-key lookup after current authority/visibility, inserts a
new key before Debt, locks Debt, captures time, inline-materializes if needed,
then writes payment/audit. Batch is `Shop -> Customer -> ShopCustomer -> Debt
-> AuditLog`; it must not call `lock_debts_in_id_order`, which lacks parents.

TX-A remains short auth/session/CSRF work. TX-B owns rechecks, locks, transition,
payment/audit writes, and coordinator commit/rollback. Repositories/services
borrow the session. No external I/O, retry, `NOWAIT`, `SKIP LOCKED`, advisory
lock, queue, or background transaction is authorised.

## Required test mapping

| Threat or invariant | Required evidence placement |
| --- | --- |
| Due-date, delayed materialization, exactly-once clawback | `tests/test_debt_business_time.py`, new overdue PostgreSQL tests |
| Batch overlap and parent-lock order | new overdue PostgreSQL tests |
| Batch/payment and payment/proposal/acceptance races | new M15 combined-lock-order PostgreSQL tests |
| Cross-Shop hard block and lawful unblock | debt-creation and customer-accept PostgreSQL gate tests |
| Basis drift, v1/v2 replay, stale revision | payment contract/command/service PostgreSQL tests |
| Receipt rebasing and overpayment | payment read/value/service tests |
| Rollback and immutable audit | overdue/payment PostgreSQL service tests |
| Tenant IDOR, suspension, CSRF/PRG | payment web and hardening tests |
| Upgrade/head/guarded downgrade | new M15 migration contract and PostgreSQL migration tests |
| OUT containment and inherited enum allowlist | `tests/test_m15_out_containment.py` plus retained M13 guard |

No prospective symbol in this map is an implementation claim. M15.06 adds only
this authority map and its static documentation check.
