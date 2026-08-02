# Nasiya M11 Repository Map

Status: authoritative M11.01–M11.08 readiness and executable placement map.
Baseline: `17ebbe166d63a32e3b7eaa3eb3838f578d9b7780`.
Correction: `CR-M11-01 — FINAL APPROVED` controls all lock acquisition.

This map records source authority, current symbols, bounded future symbols,
exact schema names and the M11.07 threat matrix. It implements no product code.

## Source Classification

| Source | Class | Use |
|---|---|---|
| `docs/tt_nasiya_web_v1.md` | AUTHORITATIVE | Product registration, Telegram, OTP, activation, session, audit, privacy and web requirements. |
| External M11 Final Scope Freeze | AUTHORITATIVE | One-capability narrowing and PO-M11-01..25. |
| `CR-M11-01 — FINAL APPROVED` | AUTHORITATIVE CORRECTION | Replaces only the executable global lock order and bounded refactor assumptions. |
| `nasiya_m11_product_gate.md` | AUTHORITATIVE GATE | Entry/stop evidence and deferred public-bootstrap boundary. |
| `docs/m10_scope_contract.md`, `docs/m10_decisions.md` | INHERITED | Identity/document/draft-only mutation and privacy boundary. |
| `docs/m9_scope_contract.md`, `docs/m9_decisions.md` | INHERITED | Current offer/immutable acceptance and transaction/audit boundary. |
| M7 OTP and M4/M6 Telegram closed implementation | INHERITED | LOGIN MAC/lifecycle/dispatcher/link behavior to preserve. |
| M2/M3 auth/customer/session implementation | INHERITED | Existing-account, own customer, outer transaction and session primitives. |
| M10/M9 result/final reports and current repository/tests | INFORMATIVE | Closure and exact integration evidence; cannot widen semantics. |

Public account bootstrap, customer lead and `shop_customer` remain deferred and
OUT. Repository gaps cannot replace or contradict product authority.

## Current Exact File:Symbol Map

| Area | Existing reuse point |
|---|---|
| Customer model | `app/customer/models.py:Customer`, `CUSTOMER_ONBOARDING_STATUS_DRAFT` |
| Own customer locks | `app/customer/repository.py:load_existing_own_customer_draft_for_update`, `get_customer_by_user_id` |
| OTP vocabulary | `app/otp/contracts.py:OtpPurpose`, `OtpChallengeStatus`, `OtpDispatchStatus`, `OtpChallengeEventAction`, `OtpInternalOutcome` |
| OTP code/MAC | `app/otp/code.py:OtpCode`, `generate_otp_code`; `app/otp/crypto.py:compute_otp_code_mac`, `verify_otp_code_mac`, `derive_browser_binding_digest` |
| OTP persistence | `app/otp/models.py:OtpChallenge`, `OtpDispatch`, `OtpChallengeEvent`, `OtpDispatcherState` |
| OTP repository locks | `app/otp/repository.py:load_outstanding_challenge_by_user_for_update`, `load_outstanding_challenge_by_browser_for_update`, `load_outstanding_challenges_by_user_for_update`, `load_dispatch_by_challenge_for_update`, `claim_next_pending_dispatch` |
| OTP issue/new-code | `app/otp/issuance.py:request_login_otp`, `request_new_login_code`, `_terminalize_existing_outstanding`, `invalidate_login_otp_challenges_for_link_change` |
| OTP verification | `app/otp/verification.py:verify_login_otp`, `check_login_otp_candidate` |
| Dispatcher | `app/otp/dispatch_service.py:prepare_next_otp_dispatch`, `record_otp_delivery_result`; `app/otp/dispatcher.py:run_otp_dispatcher_iteration` |
| Message/provider | `app/otp/message.py:format_login_otp_message`; `app/otp/provider.py:TelegramOtpProvider` |
| Settings | `app/settings.py:Settings`, `OtpHmacKeySettingsError`; existing `otp_login_*` and rate-limit settings |
| Rate limiter/IP | `app/auth/rate_limit.py:RateLimiter`; `app/telegram/client_ip.py:resolve_trusted_client_ip` |
| Telegram link locks | `app/telegram/repository.py:get_telegram_link_by_user_for_update`, `get_valid_telegram_link_token_for_consume_by_hash_for_update`, `unlink_verified_private_chat`, `relink_verified_private_chat` |
| Link services | `app/telegram/service.py:consume_start_token`, `unlink`; challenge invalidation in `app/otp/issuance.py` |
| Offer current locks | `app/offers/repository.py:SqlAlchemyOfferVersionRepository.lock_versions_for_purpose`, `_lock_version_rows_for_purpose` |
| Offer acceptance | `app/offers/repository.py:SqlAlchemyOfferAcceptanceRepository`, `SqlAlchemyHasAcceptedCurrentRegistrationOffer`; `app/offers/models.py:OfferAcceptance` |
| Identity lock/auth | `app/customer_identity/repository.py:SqlAlchemyCustomerIdentityRepository.lock_identity`; `app/customer_identity/service.py:CustomerIdentityCompletenessService`, `_decrypt_verified_summary` |
| Document/object | `app/customer_document/repository.py:SqlAlchemyCustomerDocumentRepository.lock_current_documents`; `app/customer_document/service.py:has_current_customer_identity_document`; `app/storage/repository.py:load_object_file_for_update` |
| Central audit | `app/audit/contracts.py:AuditEventType`, `AuditObjectType`, `AuditEvent`; `app/audit/redaction.py`; `app/audit/repository.py:append_audit_event` |
| Session/CSRF/cookie | `app/auth/sessions.py:rotate_session`, `_create_session`; `app/otp/session_login.py:rotate_session_after_otp_consume`; `app/auth/csrf.py`; `app/auth/cookies.py:set_session_cookie` |
| Transaction owner | `app/db.py:create_database_session_dependency` |
| Web composition | `app/main.py:create_app`; `app/customer/router.py`; `app/auth/router.py`; Jinja templates under `app/templates/` |
| Alembic | `alembic/versions/b0c1d2e3f4a5_create_customer_identity_foundation.py`; `alembic/env.py` |
| PostgreSQL tests | `tests/postgresql.py:postgresql_engine`, `M2_CLEANUP_TABLE_NAMES`; function-scoped cleanup fixtures |
| CI | `.github/workflows/ci.yml`: frozen sync, Ruff, format, Alembic, PostgreSQL full pytest and zero-skip guard |

