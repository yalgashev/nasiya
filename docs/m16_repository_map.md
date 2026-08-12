# Nasiya M16 Repository Map

This map separates repository facts from future placement. M16 baseline is `main` HEAD `547723ffc8e4148c5b4de86763b7c5add0588e86`, tree `a8bc494c90dde3cf186b49aad8b6b8470af99c00`, M15 implementation parent `13bda85fb5df99d1be2b1da578e0f1a256f1d336`, and sole Alembic head `b6c7d8e9f0a1`. `EXISTS` means the named path/symbol is present at that baseline. `EXTEND` means an existing seam must change during later M16 implementation. `PLANNED` names a future boundary only; it does **not** claim that the path or symbol already exists.

## Existing seams and required extensions

| State | Location | Current seam / M16 responsibility |
| --- | --- | --- |
| EXISTS | `app/debt/business_time.py` | Tashkent business-time and effective-overdue primitives; rating dates and hard-block overlay must use this policy. |
| EXISTS | `app/debt/repository.py` | `LockedCustomerHardBlockScope`, `SqlAlchemyLockedCustomerGlobalHardBlockReader`, and `mark_locked_debt_transition_scope` are the M15 persisted/effective hard-block and transition seams. |
| EXISTS | `app/debt/overdue_targeting.py` | `resolve_and_lock_overdue_candidate` locks `Shop -> Customer -> ShopCustomer -> Debt` for the batch path. |
| EXTEND | `app/debt/overdue_service.py` | `materialize_locked_overdue_debt` must retain its locked state transition but return a typed pending overdue effect before audit append, allowing the coordinator to stage `Debt -> RatingEvent -> AuditLog` (or `Debt -> Payment -> RatingEvent -> AuditLog` inline). |
| EXISTS | `app/payment/targeting.py` | `lock_tenant_payment_predecessors` owns the current tenant payment chain and locks `Shop -> ShopStaff -> User -> Customer -> ShopCustomer` before key/Debt work. |
| EXTEND | `app/payment/service.py` | `record_debt_payment` receives the local rating append port and makes an optional `+5` decision only after completed replay resolution, locked source state, post-lock clock capture, and Payment staging. |
| EXISTS | `app/payment/repository.py` | Current immutable Payment persistence is the source-ledger seam used to validate/reconcile exact payment facts. |
| EXTEND | `app/idempotency/contracts.py`, `app/idempotency/repository.py` | Add only the closed disclosure endpoint/result pair and canonical v1 request-hash handling; raw keys remain transient. |
| EXTEND | `app/audit/contracts.py`, `app/audit/models.py`, `app/audit/repository.py` | Extend closed audit registries for one `disclosure.risk_band_viewed` USER audit and `disclosure_view` object; rating events do not obtain generic rating audits. |
| EXTEND | `app/shop_customer/router.py` | Add only the current-Shop disclosure POST entry point and safe action locator to the existing roster/detail context. |
| EXTEND | `app/debt/router.py` | Preserve existing proposal/debt navigation while exposing no score/event/history value. |
| EXTEND | `app/main.py` | Composition-wire the concrete rating adapter and exactly two disclosure routes; this is the only concrete cross-context assembly point. |
| EXTEND | `app/db.py`, `alembic/env.py` | Register future M16 metadata/migration discovery without creating an alternate schema path. |
| EXTEND | `tests/postgresql.py` | Add future M16 children to cleanup in child-first order. |
| EXTEND | `.github/workflows/ci.yml` | Retain/assert one Alembic head after the M16 child. |

## Planned boundaries — not present at baseline

