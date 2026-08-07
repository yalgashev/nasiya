# Nasiya M12 Repository Map

Status: authoritative M12 integration map, based on M12.01–07 read-only audits.
Baseline: M11 docs-only closeout `7d8e14b2da2a77008cf3e999d77aabf277d72137`;
current Alembic head `d2e3f4a5b6c7`. This map names bounded reuse and required
M12 seams; it does not create a generic CRM or customer authority layer.

## Existing authority and persistence seams

| Area | Existing file:symbol | M12 use |
|---|---|---|
| Shop root/staff | `app/shop/models.py:Shop, ShopStaff`; `app/shop/enums.py:ShopRole, ShopStatus` | two root defaults; membership/status source |
| Shop locks | `app/shop/repository.py:lock_shop_for_update, _lock_staff_for_user_for_update, _lock_active_staff_by_id_for_update` | Shop-first actor revalidation and bounded same-class lock-set refactor |
| Current shop | `app/shop/context.py:resolve_current_shop, CurrentShopContext`; `app/shop/dependencies.py:require_shop_staff` | GET current-shop read context; TX-A detached active-shop derivation |
| Shop policy/router | `app/shop/policy.py:can_read_shop, can_mutate_shop`; `app/shop/router.py:router` | suspended read/write semantics and navigation composition |
| User/phone | `app/auth/models.py:User`; `app/auth/phone.py:normalize_uzbekistan_phone, mask_phone_for_display`; `app/auth/repository.py:get_by_phone` | canonical discovery, masked roster, auth-active lock recheck |
| Session/CSRF | `app/auth/deps.py:get_current_session_context, get_detached_mutation_session_context, validate_csrf`; `app/auth/sessions.py:touch_session` | closed TX-A only |
| Rate/IP | `app/auth/rate_limit.py:AuthRateLimiter, AuthRateLimit, hash_rate_limit_key`; `app/request_client_ip.py:resolve_client_ip`; `app/telegram/client_ip.py:ResolvedClientIp` | closed TX-B, HMAC-only four buckets |
| Telegram eligibility | `app/telegram/models.py:TelegramLink`; `app/telegram/repository.py:get_telegram_link_by_user_for_update, is_otp_eligible_telegram_link` | lock/recheck active self-phone-verified generation |
| Customer | `app/customer/models.py:Customer`; `app/customer/repository.py:get_customer_by_user_id, lock_existing_own_customer_for_update` | add dedicated target-active lock; do not use own helper as shop target authority |
| Audit | `app/audit/contracts.py:AuditEventType, AuditObjectType, AuditEvent`; `redaction.py:redact_audit_payload`; `repository.py:append_audit_event`; `models.py:AuditLog` | exact typed M12 event/object/payload extension |
| Errors/settings | `app/auth/error_codes.py:ErrorCode`; `app/settings.py:Settings` | three errors, four bounded integer settings, existing SecretStr HMAC key |
| App/database | `app/main.py:create_app`; `app/db.py:_register_database_model_dependencies, create_database_session_factory` | router/model wiring and phase-owned sessions |
| Security/presentation | `app/security_headers.py:mark_auth_response_no_store`; `app/templates/shop/{workspace,staff,select}.html`; `app/templates/customer/*.html` | M12 route no-store and feature-local templates |
| Alembic | `alembic/env.py`; `alembic/versions/`; `tests/postgresql.py:get_alembic_head` | one linear `e3f4a5b6c7d8` child and model import wiring |
| Test/CI | `tests/conftest.py`; `tests/postgresql.py:M2_CLEANUP_TABLE_NAMES`; `.github/workflows/ci.yml` | real PG fixture/cleanup, frozen sync, Ruff, Alembic, full pytest |

## Required bounded M12 placement

`app/shop_customer/` is the required new bounded domain package. It owns only
ShopCustomer values, contracts, repositories, services, presentation, and
router composition. It is not a generic customer repository, a search service,
or a debt platform.

| Location | Required responsibility |
|---|---|
| `app/shop_customer/models.py:ShopCustomer` | exact PII-free relationship model |
| `app/shop_customer/values.py` and contracts | UUID/value/status/whole-UZS/revision typed contracts with redacted reprs |
| `app/shop_customer/repository.py` | tenant-scoped relation lookup/lock/create/list; `shop_id` predicate on every shop-facing query |
| `app/shop_customer/service.py` | link/default/policy coordination; no borrowed-session ownership |
| `app/shop_customer/dependencies.py` | detached actor/current-shop TX-A adapter; server-derived IDs only |
| `app/shop_customer/rate_limit.py` | four M12 bucket construction over inherited limiter |
| `app/shop_customer/presentation.py` | UZ-Latn/RU safe view models and fixed messages |
| `app/shop_customer/router.py:router` | six frozen routes, PRG/no-store composition |
| `app/shop/repository.py` | public bounded Shop/ShopStaff lock-set helper preserving Shop-first order |
| `app/auth/repository.py` | UUID-ascending User lock-set helper after ShopStaff |
| `app/telegram/repository.py` | target-user-ID lock helper; current eligibility predicate remains authoritative |
| `app/customer/repository.py` | explicit target-user active Customer lock helper, never an own-user authority alias |
| `app/audit/*`, `app/auth/error_codes.py`, `app/settings.py` | frozen audit/error/settings extensions only |