## Bounded Future Placement

| File | Exact planned symbols/responsibility |
|---|---|
| `app/customer_activation/contracts.py` | `CustomerActivationActor`, `RegistrationReadinessSnapshot`, typed issue/new-code/verify commands/results/outcomes; redacted reprs |
| `app/customer_activation/repository.py` | CR-order readiness selector, deterministic acceptance selector, document/object recheck, customer transition adapter |
| `app/customer_activation/service.py` | side-effect-free readiness, issue/new-code, snapshot/live recheck, atomic activation coordinator |
| `app/customer_activation/session.py` | current-AuthSession-last activation rotation adapter/result |
| `app/customer_activation/presentation.py` | UZ-Latn/RU copy and PII-free view models |
| `app/customer_activation/router.py` | exact four authenticated fixed routes and PRG/cookie composition |
| `app/otp/contracts.py` | exact purpose/action/outcome extensions only |
| `app/otp/models.py` | four nullable registration-context columns and purpose check |
| `app/otp/repository.py` | purpose-aware dispatch-first/challenge lock helpers and typed create context |
| `app/otp/issuance.py` | purpose-local helpers plus link-change invalidation for both purposes |
| `app/otp/verification.py` | preserve LOGIN; bounded shared MAC/candidate primitives only |
| `app/otp/message.py`, `app/otp/provider.py` | typed registration message; same provider/transport |
| `app/otp/dispatch_service.py` | persisted-purpose prepare branch and draft recheck |
| `app/customer/models.py`, `app/customer/repository.py` | `active`, `activated_at`, own-customer lock independent of state, one-way transition |
| `app/offers/repository.py` | deterministic exact-current acceptance lock/select adapter |
| `app/customer_identity/service.py` | authenticated completeness result returning only positive revision |
| `app/customer_document/service.py`, `repository.py` | trusted candidate discovery plus ObjectFile-before-Document exact snapshot recheck |
| `app/auth/sessions.py` | activation rotation preserving current session safe context and shop |
| `app/telegram/service.py`, `repository.py` | CR-order invalidation and active ordinary-unlink guard |
| `app/audit/contracts.py`, `models.py`, `redaction.py` | exact activation event/object/payload extension |
| `app/auth/error_codes.py` | four exact stable codes and UZ-Latn safe definitions |
| `alembic/versions/c1d2e3f4a5b6_create_customer_activation_foundation.py` | one zero-new-table M11 revision |

