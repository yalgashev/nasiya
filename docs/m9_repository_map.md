# Nasiya M9 Repository Map

Status: authoritative M9.03–M9.08 repository map, including FINAL
`CR-M9-01`.
Baseline: `5429e950d0ef25dcb99617e7ca109b1aa08fc697`.
All `EXISTS` entries below were verified in the baseline. `MISSING` means no
matching production authority exists; it is not an invitation to invent one.
`APPROVED MINIMAL` names a future M9 integration point fixed by
`docs/m9_scope_contract.md` and `docs/m9_decisions.md`, not existing code.

## Runtime, DI, And Transaction Ownership

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `app/db.py:Base` | Shared SQLAlchemy declarative metadata. | M9 models inherit this base. |
| EXISTS | `app/db.py:create_database_engine` | Builds the configured PostgreSQL engine. | No M9 engine/config abstraction. |
| EXISTS | `app/db.py:create_database_session_factory` | Shared `Session` factory. | Concurrency tests use independent sessions from this pattern. |
| EXISTS | `app/db.py:create_database_session_dependency` | Request outer owner commits after successful dependency use, fully rolls back on exception, and always closes. | Offer routes borrow this dependency; repository/service never commit/full-rollback/close. |
| EXISTS | `app/auth/deps.py:get_database_session` | Bridges request app state to the outer transaction dependency. | All M9 routes use `scope="function"`. |
| EXISTS | `app/main.py:create_app` | Composes routers, DB state, exception handling, static files, and security middleware. | Include the future offer router only here. |

## Authentication, Session, And CSRF

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `app/auth/models.py:User` | UUID user, phone/password, `is_active`; no role/admin field. | Add only the approved `is_platform_admin` boolean. |
| EXISTS | `app/auth/models.py:Session` | Server-side session row with user, expiry, CSRF secret, bounded UA, and optional active shop. | M9 stores no token, cookie, CSRF, or session ID. |
| EXISTS | `app/auth/deps.py:CurrentSessionContext` | Resolves anonymous/authenticated/invalid/revoked/expired/inactive state. | Account flow reuses it; do not log/repr it because its current repr includes session ID. |
| EXISTS | `app/auth/deps.py:get_current_session_context` | Cookie-to-server-session resolution and touch. | M9 does not parse cookies itself. |
| EXISTS | `app/auth/deps.py:require_user` | Active authenticated-user boundary with HTML login redirect. | Acceptance requires this boundary. |
| EXISTS | `app/auth/deps.py:validate_csrf` | Session-bound form/header CSRF for every unsafe method. | Every M9 POST declares this dependency. |
| EXISTS | `app/auth/deps.py:CsrfFailed` / `csrf_failed_exception_handler` | Stable `CSRF_FAILED`, safe HTML/HTMX/JSON, no-store. | Reuse unchanged. |
| EXISTS | `app/auth/csrf.py:CsrfToken` / `verify_csrf_token` | Typed redacted token and constant-time validation against active session. | Token only enters hidden form/header and validation. |
| EXISTS | `app/auth/template_context.py:with_csrf_context` | Adds a form token to a local template context. | M9 mutation forms reuse it; no global token context. |
| EXISTS | `app/auth/cookies.py:set_session_cookie` / `delete_session_cookie` | HttpOnly server-session cookie lifecycle. | No new M9 cookie authority. |

## Platform Authorization Audit

| Status | Real file:symbol | Audit result | M9 consequence |
|---|---|---|---|
| MISSING | `app/auth/models.py:User` | Fields end at `is_active`, `created_at`, and `updated_at`; there is no platform authority. | Add the approved boolean column. |
| EXISTS, NOT REUSABLE | `app/shop/enums.py:ShopRole` | `OWNER`, `MANAGER`, and `CASHIER` are tenant membership roles. | Never treat a shop role as platform admin. |
| EXISTS, DEFERRED EVIDENCE | `app/shop/service.py` module contract | States production platform-admin authorization is deferred to an admin milestone. | Confirms there is no reusable guard. |
| MISSING | production `require_platform_admin` / admin router / bootstrap | Repository-wide symbol audit found none. | Use the approved offer-scoped typed actor and first-admin CLI minimum; no full admin suite. |
| APPROVED MINIMAL | `app/offers/authorization.py:PlatformAdminActor` / `require_platform_admin_actor` / `assert_platform_admin_actor` | Future offer-only authorization adapter. | Route checks the dependency; service re-checks the actor row before mutation. |
| APPROVED MINIMAL | `app/cli.py:bootstrap_platform_admin` | Future first-admin operator command. | Existing active user only, and only while admin count is zero. |

