# Nasiya M13 Repository Map

Status: authoritative M13 integration map, based on M13.01–07 read-only
audits. Baseline: M12 docs-only closeout
`7eb138571b1e990b87b6810a05524ad32986bbab`; current Alembic head
`e3f4a5b6c7d8`. This map names bounded reuse and required M13 seams; it does
not create a ledger, rating, CRM, or scheduler platform.

## Existing authority and persistence seams

| Area | Existing file:symbol | M13 use |
|---|---|---|
| Shop root/staff | `app/shop/models.py:Shop, ShopStaff`; `app/shop/enums.py:ShopRole, ShopStatus` | current-shop status and live membership/role source |
| Shop locks | `app/shop/repository.py:lock_shop_for_update, lock_actor_shop_staff_for_update` | Shop-first tenant mutation recheck |
| Current shop/auth | `app/auth/deps.py:get_detached_mutation_session_context`; `app/shop/context.py:resolve_current_shop` | closed TX-A, server-derived actor/current-shop only |
| User locks | `app/auth/repository.py:lock_actor_and_target_users_for_update` | UUID-ascending actor/target recheck before Telegram/Customer and financial FKs |
| Telegram eligibility | `app/telegram/repository.py:get_telegram_link_by_user_for_update, is_otp_eligible_telegram_link` | current active self-phone-verified target chain |
| Customer | `app/customer/models.py:Customer`; `app/customer/repository.py:lock_active_customer_for_target_user, lock_existing_own_customer_for_update` | target active and own-customer lock boundaries |
| M12 tenant parent | `app/shop_customer/models.py:ShopCustomer`; `repository.py:get_shop_customer_by_shop, lock_shop_customer_by_tenant_locator`; `contracts.py:DebtlessShopCustomerPolicyProjection` | tenant locator, parent serialization, policy projection; add debt-specific predecessor token |
| M9 offers | `app/offers/enums.py:OfferPurpose`; `models.py:OfferVersion, OfferText, OfferAcceptance`; `repository.py:SqlAlchemyCurrentOfferResolver, SqlAlchemyOfferAcceptanceRepository` | `DEBT_ACCEPTANCE` lifecycle, current offer/current-text lock, debt-scoped evidence extension |
| M11 offer readiness | `app/customer_activation/repository.py:SqlAlchemyRegistrationOfferReadiness` | registration-only inherited path remains unchanged |
| Identity/object/document | `app/customer_identity/repository.py:SqlAlchemyCustomerIdentityRepository`; `app/storage/repository.py:load_object_file_for_update`; `app/customer_document/repository.py:SqlAlchemyCustomerDocumentRepository` | inherited total-order classes; M13 does not open them |
| Session | `app/customer_activation/repository.py:SqlAlchemyCurrentSessionRotation`; `app/auth/models.py:Session` | inherited final lock class; closed TX-A avoids overlap for M13 |
| Audit | `app/audit/contracts.py:AuditEventType, AuditObjectType, AuditEvent`; `redaction.py:redact_audit_payload`; `repository.py:append_audit_event`; `models.py:AuditLog` | five typed event/object/payload and SYSTEM-expiry extension |
| Errors/security | `app/auth/error_codes.py:ErrorCode`; `app/security_headers.py:mark_auth_response_no_store`; `app/auth/deps.py:validate_csrf` | stable errors, CSRF, no-store response composition |
| App/database | `app/main.py:create_app`; `app/db.py:_register_database_model_dependencies, create_database_session_factory` | debt/idempotency model and router wiring, phase-owned sessions |
| Alembic/test | `alembic/env.py`; `alembic/versions/`; `tests/postgresql.py:get_alembic_head, M2_CLEANUP_TABLE_NAMES` | one linear migration, model registration, cleanup order, real PG evidence |

## Required bounded M13 placement

`app/debt/` is the new tenant debt package; `app/idempotency/` is the narrow
reusable financial POST replay package. Neither may grow into generic payment,
rating, workflow, or event infrastructure.

| Location | Required responsibility |
|---|---|
| `app/debt/enums.py`, `values.py`, `business_time.py`, `contracts.py` | typed redacted IDs/statuses; Decimal/bps/math; Tashkent/TTL; aggregate, eligibility, transition contracts |
| `app/debt/models.py:Debt` | exact five-status Debt metadata, RESTRICT FKs, named checks/indexes, redacted repr |
| `app/debt/repository.py` | tenant/own non-locking candidates, ordered Debt locks, insert/transition primitives, locked predecessor tokens, exposure/count queries |
| `app/debt/policy.py` | pure policy/open-exposure decision; no payment/rating mutation |
| `app/debt/service.py` | create/accept/reject/cancel/expire orchestration, live rechecks, no borrowed-session ownership |
| `app/debt/dependencies.py` | detached authority and customer/current-shop adapters; no client authority IDs |
| `app/debt/presentation.py`, `router.py` | nine frozen UZ-Latn/RU routes, safe projections, CSRF/PRG/no-store |
| `app/idempotency/contracts.py` | canonical UUID key, digest, request hash, endpoint/result/outcomes with raw-key-safe repr |
| `app/idempotency/models.py:IdempotencyKey` | durable generic key metadata and unique identity |
| `app/idempotency/repository.py` | non-locking completed lookup and exact named-conflict nested-savepoint insert |
| `app/offers/contracts.py` | separate debt acceptance command/snapshot/result without weakening registration-only contracts |
| `app/offers/models.py:OfferAcceptance` | nullable debt FK, purpose/debt check, two partial uniques, redacted debt evidence repr |
| `app/offers/repository.py` | debt-purpose current resolver that locks version/current text; debt-scoped insert/read without a late existing-acceptance lock |
| `app/audit/*`, `app/auth/error_codes.py` | exact audit registry/check/payload/error extensions only |
| `alembic/versions/<M13>.py` | sole child of `e3f4a5b6c7d8`, guarded downgrade and exact central audit extension |