## Lock acquisition map

| Existing operation | Existing ordered acquisition | M12 compatibility requirement |
|---|---|---|
| M5 staff/status mutation | `Shop -> actor ShopStaff -> target ShopStaff` | preserve Shop-first; any multi-staff set locks IDs ascending |
| M11 token/link transition | `TelegramLinkToken -> OtpDispatch -> OtpChallenge -> User -> TelegramLink -> Customer` as applicable | M12 never acquires Shop after an inherited later class |
| M11 activation | User/Link/Customer followed by Offer/Identity/Object/Document as applicable | M12 link ends at Customer/ShopCustomer and does not open M9/M10 rows |
| M9 acceptance | `OfferVersion -> OfferAcceptance` | unchanged and unused by M12 |
| M10 identity/document | `Customer -> CustomerIdentity`; `Customer -> ObjectFile -> CustomerDocument` | unchanged and unused by M12 |
| M12 link | `Shop -> ShopStaff -> User(UUID ASC) -> TelegramLink -> Customer -> ShopCustomer` | mandatory hot path |
| M12 defaults/policy | `Shop -> ShopStaff -> [ShopCustomer]` | no User, Link, Customer, or AuthSession acquired afterward |

The combined total order is:

```text
Shop -> ShopStaff -> TelegramLinkToken -> OtpDispatch -> OtpChallenge -> User
-> TelegramLink -> Customer -> OfferVersion -> OfferAcceptance
-> CustomerIdentity -> ObjectFile -> CustomerDocument -> ShopCustomer
-> AuthSession
```

TX-A authentication/session touch/current-shop resolution and TX-B rate
check-and-record end before TX-C. Therefore AuthSession and rate locks do not
overlap M12's domain locks. Candidate discovery is non-locking and always
followed by locked revalidation. No unbounded inverse was found by M12.03.

## Query and transaction rules

- Shop-facing ShopCustomer reads/updates use `shop_id == current_shop_id` or an
  explicit parent join. A `shop_customer_id` alone is never authorization.
- Customer own view uses only server-resolved current User → Customer ownership
  and returns shop names; it never accepts a client customer ID.
- Link TX-A returns detached actor and active-shop IDs only. TX-B HMACs raw
  server actor/shop IDs, trusted IP, and transient phone; it persists only the
  inherited typed hash columns. TX-C owns locks, mutation, and audit.
- Target discovery is not a public search API. Invalid/missing/disabled/draft/
  unverified target outcomes converge before response construction.
- Expected duplicate insertion uses a narrow nested savepoint and converges to
  idempotent existing-pair success; a different integrity error is not hidden.
- Every domain mutation and its audit append share one outer transaction. Rate
  attempts remain committed when TX-C subsequently denies or rolls back.

## Migration, metadata, cleanup, and containment

Revision `e3f4a5b6c7d8` has parent `d2e3f4a5b6c7`. Its ordered work is: add two
checked defaults with server backfill; create exact `shop_customers` constraints
and indexes; replace/extend central-audit DB checks atomically; preserve zero
initial link rows. Both `alembic/env.py` and
`app/db.py:_register_database_model_dependencies` import M12 models.

`tests/postgresql.py:M2_CLEANUP_TABLE_NAMES` adds `shop_customers` before
`customers`, `shops`, and `users`, while retaining the existing explicit
allowlist and no schema reset. Historical M10/M11 guards that forbid
`shop_customer` or pin exact M11 inventory are reframed as source-boundary
assertions in the same diff that adds exact M12 metadata, table-count, cleanup,
and CI-head assertions. No historical guard is deleted or broadly allowed.

## Required test map

| Evidence class | Required M12 test area |
|---|---|
| Values/contracts | whole-UZS parsing, bounds, list status, role matrix, outcomes, redacted reprs |
| Metadata/migration | exact columns/types/defaults/checks/FKs/indexes, upgrade/walk/guarded downgrade/re-upgrade |
| Repository/tenant | active target matrix, mandatory shop predicate, own-view isolation, cross-tenant indistinguishability |
| Concurrency | same-pair one row/one audit, defaults-vs-link coherent pair, stale policy winner, link-vs-suspend/revoke/activation/relink |
| Atomicity | audit failure rollback, no-op/replay no audit/revision, rate persistence isolated from domain rollback |
| Web/security | CSRF/PRG/no-store/CSP/XSS/mobile, blank phone after POST, no client authority IDs, role/suspension/platform-admin/IDOR matrix |
| Static containment | no commit/full rollback/close, no SQLite/create_all/manual DDL, no sleep/retry/advisory correctness, no M10 decrypt or OUT mutation |
| Manual | M12.54 linking/role/suspension/IDOR and M12.55 defaults/policy/stale/own-view, safe counts/statuses only |