| State | Planned location | Future responsibility |
| --- | --- | --- |
| PLANNED | `app/rating/enums.py` | Closed event type, recording source, band, and disclosure-purpose vocabulary. |
| PLANNED | `app/rating/contracts.py` | Typed immutable event, fold, append, and disclosure value contracts. |
| PLANNED | `app/rating/models.py` | ORM metadata for `rating_events` and `disclosure_view_logs`; no score/band cache model. |
| PLANNED | `app/rating/repository.py` | Locked append/read primitives, ordered event read, and historical snapshot persistence; repositories borrow caller Session only. |
| PLANNED | `app/rating/service.py` | Pure ordered fold/band derivation and coordinated disclosure snapshot service. |
| PLANNED | `app/rating/adapters.py` | Concrete composition-wired adapter that reuses an already locked Customer and never re-locks it. |
| PLANNED | `app/rating/router.py` | The two SSR disclosure routes only; no JSON/API/admin surface. |
| PLANNED | `app/debt/rating_ports.py` | Debt-local structural protocol for typed pending overdue append; no concrete `app.rating` import. |
| PLANNED | `app/payment/rating_ports.py` | Payment-local structural protocol for optional on-time append; no concrete `app.rating` import. |
| PLANNED | one Alembic child of `b6c7d8e9f0a1` | Revision `c7d8e9f0a1b2`; final filename is intentionally not frozen here. |
| PLANNED | `tests/test_m16_rating_contracts.py` | Pure event eligibility, exact fold/order, clamps, and OUT containment. |
| PLANNED | `tests/test_m16_rating_repository_postgresql.py` | Immutable/unique/check/index and customer-serialized daily-cap evidence. |
| PLANNED | `tests/test_m16_migration_postgresql.py` | Upgrade/reconciliation/corruption/rollback/guarded-downgrade evidence on real PostgreSQL. |
| PLANNED | `tests/test_m16_live_rating_postgresql.py` | Payment, batch, inline-late, replay, and source/audit atomicity evidence. |
| PLANNED | `tests/test_m16_combined_lock_order_postgresql.py` | Deterministic cross-flow lock-order proof without sleeps, retries, or timeouts. |
| PLANNED | `tests/test_m16_disclosure_web.py` | SSR, PRG, idempotency, localization, and suspended-history behavior. |
| PLANNED | `tests/test_m16_disclosure_web_security.py` | CSRF, IDOR, staff status, privacy, no-store/CSP, and browser-storage checks. |
| PLANNED | `tests/test_m16_out_containment.py` | Source-scoped static denials for newly introduced M16 OUT wiring. |

## Transaction ownership and dependency rule

The route/service coordinator owns one transaction. Every existing and planned repository takes a borrowed Session and must not commit, rollback, close, or re-lock Customer. The normative forward lock order is:

```text
Shop -> ShopStaff -> User -> Customer -> ShopCustomer -> IdempotencyKey
-> Debt -> Payment -> RatingEvent -> DisclosureViewLog -> AuditLog
```

Unused classes are skipped; same-class UUID locks ascend. Existing payment and overdue targeting stay the parent-chain authority. A disclosure POST discovers its scalar target before acquisition, then locks `Shop -> ShopStaff -> User -> Customer -> ShopCustomer`; it has no independent Customer-first path. A planned adapter can append only under that inherited lock and must not manufacture a fallback/no-op rating writer.

## Web placement and authority

The only planned M16 routes are:

```text
POST /shop/customers/{shop_customer_id}/risk-band-disclosures
GET  /shop/risk-band-disclosures/{disclosure_view_id}
```

POST authority is active actor, active current Shop, live OWNER/MANAGER/CASHIER membership, exact Customer/ShopCustomer parent chain, CSRF, closed purpose, and canonical key. It resolves idempotency before clock/rating work; then it reads the M15 hard-block reader and ordered immutable events, stores only a disclosure snapshot, appends its one audit, and returns PRG. GET scopes the opaque identifier through current actor/current Shop and the stored parent chain; it is historical only, performs no target lock/recomputation, and returns generic unavailable for non-own/invalid/missing state. Suspended and revoked members cannot read historical snapshots and receive the same generic unavailable result. Platform admin status never bypasses membership.

The browser-facing map is deliberately band-only. A future template/context may carry only `band`, `purpose`, `viewed_at`, an authorised POST action locator, a transient same-origin no-store raw key, and an opaque disclosure locator. It may not carry score, events, deltas, counts, amounts, balances, hard-block cause, PII, business identifiers, raw/digest/hash values, or internal authority IDs.

## Required proof placement

The planned tests are evidence contracts, not existing test claims. They must prove atomic source/rating/audit rollback; exactly-once source and daily-cap races; migration table-lock/drain and corruption failure; fresh, mixed, and guarded-downgrade paths; batch/inline overlap; effective-blocked and late-payoff history; same-key and stale-snapshot behavior; guessed/foreign IDOR and privacy absence; suspended/revoked behavior; and no lock inversion. Barriers are deterministic; sleep, retry loops, timeouts, NOWAIT, SKIP LOCKED, advisory locks, SQLite, `create_all`, and manual DDL do not qualify.