No other package, table, dependency, worker, dispatcher, broker or route family
is planned.

## Corrected Acquisition Map

| Operation | Closed pre-phase | Domain acquisition |
|---|---|---|
| Readiness GET | auth/session-touch committed | Side-effect-free non-locking read only |
| Registration rate | auth/CSRF/IP resolved | Separate check-and-record transaction; closes before domain work |
| Issue/new-code | rate phase committed | open dispatches UUID asc -> outstanding challenges UUID asc -> User -> TelegramLink -> Customer -> OfferVersion set UUID asc -> valid OfferAcceptance rows `accepted_at,id` -> CustomerIdentity -> candidate ObjectFile -> current CustomerDocument |
| Dispatcher prepare | none | OtpDispatch -> OtpChallenge -> User -> TelegramLink -> Customer; commit/close before send |
| Dispatcher result | send completed session-free | OtpDispatch -> OtpChallenge; typed result/event only |
| Verify | auth/session-touch committed | candidate OtpDispatch if present -> OtpChallenge -> User -> TelegramLink -> Customer -> OfferVersion -> snapshot OfferAcceptance -> CustomerIdentity -> snapshot ObjectFile -> CustomerDocument -> current AuthSession |
| Offer switch | none | existing OfferVersion purpose-set order; activation waiting on it sees new current after commit |
| Identity update | none | Customer -> CustomerIdentity, inherited M10 |
| Document update | RL/TX-A/M8 phases inherited | Customer -> ObjectFile -> CustomerDocument, inherited M10 |
| Unlink/protected relink | optional link-token pre-anchor | affected OtpDispatch rows UUID asc -> affected OtpChallenge rows UUID asc -> User -> TelegramLink rows stable -> Customer |
| Session-only flows | auth/session pre-phase where applicable | AuthSession only; no reverse domain acquisition |

### Candidate And Revalidation Rules

- Candidate IDs derive from authenticated user/browser/persisted relations,
  never from client authority.
- Deterministic acceptance selector joins acceptance to current version/text,
  validates purpose/version/language/hash, locks selected/all candidate
  acceptance rows after OfferVersion, and chooses `accepted_at ASC, id ASC`.
- Identity adapter locks the 1:1 row, decrypts/authenticates and blind-index
  rechecks in memory, and returns only `IdentityRevision`.
- Document candidate discovery reads current document/object IDs without lock;
  then ObjectFile is locked first, CustomerDocument second, and the relation,
  CURRENT status, AVAILABLE status and allowed image type are revalidated.
- Any inserted/deleted/switched candidate between discovery and locking yields
  a safe readiness failure or snapshot invalidation, never mixed authority.

## Exact Schema Appendix

Migration revision `c1d2e3f4a5b6`, down revision `b0c1d2e3f4a5`.

New customer column/check names:

```text
customers.activated_at TIMESTAMPTZ NULL
ck_customers_onboarding_status_allowed
ck_customers_activation_state_consistent
ck_customers_timestamp_order
```

New challenge columns/names:

```text
customer_id UUID NULL
registration_offer_acceptance_id UUID NULL
customer_identity_revision INTEGER NULL
customer_document_id UUID NULL
ck_otp_challenges_purpose_allowed
ck_otp_challenges_registration_context_matches_purpose
fk_otp_challenges_customer_id_customers_id
fk_otp_challenges_registration_acceptance_offer_acceptances
fk_otp_challenges_customer_document_id_customer_documents
```

Replaced/extended existing checks:

```text
ck_otp_challenge_events_action_allowed
ck_audit_log_event_type_allowed
ck_audit_log_object_type_allowed
ck_audit_log_object_matches_event
ck_audit_log_payload_exact_shape
```

Existing purpose-bearing OTP partial uniques are unchanged. No new index or
table is required. All FKs use `ON DELETE RESTRICT`.

## M11.07 Threat-To-Test Matrix — 48 Threats / 16 Files