## Error And Localization Style

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `app/auth/error_codes.py:ErrorCode` | Shared stable English codes. | Add the six frozen offer codes only. |
| EXISTS | `app/auth/error_codes.py:get_error_http_status` / `get_public_error_body` | Safe public mapping discards internal detail. | Offer errors must not include title/body, evidence, actor, or SQL detail. |
| EXISTS | `app/otp/web_presentation.py:OtpWebLanguage` | UZ-Latn/RU UI enum. | Pattern only; legal language remains separate. |
| EXISTS | `app/otp/web_presentation.py:resolve_otp_web_language` / `get_otp_web_copy` | Pure cookie/Accept-Language resolution and immutable copy map. | Follow the pure-map style without importing OTP semantics. |
| EXISTS | `app/telegram/web_presentation.py:TelegramWebLanguage` / `get_telegram_web_copy` | A second narrow UZ-Latn/RU presentation map. | Confirms repository uses feature-local localization. |
| MISSING | shared application/profile locale symbol | No locale field exists on `User`, customer, or session models. | M9 uses a narrow UZ-Latn/RU resolver; selected legal language is explicit. |

## Templates, Rendering, And Headers

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `app/main.py:templates` and feature router `templates` objects | Jinja2 filesystem templates. | Offer router follows feature-local template composition. |
| EXISTS | `app/templates/base.html` | Autoescaped base layout with dynamic page language. | Offer templates extend it. |
| EXISTS | `app/templates/auth/account.html` | Authenticated account navigation and CSRF form pattern. | Link only to current registration offer; no activation UI. |
| EXISTS | `app/templates/shop/staff.html` | Labeled mutation forms and per-form CSRF fields. | Reuse form/accessibility shape, not tenant authorization. |
| EXISTS | `app/security_headers.py:install_security_headers_middleware` / `set_security_headers` | CSP forbids inline scripts and adds standard security headers. | No inline JS or CSP weakening. |
| EXISTS | `app/security_headers.py:mark_auth_response_no_store` | Sets `Cache-Control: no-store`. | Apply to every M9 admin/account response, redirect, error, and fragment. |
| EXISTS | `app/customer/router.py:onboarding_page` / `start_onboarding` | Thin route, auth context, CSRF, PRG, no-store example. | Reuse mechanics only; do not call customer onboarding/activation. |
| EXISTS | `app/shop/router.py:_render_forbidden` | Safe 403/no-store rendering with stable code. | Reuse the response convention for authenticated non-admin. |

## SQLAlchemy, Alembic, And Constraint Style

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `app/auth/models.py:utc_now` and model declarations | UUID, named checks/FKs, aware `DateTime(timezone=True)`. | M9 follows types/naming and uses injected time in services. |
| EXISTS | `app/shop/models.py:ShopStatusEvent` / `ShopStaffEvent` | Append-shaped domain events with explicit columns. | Pattern evidence only; not a central audit authority. |
| EXISTS | `app/telegram/models.py:TelegramLinkEvent` | Narrow Telegram event journal. | Not reusable for offer audit. |
| EXISTS | `app/otp/models.py:OtpChallengeEvent` | Narrow OTP event journal. | Not reusable for offer audit. |
| EXISTS | `alembic/env.py:target_metadata` / `run_migrations_online` | Imports every model package into `Base.metadata`; Alembic owns schema change. | Import future offer and audit models; no manual DDL/create_all. |
| EXISTS | `alembic/versions/f8a9b0c1d2e3_create_object_files.py:upgrade` / `downgrade` | Current single head and named PostgreSQL schema objects. | M9 revision is one linear child of `f8a9b0c1d2e3`; no M9 revision exists yet. |
| EXISTS | `app/telegram/repository.py` expected-conflict functions | `Session.begin_nested()`, `flush()`, and exact `diag.constraint_name`. | Replay/version unique handling uses this pattern. |
| EXISTS | `app/shop/repository.py:lock_shop_for_update` | `SELECT ... FOR UPDATE` row locking. | Current switch locks purpose rows in stable UUID order. |
| EXISTS | `app/storage/repository.py:load_object_file_for_update` / `_lock_transition_row` | Repository lock/transition guard pattern. | Offer repository exposes explicit lock methods. |

## Time And Browser Metadata

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `app/auth/deps.py:get_current_time` | Injectable `datetime.now(UTC)` request time. | Approval/current/accept routes inject one time. |
| EXISTS | `app/auth/sessions.py:_as_utc` | Rejects naive timestamps and normalizes aware time to UTC. | Offer domain has the same strict aware-time rule. |
| EXISTS | `app/auth/rate_limit.py:_as_utc` | Another strict aware UTC boundary. | Confirms naive legal-review time must fail. |
| EXISTS | `app/auth/user_agent.py:MAX_USER_AGENT_LENGTH` / `truncate_user_agent` | Bound is 512, but control characters are preserved. | Reuse the bound and add the exact narrow normalization in M9. |
| EXISTS | `app/auth/user_agent.py:get_user_agent_metadata` | Safe browser/device display classification plus truncated raw value. | Classification may be displayed; raw UA never enters audit/log/error. |

