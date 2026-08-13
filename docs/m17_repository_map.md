# Nasiya M17 Repository Map

`EXISTS` is present at M17 baseline. `EXTEND` is an existing bounded seam to
change. `PLANNED` does **not** claim that the path or symbol already exists.
The shared baseline is `fdfe7258da70b4ad8c948f8e5dfd2ce7e6117057` /
`4349b7fbdbc6ee1c7ba08a756a6b6fb647cdf30c`, protected by the external hashes
recorded in `m17_scope_contract.md`; the current head is `c7d8e9f0a1b2`.

| Status | Path / symbol | M17 responsibility |
| --- | --- | --- |
| EXISTS | `app/debt/enums.py:DebtStatus.WRITTEN_OFF`, `WRITTEN_OFF_SETTLED` | Dormant vocabulary only; not in `M15_PERSISTED_STATUSES`. |
| EXTEND | `app/debt/{enums,contracts,models,repository}.py` | M17 persisted family, aggregate, six fields/checks, and block reader. |
| EXISTS | `app/debt/overdue_service.py:materialize_locked_overdue_debt` | M15 source marker reused by write-off. |
| EXISTS | `app/debt/rating_ports.py:LockedOverdueRatingAppendPort` | Debt-local structural rating pattern. |
| PLANNED | `app/debt/write_off_targeting.py` | Scalar candidate/detail reads and forward admin locks. |
| PLANNED | `app/debt/write_off_service.py` | Pure transition and caller-owned write-off coordinator. |
| EXTEND | `app/payment/{policy,values,service,repository}.py` | Original-basis payability and partial/full recovery. |
| EXISTS | `app/payment/targeting.py:lock_tenant_payment_predecessors` | `Shop -> ShopStaff -> User -> Customer -> ShopCustomer` lock token. |
| EXISTS | `app/payment/rating_ports.py:LockedPaymentRatingAppendPort` | Payment-local structural rating pattern. |
| EXTEND | `app/rating/{enums,contracts,ports,service,repository,current_read_service}.py` | `-40/+10`, legal source chains, append/read/fold. |
| EXISTS | `app/rating/adapters.py:SqlAlchemyLockedRatingAppendAdapter` | Composition adapter; validates existing tokens and takes no lock. |
| EXTEND | `app/idempotency/{contracts,models,repository}.py` | Admin endpoint/hash and existing Debt result. |
| EXTEND | `app/audit/{contracts,models,repository,redaction}.py` | Two closed Debt USER audit facts. |
| EXISTS | `app/offers/authorization.py:require_platform_admin_actor` | Active-admin dependency and row-lock revalidation. |
| PLANNED | `app/debt/admin_router.py` | Three bounded admin SSR routes. |
| EXTEND | `app/{main.py,security_headers.py,debt/router.py,payment/router.py}` | Composition, no-store, and safe presentation. |
| PLANNED | `app/templates/debt/admin_write_off_*.html` | Autoescaped candidate/form/completed views. |
| PLANNED | `alembic/versions/d8e9f0a1b2c3_add_written_off_debt_recovery.py` | Single schema-only child. |

## Dependency and lock constraints

`app.debt` and `app.payment` may depend only on their local structural rating
ports, never concrete rating model/repository code. The composition adapter
avoids debt/payment-to-rating-to-debt cycles.

```text
admin:    Shop -> User -> Customer -> ShopCustomer -> IdempotencyKey -> Debt
          -> RatingEvent -> AuditLog
recovery: Shop -> ShopStaff -> User -> Customer -> ShopCustomer -> IdempotencyKey
          -> Debt -> Payment -> RatingEvent -> AuditLog
```

For admin, `assert_platform_admin_actor` runs after Shop lock. A Customer
obtained in either forward chain is adapted to existing block/rating scope and
never re-locked. Tokens are session-bound/redacted; ORM rows stay inside
repositories; no repository commits, rolls back, or closes the borrowed Session.

## Finite planned test families

`tests/test_m17_authority_docs.py`, `test_m17_contracts.py`,
`test_m17_migration_contract.py`, `test_m17_migration_postgresql.py`,
`test_m17_repository_postgresql.py`, `test_m17_write_off_service_postgresql.py`,
`test_m17_write_off_races_postgresql.py`, `test_m17_recovery_service_postgresql.py`,
`test_m17_recovery_races_postgresql.py`,
`test_m17_combined_lock_order_postgresql.py`, `test_m17_admin_router.py`,
`test_m17_web_privacy.py`, and `test_m17_out_containment.py` are finite M17
families. Scheduler, notification, report, void, and API test families are not
planned because their runtime capability is OUT.