Each node ID below is exact. Prevention names the owning source boundary;
the assertion is deterministic and mandatory.

| ID | Exact future node ID | Prevention/source | Deterministic GREEN assertion |
|---|---|---|---|
| T-M11-001 | `tests/test_m11_baseline_and_migration.py::test_m10_baseline_and_protected_source_pins_are_exact` | Git/TT/freeze/CR pins | Exact SHAs/hashes and M10 `2735`, `8/8`, run evidence match. |
| T-M11-002 | `tests/test_m11_baseline_and_migration.py::test_m11_migration_is_single_linear_zero_table_child` | Alembic revision | One head/parent; no table/enum/sequence/trigger/function/view creation. |
| T-M11-003 | `tests/test_m11_baseline_and_migration.py::test_m11_upgrade_downgrade_walk_and_active_downgrade_fail_closed` | migration downgrade guard | Empty/populated walk succeeds; active row makes downgrade fail without rewrite. |
| T-M11-004 | `tests/test_m11_otp_purpose_crypto.py::test_otp_purpose_and_event_action_sets_are_exact` | OTP enums/models | Exactly LOGIN/REGISTRATION and one new invalidation action. |
| T-M11-005 | `tests/test_m11_otp_purpose_crypto.py::test_login_mac_golden_vector_is_unchanged` | OTP crypto | Pinned LOGIN MAC bytes remain identical. |
| T-M11-006 | `tests/test_m11_otp_purpose_crypto.py::test_registration_mac_is_domain_separated_and_uses_compare_digest` | OTP crypto | REGISTRATION vector passes; cross-purpose substitution fails via compare_digest. |
| T-M11-007 | `tests/test_m11_otp_purpose_crypto.py::test_otp_code_mac_and_keys_remain_redacted_from_all_sinks` | wrappers/static leakage | Raw code/MAC/key absent from repr/log/error/report/HTML/URL. |
| T-M11-008 | `tests/test_m11_registration_rate_policy.py::test_registration_settings_defaults_and_bounds_fail_closed` | Settings validation | Exact defaults/bounds; malformed operation config yields redacted unavailable. |
| T-M11-009 | `tests/test_m11_registration_rate_policy.py::test_registration_rate_uses_server_phone_user_and_trusted_ip_once` | rate policy/IP resolver | One allowed POST records three HMAC buckets once; accepts no client phone. |
| T-M11-010 | `tests/test_m11_registration_rate_policy.py::test_registration_scopes_and_cooldown_are_isolated_from_login` | rate/cooldown purpose | LOGIN counters/challenge remain unchanged at 59/60-second boundaries. |
| T-M11-011 | `tests/test_m11_dispatcher_registration.py::test_dispatcher_prepares_typed_registration_message_after_live_recheck` | dispatch service/message | Draft+same link prepares safe code/TTL text and purpose MAC only. |
| T-M11-012 | `tests/test_m11_dispatcher_registration.py::test_dispatcher_external_send_has_no_open_session_and_records_result_once` | dispatcher TX-D1/send/TX-D2 | Session closed during fake send; one result and one typed event. |
| T-M11-013 | `tests/test_m11_dispatcher_registration.py::test_dispatcher_failure_unknown_and_stopped_modes_preserve_existing_process` | dispatcher recovery/health | No resend/retry/new process; web/LOGIN health semantics stay exact. |
| T-M11-014 | `tests/test_m11_registration_readiness.py::test_each_missing_readiness_gate_is_safe_and_side_effect_free` | readiness service | Every absent/ineligible gate yields expected safe code and zero OTP/audit writes. |
| T-M11-015 | `tests/test_m11_registration_readiness.py::test_readiness_snapshot_contains_only_exact_redacted_evidence` | snapshot contract | Exact fields, positive revision, redacted repr; forbidden values absent. |
| T-M11-016 | `tests/test_m11_registration_readiness.py::test_current_acceptance_selects_earliest_accepted_at_then_id` | offer acceptance selector | Multiple accepted languages deterministically return earliest row. |
| T-M11-017 | `tests/test_m11_registration_issue_postgresql.py::test_registration_issue_atomically_creates_snapshot_dispatch_and_event` | issue service/repositories | One bound challenge, one dispatch, one ISSUED event or all zero on failure. |
| T-M11-018 | `tests/test_m11_registration_issue_postgresql.py::test_login_and_registration_coexist_without_cross_purpose_supersession` | purpose queries/partial uniques | One outstanding per purpose/browser/user and LOGIN row unchanged. |
| T-M11-019 | `tests/test_m11_registration_issue_postgresql.py::test_new_code_cooldown_and_supersession_are_registration_local` | new-code service | 59s zero write; 60s cancels/supersedes old REGISTRATION and creates one fresh. |
| T-M11-020 | `tests/test_m11_registration_verify_postgresql.py::test_verify_resolves_browser_registration_candidate_without_client_ids` | verify command/repository | Forged UUID/purpose fields cannot select or mutate another candidate. |
| T-M11-021 | `tests/test_m11_registration_verify_postgresql.py::test_snapshot_mismatch_invalidates_before_mac_without_attempt` | verify live recheck | INVALIDATED + one state-change event; attempts/customer/audit/session unchanged. |
| T-M11-022 | `tests/test_m11_registration_verify_postgresql.py::test_wrong_registration_otp_increments_and_fifth_attempt_burns` | candidate/MAC lifecycle | Attempts 1..5, VERIFY_FAILED each, one BURNED at cap, no activation. |
| T-M11-023 | `tests/test_m11_registration_verify_postgresql.py::test_correct_registration_otp_atomically_activates_and_rotates` | activation coordinator | Consumed/event/active/audit/new current session all commit together. |
| T-M11-024 | `tests/test_m11_offer_acceptance_races_postgresql.py::test_offer_switch_and_issue_serialize_on_current_version_set` | OfferVersion lock | Switch-first sees new current; issue-first stable snapshot; no old-current authority. |
| T-M11-025 | `tests/test_m11_offer_acceptance_races_postgresql.py::test_multiple_language_acceptance_race_keeps_deterministic_snapshot` | User/OfferVersion/Acceptance locks | Earliest valid evidence chosen; concurrent accept cannot yield mixed snapshot. |
| T-M11-026 | `tests/test_m11_identity_document_races_postgresql.py::test_identity_update_and_activation_serialize_on_customer_then_identity` | Customer/Identity locks | Update-first invalidates; activation-first makes later M10 update fail draft gate. |
| T-M11-027 | `tests/test_m11_identity_document_races_postgresql.py::test_document_supersede_object_state_and_activation_serialize` | Customer/Object/Document locks | New current or non-AVAILABLE invalidates; activation-first denies later mutation. |
| T-M11-028 | `tests/test_m11_activation_atomicity_postgresql.py::test_central_audit_failure_rolls_back_consume_activation_and_rotation` | outer transaction/audit | Original challenge/customer/session state remains exact. |
| T-M11-029 | `tests/test_m11_activation_atomicity_postgresql.py::test_otp_event_failure_rolls_back_activation_audit_and_rotation` | OTP journal/outer transaction | Zero partial writes and original session remains valid. |
| T-M11-030 | `tests/test_m11_activation_atomicity_postgresql.py::test_session_or_cookie_failure_rolls_back_and_releases_no_cookie` | session adapter/router commit ordering | No consume/active/audit/rotation commit and no Set-Cookie response. |
| T-M11-031 | `tests/test_m11_activation_atomicity_postgresql.py::test_rotation_is_current_only_and_preserves_shop_and_other_sessions` | AuthSession-last adapter | Old current revoked/new current created; shop copied; other sessions unchanged. |
| T-M11-032 | `tests/test_m11_activation_concurrency_postgresql.py::test_parallel_correct_verify_has_one_activation_winner` | CR locks/PostgreSQL barrier | One consume/audit/rotation; loser converges ALREADY_ACTIVE; no deadlock. |
| T-M11-033 | `tests/test_m11_activation_concurrency_postgresql.py::test_replay_and_already_active_are_zero_write_noop_success` | customer state/idempotency | Timestamps/counts/session set remain byte-for-byte unchanged. |
| T-M11-034 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_active_customer_ordinary_unlink_is_zero_write_denied` | Telegram service Customer guard | Exact stable error; link/tokens/events/challenges unchanged. |
| T-M11-035 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_active_customer_protected_relink_is_atomic_and_invalidates_both_purposes` | CR link-change path | New generation commits with invalidations; collision preserves old generation. |
| T-M11-036 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_activation_unlink_and_relink_barriers_never_leave_active_unlinked` | global order/barriers | Final state only draft-unlinked or active-linked; zero deadlock/timeouts. |
| T-M11-037 | `tests/test_m11_activation_web_postgresql.py::test_activation_routes_ignore_forged_authority_identifiers` | actor/router commands | Foreign IDs/purpose/session fields select nothing and alter no foreign row. |
| T-M11-038 | `tests/test_m11_activation_web_postgresql.py::test_activation_posts_require_session_bound_csrf_before_any_write` | auth deps/CSRF | Missing/forged CSRF returns CSRF_FAILED with zero rate/domain writes. |
| T-M11-039 | `tests/test_m11_activation_web_security.py::test_activation_templates_autoescape_and_csp_forbid_inline_code` | Jinja/CSP | Synthetic markup is escaped; no unsafe/inline script/style handler. |
| T-M11-040 | `tests/test_m11_activation_web_security.py::test_activation_prg_urls_flash_and_headers_are_no_store_and_secret_free` | router/security headers | Fixed PRG targets, no PII/OTP/IDs; every response no-store. |
| T-M11-041 | `tests/test_m11_activation_web_security.py::test_activation_copy_has_matching_uz_latn_and_ru_keys_with_fallback` | presentation catalog | Exact key parity and UZ fallback; messages remain safe. |
| T-M11-042 | `tests/test_m11_activation_web_security.py::test_activation_mobile_controls_and_otp_input_contract_are_exact` | template/CSS | 320–430px, labels/focus/44px and exact text OTP attributes. |
| T-M11-043 | `tests/test_m11_sensitive_data_leakage.py::test_registration_forbidden_values_never_reach_db_audit_log_error_html_url_or_repr` | models/redaction/static/runtime sweep | Every supplied sentinel absent outside approved typed DB columns. |
| T-M11-044 | `tests/test_m11_sensitive_data_leakage.py::test_web_never_calls_telegram_and_dispatcher_never_leaks_transport_values` | import/runtime boundaries | Zero web bot calls; fake-send logs/errors contain no sensitive sentinel. |
| T-M11-045 | `tests/test_m11_scope_containment.py::test_m11_adds_no_public_bootstrap_user_customer_lead_or_shop_customer` | route/model inventory | Forbidden symbols/routes/tables absent. |
| T-M11-046 | `tests/test_m11_scope_containment.py::test_m11_adds_no_table_dependency_worker_dispatcher_or_out_scope_capability` | metadata/lock/process inventory | Table/dependency/process sets equal approved delta only. |
| T-M11-047 | `tests/test_m11_scope_containment.py::test_m1_through_m10_targeted_contracts_remain_green` | inherited regression inventory | Exact inherited target list executes with zero non-pass outcomes. |
| T-M11-048 | `tests/test_m11_scope_containment.py::test_transaction_lock_and_test_static_guards_are_exact` | source/test AST guards | No borrowed commit/full rollback/close, inverse lock, sleep, SQLite/create_all/skip/xfail. |

File inventory (exactly 16):

```text
tests/test_m11_baseline_and_migration.py
tests/test_m11_otp_purpose_crypto.py
tests/test_m11_registration_rate_policy.py
tests/test_m11_dispatcher_registration.py
tests/test_m11_registration_readiness.py
tests/test_m11_registration_issue_postgresql.py
tests/test_m11_registration_verify_postgresql.py
tests/test_m11_offer_acceptance_races_postgresql.py
tests/test_m11_identity_document_races_postgresql.py
tests/test_m11_activation_atomicity_postgresql.py
tests/test_m11_activation_concurrency_postgresql.py
tests/test_m11_active_telegram_invariant_postgresql.py
tests/test_m11_activation_web_postgresql.py
tests/test_m11_activation_web_security.py
tests/test_m11_sensitive_data_leakage.py
tests/test_m11_scope_containment.py
```

## Implementation Stop Conditions

Stop on protected-source drift, unexplained dirty state, a required semantic
change, non-executable CR order, need for a new dependency/table/process/
infrastructure component, OUT-scope requirement, production secret/PII,
destructive recovery, unavailable mandatory external evidence, or a non-GREEN
exact gate. A repository observation never grants authority to bypass a stop.