## Locking, Savepoint, And Coordinator Examples

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `app/auth/rate_limit.py:check` | Locked counter read with `with_for_update()`. | Confirms PostgreSQL row-lock convention. |
| EXISTS | `app/otp/issuance.py` locked-user/link queries | Stable lock acquisition before mutation. | Current and acceptance resolvers lock before validating. |
| EXISTS | `app/telegram/repository.py:invalidate_and_insert_telegram_link_token` | Expected insert conflict isolated by nested transaction and exact constraint name. | Acceptance replay/version race uses named savepoint handling. |
| EXISTS | `app/shop/service.py:add_staff` and other mutations | Service flushes, caller commits; event failure is atomic. | Offer service follows the same ownership. |
| EXISTS | `app/telegram/update_processing.py:process_telegram_update_tx_a` | Non-request coordinator owns `session_factory.begin()`. | Pattern is available, but M9 has no worker/external I/O. |
| EXISTS | `app/storage/service.py:reconcile_stale_object_uploads` | Coordinator creates short explicit transaction phases. | Not needed by M9; no scheduler/reconciliation is added. |

## Audit And Redaction Audit

| Status | Real file:symbol | Audit result | M9 consequence |
|---|---|---|---|
| EXISTS, NARROW | `app/shop/repository.py:add_shop_status_event` / `add_shop_staff_event` | Shop-only explicit event writes. | Cannot accept generic offer events. |
| EXISTS, NARROW | `app/telegram/events.py:append_telegram_link_event` | Telegram-only append helper and UTC guard. | Pattern evidence only. |
| EXISTS, NARROW | `app/otp/repository.py:append_challenge_event` | OTP-only event helper. | Pattern evidence only. |
| EXISTS, REDACTION PRIMITIVES | `app/auth/csrf.py:CsrfToken`, `app/auth/sessions.py:RawSessionToken`, `app/telegram/inbound.py:VerifiedPrivateTelegramChatIdentity` | Sensitive typed values redact default string/repr. | M9 sensitive DTOs must use `repr=False` and safe custom repr. |
| MISSING, APPROVED MINIMAL | central append-only audit/redaction service and `audit_log` model/table | Repository-wide audit found no production symbol or table. | FINAL `CR-M9-01` authorizes exactly one support table and the narrow future `app/audit/` boundary; no generic audit features. |

## Real PostgreSQL And Test Fixtures

| Status | Real file:symbol | Existing contract | M9 integration |
|---|---|---|---|
| EXISTS | `tests/conftest.py:test_database_url` | Fails the run when a valid test DB is unavailable. | No skip fallback. |
| EXISTS | `tests/conftest.py:test_database_engine` / `m2_test_database` | Session engine and per-test cleanup. | Add M9 tables to deterministic cleanup after migration exists. |
| EXISTS | `tests/postgresql.py:validate_test_database_url` | Rejects SQLite/non-PostgreSQL and requires `_test` database. | All M9 integration tests use it. |
| EXISTS | `tests/postgresql.py:get_alembic_head` | Requires one Alembic head. | Must become the exact M9 revision after migration. |
| EXISTS | `tests/postgresql.py:M2_CLEANUP_TABLE_NAMES` / `cleanup_m2_tables` | FK-aware explicit cleanup order. | Add acceptances, texts, versions before users. |
| EXISTS | `tests/test_storage_migration_postgresql.py:test_empty_to_head_parent_walk_and_repeat_upgrade_preserve_inherited_tables` | Empty/head and parent walk on real PostgreSQL. | M9 extends with `M8 -> M9 -> M8 -> M9`. |
| EXISTS | `tests/test_storage_persistence_concurrency_postgresql.py:test_expected_duplicate_inside_savepoint_leaves_session_usable` | Exact constraint and usable-session assertion. | Required for replay/current conflict recovery. |
| EXISTS | `tests/test_shop_service_concurrency.py` ThreadPool/Barrier helpers | Deterministic independent-session concurrency style. | Use for two make-current requests. |
| EXISTS | `tests/test_otp_issuance_postgresql.py:test_parallel_issue_requests_have_exactly_one_final_outstanding` | Real PostgreSQL exactly-one-final-state pattern. | Adapt to one current and one acceptance replay row. |