## Lock acquisition map and append proof

| Operation | Required ordered acquisition | Constraint |
|---|---|---|
| M9 registration acceptance | User -> OfferVersion -> OfferAcceptance | remains registration-only and unchanged |
| M11 activation | inherited User -> TelegramLink -> Customer -> OfferVersion -> OfferAcceptance -> Identity/Object/Document -> AuthSession | M13 does not alter it |
| M12 policy/default | Shop -> ShopStaff -> [ShopCustomer] | never acquires earlier classes after ShopCustomer |
| M13 create | Shop -> ShopStaff -> User(UUID ASC) -> TelegramLink -> Customer -> OfferVersion/current text -> ShopCustomer -> IdempotencyKey -> new Debt | all target FK rows are already locked/rechecked |
| M13 accept | User -> TelegramLink -> Customer -> OfferVersion/current text -> [ShopCustomer when policy is consumed] -> Debt -> new OfferAcceptance | no late existing acceptance row lock |
| M13 reject/cancel/expire | relevant earlier authority classes -> [ShopCustomer] -> Debt | Debt candidates UUID ascending; expiry is system actor only |

The combined order is:

```text
Shop -> ShopStaff -> TelegramLinkToken -> OtpDispatch -> OtpChallenge -> User
-> TelegramLink -> Customer -> OfferVersion -> OfferAcceptance
-> CustomerIdentity -> ObjectFile -> CustomerDocument -> ShopCustomer
-> IdempotencyKey -> Debt -> AuthSession
```

Every multi-row same-class lock is UUID ascending. Candidate discovery is
non-locking and followed by a locked recheck. TX-A closes before the M13 domain
transaction, so AuthSession never overlaps its locks.

The one exception is a new debt-scoped acceptance append after locked Debt. It
is lawful only because the service has already locked User, OfferVersion/current
text, and Debt; the new row's earlier FK checks are self-held or compatible, and
Debt serializes the partial unique `debt_id`. A correct second accept observes
the first committed Debt transition and returns its debt projection; it never
attempts to lock an existing acceptance. If a narrow recovery path is needed,
only the named debt-only partial unique index may converge to that Debt
projection without acquiring a late acceptance lock. Every other acceptance
integrity error remains unexpected and is raised.

Idempotency's expected unique race is bounded: same actor operations first
serialize on actor User, then key insertion is after ShopCustomer. Different
actors cannot collide on the actor-scoped unique key. No path may acquire an
IdempotencyKey then return to ShopCustomer.

## Query, transaction, migration, and containment rules

- Shop Debt reads/mutations require current Shop predicate or explicit parent
  join. `shop_customer_id` or `debt_id` alone is never authority.
- Customer reads/mutations follow server-resolved User -> own Customer and
  owner predicate. A customer-provided Customer/User ID is never accepted.
- The target Customer is locked before ShopCustomer for every create/accept
  path that consumes policy. The locked ShopCustomer is rechecked against that
  Customer, so M12's bare tenant locator token is not overextended.
- Create locks ShopCustomer before reading all M13 open debts. Future global
  hard-block producer must lock the same Customer before a cross-shop state
  change. No payment import, cached exposure, or rating write is allowed.
- Current completed key lookup is non-locking and may stop early. New key,
  Debt, and audit are one TX-B mutation. Failure rolls them all back.
- Expected duplicate insert uses a narrow savepoint for only the named
  idempotency constraint. M9 registration's own expected replay path remains
  scoped to registration partial uniqueness. Other integrity failures raise.
- M13 migration creates Debt/IdempotencyKey, extends OfferAcceptance/audit,
  imports models through `app/db.py`, updates exact cleanup ordering, and keeps
  historical M1–M12 tests as source-boundary assertions rather than deleting
  them.
- No SQLite, `create_all`, manual DDL, retry, sleep, NOWAIT, SKIP LOCKED, lock
  timeout, advisory lock, new process, provider I/O, or direct dependency is
  lawful M13 evidence.

## Required test map

| Evidence class | Required M13 test area |
|---|---|
| Values/contracts | IDs/status subsets, money grammar/bps/half-up/zero result, Tashkent and exact TTL, reasons, transitions, redacted reprs |
| Metadata/migration | exact columns/types/checks/FKs/indexes, partial unique behavior, fresh/upgrade/walk/guarded downgrade/re-upgrade, one head |
| Repository/tenant | Shop and own-customer predicates, locator indistinguishability, policy/exposure/count queries, FK and unexpected-conflict handling |
| Idempotency | form/header equality, digest/hash containment, same/different request replay, parallel one key/one Debt/one audit, zero-write failure |
| Legal/lifecycle races | missing/switch/stale offer, one acceptance per debt, accept/reject/cancel/expire one winner, exact active replay |
| Concurrency | same ShopCustomer limit/max, policy-change coherence, cross-shop Customer hard-block fake, UUID order, no lock inversion |
| Atomicity/privacy | audit faults roll back mutation/key/acceptance; reason/key/UA/body/IDs absent from logs/reprs/audits/web |
| Web/security/manual | CSRF/PRG/no-store/CSP/XSS/mobile/Telegram browser, roles/suspension/IDOR, synthetic safe manual statuses/counts |
| Historical/OUT containment | full M1–M12 suite, no payment/rating/notification/scheduler/storage/PII mutation, no dependency/process growth |
