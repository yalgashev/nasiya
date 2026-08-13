# Nasiya M18 Repository Map

`EXISTS` is present at the M18 docs baseline. `EXTEND` is an existing bounded
seam that M18 may alter. `PLANNED` does **not** claim that a path or symbol already exists. The protected baseline is
`d341edf95511653d566726826304a74b3b3ffb60` /
`aedd8ef31a66e1bd15481e2f5079506f2bde61df`; current Alembic head is
`d8e9f0a1b2c3`.

| Status | Path / symbol | M18 responsibility |
| --- | --- | --- |
| EXISTS | `app/payment/models.py:Payment` | Immutable Payment source with `uq_payments_debt_id_debt_revision_after`. |
| EXTEND | `app/payment/{enums,commands,contracts,values,policy}.py` | Closed void reason/outcome/error, v1 hash, money/as-of, and pure transition contracts. |
| EXTEND | `app/payment/{repository,targeting,service,read_service,dependencies,presentation,router}.py` | Tenant discovery/locks, latest source proof, caller-owned coordinator, safe reads, and exact SSR composition. |
| EXISTS | `app/payment/targeting.py:lock_tenant_payment_predecessors` | Inherited `Shop -> ShopStaff -> User -> Customer -> ShopCustomer` lock prefix. |
| EXISTS | `app/payment/rating_ports.py:LockedPaymentRatingAppendPort` | Payment-local structural port, not a concrete rating dependency. |
| PLANNED | `app/payment/void_targeting.py` | Detached Payment discovery and void-only OWNER/MANAGER targeting. |
| PLANNED | `app/payment/void_source.py` | Locked latest/non-voided and exact positive-source classifier. |
| PLANNED | `app/payment/void_service.py` | Void coordinator and append-only stage order. |
| EXTEND | `app/debt/{enums,contracts,repository,overdue_service,rating_ports}.py` | `payment_void` overdue source, pure reopen effects, marker-safe hard-block and audit integration. |
| EXISTS | `app/debt/overdue_service.py:materialize_locked_overdue_debt` | Existing narrow overdue materialization seam. |
| EXISTS | `app/debt/overdue_service.py:append_pending_overdue_audits` | Existing canonical overdue/clawback audit append seam. |
| EXTEND | `app/rating/{enums,contracts,models,ports,repository,service,current_read_service,adapters}.py` | Source revision, cycles, compensation, ordered fold, and locked read/append composition. |
| EXISTS | `app/rating/adapters.py:SqlAlchemyLockedRatingAppendAdapter` | Stateless composition adapter that validates inherited locks and takes no Customer lock. |
| EXTEND | `app/audit/{contracts,models,repository,redaction}.py` | Closed void/reopen facts and narrow payment-void overdue payload extension. |
| EXTEND | `app/idempotency/{contracts,models,repository}.py` | `shop.payments.void` to existing `payment` result registry and transactional replay. |
| EXTEND | `app/main.py` | Concrete adapter composition only. |
| EXTEND | `app/templates/payment/{shop_list,shop_new,shop_receipt,customer_list,customer_receipt}.html` | Existing safe history/receipt projections. |
| PLANNED | `app/templates/payment/shop_void.html` | Autoescaped no-store void form; no client authority, money, or clock. |
| PLANNED | `alembic/versions/e9f0a1b2c3d4_add_payment_void_and_rating_source_revision.py` | Sole linear source-metadata/backfill migration. |

## Dependency and lock constraints

Payment and Debt depend only on their local structural rating ports. Concrete
rating persistence is composed in `app.main`; no Payment-to-concrete-rating or
concrete-rating-to-Payment-service cycle is permitted. A Customer token from
the forward chain is adapted, never re-locked.

```text
Shop -> ShopStaff -> actor User -> Customer -> ShopCustomer -> IdempotencyKey
-> Debt -> Payment -> PaymentVoid -> RatingEvent -> AuditLog
```

PaymentVoid, RatingEvent, and AuditLog are append stages after their predecessor
locks; new rows are not separately re-locked. Repositories borrow the Session:
they do not commit, roll back, or close it, and ORM rows do not escape their
repository boundary.

## Finite planned test families

`tests/test_m18_authority_docs.py`, `test_m18_void_contracts.py`,
`test_m18_void_targeting_postgresql.py`, `test_m18_void_source_postgresql.py`,
`test_m18_void_service_postgresql.py`, `test_m18_migration_postgresql.py`,
`test_m18_rating_cycles.py`, `test_m18_rating_compensation_postgresql.py`,
`test_m18_void_read_postgresql.py`, `test_m18_void_concurrency_postgresql.py`,
`test_m18_global_lock_order_postgresql.py`, `test_m18_void_web_flows.py`,
`test_m18_web_presentation_security.py`, and `test_m18_hardening_contracts.py`
are the finite M18 families. Refund, unvoid, scheduler, notification, report,
admin/API, and M19 families are not planned because their capability is OUT.
