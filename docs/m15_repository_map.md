# Nasiya M15 Repository Map

This map fixes M15 placement and identifies which seams already exist.
`EXISTS` means the symbol exists in the implementation through M15.30;
`EXTEND` means later bounded M15 presentation/closeout work remains;
`PLANNED` means no symbol is claimed to exist yet.

## Existing authority and extension seams

| State | Location | Symbol/seam | M15 responsibility |
| --- | --- | --- | --- |
| EXISTS | `app/debt/enums.py` | `DebtStatus`, persisted set | `overdue` is implemented; inherited written-off vocabulary remains unwired. |
| EXISTS | `app/debt/models.py` | `Debt` M15 metadata | Marker fields and exact constraints are implemented by M15.15. |
| EXISTS | `app/debt/contracts.py` | `DebtAggregate`, projections | Effective/persisted overdue lifecycle, marker and late-payment transitions are implemented. |
| EXISTS | `app/debt/business_time.py` | `tashkent_business_date` | The single timezone helper owns the due-past predicate. |
| EXISTS | `app/debt/repository.py` | debt mapping/lock scopes/hard-block reader | Marker mapping, locked-Customer boolean read, and the Debt-owned adapter for an already forward-locked transition row are implemented. |
| EXISTS | `app/debt/creation_eligibility.py` | hard-block reader seam | The production locked-Customer reader and post-lock business date gate proposal creation. |
| EXISTS | `app/debt/customer_accept_service.py` | hard-block reader seam | Acceptance uses the same production reader factory and post-lock clock authority. |
| EXISTS | `app/debt/targeting.py` | `lock_debt_target_before_offer` | Preserve proposal predecessor order. |
| EXISTS | `app/debt/customer_decision_targeting.py` | `lock_customer_debt_predecessors` | Preserve acceptance predecessor order. |
| EXISTS | `app/debt/overdue_ports.py` | overdue/materialization ports | Narrow hard-block/date and locked-Debt posted-total protocols; no payment import into debt. |
| EXISTS | `app/debt/overdue_targeting.py` | candidate discovery and locks | M15.19 adds bounded detached scalar discovery and `Shop -> Customer -> ShopCustomer -> Debt` locked revalidation. |
| EXISTS | `app/debt/overdue_service.py` | inline/bounded batch transition | M15.20–21 exact-once rollover/audit pair and per-candidate caller-owned transactions. |
| EXISTS | `app/payment/contracts.py` | v1/v2 payment hash and receipt contracts | V2 binds typed basis; v1 is restricted to completed legacy replay; receipt history is marker-derived. |
| EXISTS | `app/payment/commands.py` | payment command assembly | The exact mutation-v2 versus legacy-replay-v1 sum type is implemented. |
| EXISTS | `app/payment/service.py` | `record_debt_payment` | Post-Debt-lock clock, inline rollover, persisted-overdue partial/full payment and replay boundaries are implemented. |
| EXISTS | `app/payment/repository.py` | open-set/posted-total/history adapters | Overdue exposure/count, locked posted-total and basis-aware historical/current balance adapters are implemented. |
| EXISTS | `app/payment/router.py` | `create_shop_debt_payment` | Production mutation uses the service clock, strict v2 basis, and narrow v1 replay resolver. |
| EXISTS | `app/payment/read_service.py` | payment progress/receipt composition | Effective-overdue progress and marker-derived historical/current receipt bases are side-effect free. |
| EXTEND | templates / `app/payment/router.py` | payment GET/form/receipt | Hidden basis plumbing is implemented; M15.33 retains only overdue presentation/copy completion. |
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
| Due-date, delayed materialization, exactly-once clawback | `tests/test_debt_business_time.py`, `tests/test_m15_overdue_service_postgresql.py` |
| Batch/payment overlap, stale candidates, edge money, rollback and privacy | `tests/test_m15_transition_race_postgresql.py` plus the overdue-service PostgreSQL matrix |
| Batch/payment and payment/proposal/acceptance races | `tests/test_m15_transition_race_postgresql.py`, `tests/test_m15_late_payment_postgresql.py`, and retained combined-lock tests |
| Cross-Shop hard block, post-lock midnight date, and lawful unblock | debt-creation, customer-accept, and late-payment PostgreSQL tests |
| Basis drift, v1/v2 replay, stale revision | payment boundary/service and `tests/test_m15_late_payment_postgresql.py` |
| Receipt rebasing and overpayment | payment read/value/service and late-payment receipt tests |
| Rollback and immutable audit | overdue/payment PostgreSQL service tests |
| Tenant IDOR, suspension, CSRF/PRG | payment web and hardening tests |
| Upgrade/head/guarded downgrade | new M15 migration contract and PostgreSQL migration tests |
| OUT containment and inherited enum allowlist | `tests/test_m15_out_containment.py` plus retained M13 guard |

States in this map reflect implementation through M15.30. Any remaining
`EXTEND` or `PLANNED` entry is prospective and is not an implementation claim.