## Security And Containment Test Anchors

| Real file:symbol | M9 reuse/extension |
|---|---|
| `tests/test_shop_route_security_matrix.py:test_all_shop_routes_have_explicit_security_classification` | Add an offer-specific role/route inventory for anonymous, account, shop roles, and platform admin. |
| `tests/test_csrf_route_inventory.py:test_production_unsafe_routes_are_csrf_protected` | Every M9 POST must appear automatically in the unsafe-route inventory. |
| `tests/test_csrf_dependency.py:test_missing_empty_or_wrong_token_fails` | Extend with missing/wrong/cross-session M9 cases. |
| `tests/test_csrf_template_context.py:test_csrf_template_does_not_use_safe_filter` | Keep offer forms autoescaped and locally tokenized. |
| `tests/test_shop_get_leakage_xss_audit.py:test_rendered_shop_name_is_jinja_escaped` | Pattern for legal title/body XSS canaries. |
| `tests/test_otp_sensitive_data_audit.py:test_e2e_flow_keeps_sensitive_values_out_of_db_html_urls_and_logs` | Pattern for body/UA/token/session leakage canaries. |
| `tests/test_storage_sensitive_data_audit.py:test_m8_production_modules_have_no_body_or_exception_output_sink` | Pattern for production-source leakage scan. |
| `tests/test_shop_service_transaction_atomicity.py:test_event_append_failure_rolls_back_state_with_caller_rollback` | Required same-transaction audit failure semantics. |
| `tests/test_storage_scope_containment.py:test_m5_m6_m7_roles_main_composition_and_tt_are_immutable` | Extend containment through M8 and forbidden M9 capabilities. |

## Approved M9 File Integration Map

These paths/symbols are fixed future placement, not baseline existence:

| Future file:symbol | Responsibility |
|---|---|
| `app/audit/contracts.py:AuditEvent` / `AuditEventType` / `AuditObjectType` | Seven-event/four-object typed M9 registry and redacted event input. |
| `app/audit/redaction.py:redact_audit_payload` | Per-event safe-key allowlists, bounded scalar output, unknown-key removal. |
| `app/audit/models.py:AuditLog` | The single CR-M9-01 `audit_log` supporting table. |
| `app/audit/repository.py:append_audit_event` | Insert/flush only in caller-owned transaction; no query/update/delete API. |
| `app/offers/enums.py:OfferPurpose` / `OfferLanguage` / `OfferStatus` | Exact domain allowlists. |
| `app/offers/content.py:canonicalize_offer_text` / `compute_offer_content_hash` | The only canonicalization/hash implementation. |
| `app/offers/contracts.py:LegalReviewEvidence` / `AcceptCurrentRegistrationOffer` / `RegistrationOfferAcceptance` | Immutable offer commands/evidence and narrow repository ports. |
| `app/offers/models.py:OfferVersion` / `OfferText` / `OfferAcceptance` | Three-table ORM mapping. |
| `app/offers/repository.py` explicit functions | Create/list/get, purpose locks, current resolver, text upsert, acceptance create/re-read. |
| `app/offers/authorization.py:PlatformAdminActor` / `require_platform_admin_actor` / `assert_platform_admin_actor` | Tenant-independent route/service authority. |
| `app/offers/service.py` lifecycle and acceptance functions | All business rules; no commit/full rollback/close. |
| `app/offers/web_presentation.py:OfferWebLanguage` / `resolve_offer_web_language` / `get_offer_web_copy` | UZ-Latn/RU safe copy; separate legal language. |
| `app/offers/router.py:router` | The nine exact routes frozen in scope contract. |
| `app/templates/offers/admin_list.html` / `admin_detail.html` / `admin_new.html` / `registration_offer.html` | Autoescaped, no-inline-script, no-store UI. |
| `app/main.py:create_app` | Include `app.offers.router:router`. |
| `alembic/env.py:target_metadata` | Import offer and audit models. |
| `app/auth/error_codes.py:ErrorCode` | Add only the frozen offer errors. |
| `app/auth/models.py:User` | Add only `is_platform_admin`. |
| `app/cli.py:build_parser` / `bootstrap_platform_admin` | First-admin-only operator bootstrap. |

No M9 Alembic revision file or M9 production symbol exists at this baseline,
so this map does not invent a revision identifier or claim implementation.

## Readiness Gaps

The baseline still has no implemented platform-admin or central audit
authority. Their exact minimal implementations are now authorized by
PO-M9-07 and FINAL CR-M9-01 respectively, and the guide places their
model/migration/port work before lifecycle mutation tasks. No unresolved
authority decision remains at M9.08; failure to stay within either approved
minimum is a later task blocker.
