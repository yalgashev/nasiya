# Nasiya M14 Repository Map

This map fixes where the M14 capability belongs, and why its lock and append
properties are feasible in the current repository.

## Existing authority and extension seams

| Location | Existing symbol/seam | M14 responsibility |
| --- | --- | --- |
| `app/db.py` | `_register_database_model_dependencies`, session factory/dependency | Register Payment model; retain request owner semantics. |
| `alembic/env.py` | model imports | Import Payment metadata for the one revision. |
| `app/debt/models.py` | `Debt` | Six persisted statuses, nullable `paid_at`, DB invariants. |
| `app/debt/enums.py` | `DebtStatus`, `M13_PERSISTED_STATUSES` | Expand persisted M14 set without enabling future lifecycle writes. |
| `app/debt/contracts.py` | `DebtAggregate`, `DebtProjection` | Add paid/payment projection data only. |
| `app/debt/business_time.py` | `tashkent_business_date` | Shared due-date calculation; no parallel timezone utility. |
| `app/debt/values.py` | strict Decimal amount parser pattern | Reuse for payment input grammar. |
| `app/idempotency/contracts.py` | endpoint/result/hash contracts | Add `shop.debt_payments.create -> payment` typed result. |
| `app/idempotency/repository.py` | `find_completed_key`, `insert_or_resolve_key` | Existing lookup and named unique-savepoint pattern. |
| `app/audit/*` | contracts, models, redaction, repository | Append the two M14 safe immutable audit events. |
| `app/auth/error_codes.py` | localized error vocabulary | Add the three payment codes in UZ/RU. |
| `tests/postgresql.py` | `M2_CLEANUP_TABLE_NAMES`, `cleanup_m2_tables` | Delete `payments` before debts/users. |

## Bounded M14 package and symbols

`app/payment/` is the only new feature root. Its required modules are
`contracts.py`, `values.py`, `enums.py`, `models.py`, `repository.py`,
`targeting.py`, `policy.py`, `commands.py`, `service.py`, `read_service.py`,
`presentation.py`, and `router.py` (with feature-local templates/static assets
only if required by the SSR pages). It owns the `Payment` model, payment request
hash and typed idempotency projection, locked ledger aggregation, append logic,
tenant/customer read projections, receipt rendering data, and the six routes.

The service’s mutation sequence is fixed: live actor/current-Shop authority and
target visibility; completed-key replay or same-key conflict; locked active
Debt; Tashkent payability; coherent positive remaining; expected revision;
amount-at-most-remaining; then partial/full append. It must compute all
mutation-time totals after acquiring the Debt lock and capture server time once
there. It writes Payment, Debt, IdempotencyKey completion, and AuditLog as one
atomic TX-B unit.

`app/debt/tenant_read_service.py` (`TenantDebtListProjection`,
`TenantDebtDetailProjection`) and `app/debt/customer_read_service.py` receive
only the narrow payment data needed by debt views. Existing targeting/service
seams (`targeting.py`, `service.py`, `creation_eligibility.py`,
`dependencies.py`, `customer_decision_targeting.py`, `tenant_cancel_targeting.py`,
and `expiry_targeting.py`) remain the authoritative M13 boundaries; M14 must
not turn them into a payment implementation package.

## Lock acquisition and append proof

Existing lock helpers provide the intended building blocks:

| Location | Symbol | M14 use |
| --- | --- | --- |
| `app/shop/repository.py` | `lock_shop_for_update`, `lock_actor_shop_staff_for_update` | Live Shop/staff lock and status recheck. |
| `app/auth/repository.py` | `lock_actor_and_target_users_for_update` | UUID-ordered User locking. |
| `app/customer/repository.py` | `lock_active_customer_for_target_user` | Pattern only; payment target lookup must be status-agnostic. |
| `app/shop_customer/repository.py` | `lock_shop_customer_by_pair`, `lock_shop_customer_by_tenant_locator` | Shared predecessor for payment/new-Debt exposure. |
| `app/debt/repository.py` | `LockedDebtPredecessor`, `mark_debt_predecessor_locked`, `lock_debts_in_id_order`, `SqlAlchemyDebtOpenSetReader` | Debt lock ordering and bounded open-set seam. |
| `app/idempotency/repository.py` | `find_completed_key`, `insert_or_resolve_key` | Nonlocking completed lookup; new key before Debt. |

The global forward order is Shop, ShopStaff, TelegramLinkToken, OtpDispatch,
OtpChallenge, User, TelegramLink, Customer, OfferVersion, OfferAcceptance,
CustomerIdentity, ObjectFile, CustomerDocument, ShopCustomer, IdempotencyKey,
Debt, Payment, AuthSession; UUID ascending within a class. Payment’s actual
path is Shop → staff → actor User → target Customer → ShopCustomer → new key →
Debt → new Payment → AuditLog. Existing payment rows are deliberately not
locked after Debt: every lawful append locks that Debt, serialising the ledger;
the Payment FKs point only to already-held actor/Debt rows. No M14 audit path
returns from a later class to an earlier one.

## Exposure handoff and containment

M13's `SqlAlchemyDebtOpenSetReader` is valid for its no-payment fixtures, but
it cannot remain the final exposure authority once active Debt payments exist.
`app/payment/repository.py` supplies a bounded payment-aware adapter/read port
to the M13 creation-eligibility composition point. It computes the active
component from the immutable ledger while holding the same ShopCustomer
predecessor. This makes payment-versus-new-Debt visibility old-or-new complete,
without a dependency from `app.debt` to `app.payment`.

`tests/test_m13_contract_scope.py` protects that containment. Preserve its
source-scoped intent and existing no-payment tests; do not delete, weaken, or
broaden an import solely to make M14 convenient.

## Persistence, migration, and route placement

The migration follows `f4a5b6c7d8e`, creates `payments`, expands Debt schema,
and guards non-empty downgrade. Model registration is added both in `app/db.py`
and `alembic/env.py`; cleanup order is Payment then Debt then User. The model
has no cached or mutable payment columns.

`app/payment/router.py` attaches exactly the shop and customer SSR URLs in the
scope contract. Presentation maps localized errors and redacted values into
feature-local HTML. No router exposes JSON, admin, public self-payment, void,
or mutation other than the one POST.

## Required test placement

Unit tests cover strict parsing, request hash, due-date edge values, formulas,
policy, and redaction. PostgreSQL integration tests cover constraints,
idempotency races, exact/partial concurrent payment, payment-vs-new-Debt
exposure, lock ordering, rollback, migration guard, and no writes on denial.
Web tests cover CSRF/PRG, staff/customer IDOR matrices, suspended-read versus
write behaviour, generic absence, localized errors, receipt/history snapshot
semantics, and no client financial authority. Static contract tests retain M13
containment and reject OUT routes/schema/dependencies. The full M1–M13 suite is
the non-regression floor.
