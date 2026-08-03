# Nasiya M11 Repository Map

Status: authoritative M11 repository placement map, CR-M11-03 recovery-amended.
Baseline: `17ebbe166d63a32e3b7eaa3eb3838f578d9b7780`.
Corrections: `CR-M11-01 — FINAL APPROVED` and
`CR-M11-02 — FINAL APPROVED` jointly control lock acquisition and verified
Telegram-link authority. `CR-M11-03 — FINAL APPROVED` corrects checkpoint
truth and authorizes the bounded owner/closed-pre-phase R09 completion.

This map records source authority, current symbols, bounded future symbols,
exact schema names and the M11.07 threat matrix. It implements no product code.

## Source Classification

| Source | Class | Use |
|---|---|---|
| `docs/tt_nasiya_web_v1.md` | AUTHORITATIVE | Product registration, Telegram, OTP, activation, session, audit, privacy and web requirements. |
| External M11 Final Scope Freeze | AUTHORITATIVE | One-capability narrowing and PO-M11-01..25. |
| `CR-M11-01 — FINAL APPROVED` | AUTHORITATIVE CORRECTION | Replaces only the executable global lock order and bounded refactor assumptions. |
| `CR-M11-02 — FINAL APPROVED` | AUTHORITATIVE SECURITY CORRECTION | Requires Telegram self-phone verification, token-first shared locks, one recovery migration and amended evidence. |
| `CR-M11-03 — FINAL APPROVED` | AUTHORITATIVE RECOVERY CORRECTION | Pins seven original checkpoints plus one bounded recovery commit and requires owner-aware OTP policy plus closed auth/rate pre-phases. |
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
| OTP repository locks | `app/otp/repository.py:load_outstanding_challenge_by_user_for_update`, `load_outstanding_challenge_by_browser_for_update`, `load_outstanding_challenges_by_user_for_update`, `load_dispatch_by_challenge_for_update`, `claim_next_pending_dispatch_for_update` |
| OTP issue/new-code | `app/otp/issuance.py:request_login_otp`, `request_new_login_code`, `_terminalize_existing_outstanding`, `invalidate_login_otp_challenges_for_link_change` |
| OTP verification | `app/otp/verification.py:verify_login_otp`, `check_login_otp_candidate` |
| Dispatcher | `app/otp/dispatch_service.py:prepare_next_otp_dispatch`, `record_otp_delivery_result`; `app/otp/dispatcher.py:run_dispatcher`, `run_dispatch_loop`, `_prepare_next_item`, `_send_prepared_otp`, `_record_delivery_result` |
| Message/provider | `app/otp/message.py:format_login_otp_message`; `app/otp/provider.py:TelegramOtpProvider` |
| Settings | `app/settings.py:Settings`, `OtpHmacKeySettingsError`; existing `otp_login_*` and rate-limit settings |
| Rate limiter/IP | `app/auth/rate_limit.py:AuthRateLimiter`; `app/request_client_ip.py:resolve_client_ip`; `app/telegram/client_ip.py:ResolvedClientIp` |
| Telegram link locks | `app/telegram/repository.py:get_telegram_link_by_user_for_update`, `get_valid_telegram_link_token_for_consume_by_hash_for_update`, `link_unverified_private_chat`, `relink_unverified_private_chat`, retained `unlink_verified_private_chat`, and phone-verified prelocked lifecycle helpers |
| Link services | `app/telegram/service.py:consume_start_token`, `unlink`; challenge invalidation in `app/otp/issuance.py` |
| Link/token persistence | `app/telegram/models.py:TelegramLink`, `TelegramLinkToken`, `TelegramLinkEvent`; token status derives from expiry/consumed/invalidated timestamps |
| Inbound chat boundary | `app/telegram/inbound.py:VerifiedPrivateTelegramChatIdentity`; `app/telegram/bot_api.py:TelegramMessageEnvelope`, `_parse_message_envelope` |
| Update parsing/transaction | `app/telegram/update_parser.py:TelegramUpdateParseCode`, `ParsedTelegramUpdate`, `parse_telegram_update`; `app/telegram/update_processing.py:process_telegram_update_tx_a`, `_apply_terminal_update`, `BotReplyIntent` |
| Bot reply boundary | `app/telegram/bot_api.py:TelegramBotApiClient.send_message`; `app/telegram/bot_reply.py:render_bot_reply`, `deliver_bot_reply_best_effort` |
| Phone canonicalization | `app/auth/phone.py:normalize_uzbekistan_phone`; `app/auth/models.py:User.phone`; `app/auth/service.py:create_user` |
| Shared owner-aware link gate | `app/telegram/repository.py:is_otp_eligible_telegram_link`, mandatory server-derived `expected_user_id`, exact active verified generation |
| LOGIN verified-link gates | `app/otp/issuance.py:lookup_login_otp_eligibility`, `_target_from_challenge_snapshot`, `_lock_and_revalidate_target`; `app/otp/dispatch_service.py:_validate_challenge_target`; `app/otp/verification.py:_revalidate_current_login_target` |
| REGISTRATION verified-link gates | `app/customer_activation/service.py:_issue_registration_otp`, `get_registration_readiness`, `recheck_registration_activation_snapshot` |
| Stable error/presentation | `app/auth/error_codes.py:ErrorCode`; `app/customer_activation/presentation.py`; `app/customer_activation/router.py` |
| Offer current locks | `app/offers/repository.py:SqlAlchemyOfferVersionRepository.lock_versions_for_purpose`, `_lock_version_rows_for_purpose` |
| Offer acceptance | `app/offers/repository.py:SqlAlchemyOfferAcceptanceRepository`, `SqlAlchemyHasAcceptedCurrentRegistrationOffer`; `app/offers/models.py:OfferAcceptance` |
| Identity lock/auth | `app/customer_identity/repository.py:SqlAlchemyCustomerIdentityRepository.lock_identity`; `app/customer_identity/service.py:CustomerIdentityCompletenessService`, `_decrypt_verified_summary` |
| Document/object | `app/customer_document/repository.py:SqlAlchemyCustomerDocumentRepository.lock_current_documents`; `app/customer_document/service.py:has_current_customer_identity_document`; `app/storage/repository.py:load_object_file_for_update` |
| Central audit | `app/audit/contracts.py:AuditEventType`, `AuditObjectType`, `AuditEvent`; `app/audit/redaction.py`; `app/audit/repository.py:append_audit_event` |
| Session/CSRF/cookie | `app/auth/sessions.py:rotate_session`, `_create_session`; `app/otp/session_login.py:rotate_session_after_otp_consume`; `app/auth/csrf.py`; `app/auth/cookies.py:set_session_cookie` |
| Transaction owner | `app/db.py:create_database_session_dependency`; bounded route/coordinator use of `request.app.state.database_session_factory` for closed auth/rate/domain phases |
| Web composition | `app/main.py:create_app`; `app/customer/router.py`; `app/auth/router.py`; Jinja templates under `app/templates/` |
| Alembic | current `alembic/versions/d2e3f4a5b6c7_add_telegram_self_phone_verification.py`; parent `c1d2e3f4a5b6`; original M11 parent `b0c1d2e3f4a5`; `alembic/env.py` |
| PostgreSQL tests | `tests/postgresql.py:postgresql_engine`, `M2_CLEANUP_TABLE_NAMES`; function-scoped cleanup fixtures |
| CI | `.github/workflows/ci.yml`: frozen sync, Ruff, format, Alembic, PostgreSQL full pytest and zero-skip guard |

### M11.R01 current gaps

- `TelegramMessageEnvelope` retains chat/text/language only; sender ID and
  contact fields are absent, and `parse_telegram_update` accepts only private
  `/start <token>`.
- `TelegramBotApiClient.send_message` accepts text only; no fixed typed contact
  request or keyboard removal exists.
- `consume_start_token` currently consumes and creates/relinks immediately.
- `TelegramLink` has no phone-verification evidence and `TelegramLinkToken`
  has no pending-contact state.
- `normalize_uzbekistan_phone` uses `isdecimal()` and therefore does not reject
  every non-ASCII decimal digit; the database has no canonical phone check.
- LOGIN and REGISTRATION issue/dispatch/verify/readiness predicates currently
  treat any active link generation as sufficient.
- No current product route mutates `User.phone`; user creation is the sole
  application writer. A future mutation path remains OUT and must atomically
  invalidate Telegram verification plus outstanding LOGIN/REGISTRATION OTP
  under the FINAL order.

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
| `app/telegram/inbound.py`, `bot_api.py`, `update_parser.py` | redacted sender/self-contact fields and fixed parser outcomes only |
| `app/telegram/bot_api.py`, `bot_reply.py`, `update_processing.py` | fixed request-contact and keyboard-removal types; no arbitrary reply-markup input |
| `app/telegram/models.py`, `repository.py`, `service.py`, `token.py` | pending binding MAC, token-first locks, self-contact verification and verified/reverified lifecycle |
| `app/auth/phone.py`, `models.py` | ASCII-only canonicalization and named canonical phone check |
| `app/audit/contracts.py`, `models.py`, `redaction.py` | exact activation event/object/payload extension |
| `app/auth/error_codes.py` | retain four original M11 codes; add three exact CR-M11-02 codes and UZ-Latn/RU safe definitions |
| `alembic/versions/c1d2e3f4a5b6_extend_customer_activation_foundation.py` | one zero-new-table M11 revision |
| `alembic/versions/d2e3f4a5b6c7_add_telegram_self_phone_verification.py` | sole zero-table CR-M11-02 recovery child |

No other package, table, dependency, worker, dispatcher, broker or route family
is planned.

## Corrected Acquisition Map

M11.R01 found the exact current inversion that CR-M11-02 corrects:

```text
app/telegram/service.py:consume_start_token
  TelegramLinkToken -> OtpDispatch -> OtpChallenge -> User -> TelegramLink

app/telegram/service.py:unlink
  OtpDispatch -> OtpChallenge -> User -> TelegramLink -> Customer
  -> app/telegram/repository.py:invalidate_outstanding_telegram_link_tokens
  -> TelegramLinkToken                         # forbidden inverse
```

The single recovery hierarchy is:

```text
TelegramLinkToken rows UUID ascending, whenever touched
-> OtpDispatch rows UUID ascending
-> OtpChallenge rows UUID ascending
-> User
-> TelegramLink rows UUID ascending
-> Customer
-> OfferVersion
-> OfferAcceptance
-> CustomerIdentity
-> ObjectFile
-> CustomerDocument
-> AuthSession
```

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
| `/start` binding | non-locking token candidate | all affected TelegramLinkToken rows UUID asc; bind MAC/timestamp and stop; commit/close before contact-request send |
| Contact malformed/mismatch | parse and optional non-locking candidate | zero domain mutation; generic result; pending token and prior verified link remain unchanged |
| Contact success/protected reverify | non-locking MAC candidate | affected TelegramLinkToken rows UUID asc -> OtpDispatch UUID asc -> OtpChallenge UUID asc -> User -> TelegramLink rows UUID asc -> Customer; consume/clear, verified generation, both-purpose invalidation, existing event; commit before reply |
| Ordinary unlink | trusted user discovery | outstanding/pending TelegramLinkToken rows UUID asc -> OtpDispatch UUID asc -> OtpChallenge UUID asc -> User -> TelegramLink rows UUID asc -> Customer; active guard, mutate only prelocked token set, clear verification when permitted |
| Token issue/replacement/purge | trusted token candidates | TelegramLinkToken rows UUID asc only; no later earlier-class acquisition |
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
- LOGIN/REGISTRATION issue, dispatcher, readiness and verify all recheck
  `unlinked_at IS NULL`, non-null `phone_verified_at`, and
  `phone_verified_at = linked_at`. The unchanged challenge
  `telegram_linked_at` snapshot therefore becomes stale on re-verification.

## Exact Schema Appendix

Original migration revision `c1d2e3f4a5b6`, down revision `b0c1d2e3f4a5`.
The original migration alters only `customers`, `otp_challenges`,
`otp_challenge_events`, and `audit_log`; it creates no table, enum, sequence,
index, trigger, function, or view.

### Customer lifecycle

```text
customers.activated_at TIMESTAMPTZ NULL
ck_customers_onboarding_status_allowed
ck_customers_activation_state_consistent
ck_customers_timestamp_order
```

`ck_customers_onboarding_status_draft_only` is replaced. The three exact
predicates are:

```text
onboarding_status IN ('draft', 'active')
(onboarding_status = 'draft' AND activated_at IS NULL)
OR (onboarding_status = 'active' AND activated_at IS NOT NULL)
updated_at >= created_at
AND (activated_at IS NULL OR activated_at >= created_at)
AND (activated_at IS NULL OR updated_at >= activated_at)
```

No default or index is added for `activated_at`. Adding the nullable column
leaves every M10 row exactly `draft / NULL`; no customer data rewrite occurs.

### REGISTRATION challenge context

```text
otp_challenges.customer_id UUID NULL
otp_challenges.registration_offer_acceptance_id UUID NULL
otp_challenges.customer_identity_revision INTEGER NULL
otp_challenges.customer_document_id UUID NULL
ck_otp_challenges_purpose_allowed
ck_otp_challenges_registration_context_matches_purpose
fk_otp_challenges_customer_id_customers_id
fk_otp_challenges_registration_acceptance_offer_acceptances
fk_otp_challenges_customer_document_id_customer_documents
```

The purpose checks replace `ck_otp_challenges_purpose_login` with these exact
predicates:

```text
purpose IN ('LOGIN', 'REGISTRATION')
(purpose = 'LOGIN'
 AND customer_id IS NULL
 AND registration_offer_acceptance_id IS NULL
 AND customer_identity_revision IS NULL
 AND customer_document_id IS NULL)
OR
(purpose = 'REGISTRATION'
 AND user_id IS NOT NULL
 AND telegram_link_id IS NOT NULL
 AND telegram_linked_at IS NOT NULL
 AND customer_id IS NOT NULL
 AND registration_offer_acceptance_id IS NOT NULL
 AND customer_identity_revision > 0
 AND customer_document_id IS NOT NULL)
```

The three named FKs target `customers.id`, `offer_acceptances.id`, and
`customer_documents.id`, respectively, and all use `ON DELETE RESTRICT`.
Identity revision is a positive snapshot scalar, not a new identity FK.
Existing LOGIN rows receive four NULLs and remain valid. Existing purpose-
bearing partial unique indexes and `ix_otp_challenges_terminal_at` are retained
byte-for-byte; no context index is added.

### Event and central-audit checks

```text
ck_otp_challenge_events_action_allowed
ck_audit_log_event_type_allowed
ck_audit_log_object_type_allowed
ck_audit_log_object_matches_event
ck_audit_log_payload_exact_shape
```

The OTP action set gains only
`INVALIDATED_BY_REGISTRATION_STATE_CHANGE`. The audit registry gains only
`customer.activated`, object type `customer`, and this exact object mapping and
payload clause:

```text
customer.activated -> customer
payload keys exactly: from_status, to_status, activation_method
from_status = 'draft'
to_status = 'active'
activation_method = 'TELEGRAM_REGISTRATION_OTP'
```

All M10 audit event/object/payload clauses remain exact. The replacement
payload check rejects extra JSON keys for `customer.activated`.

### Executable alter and lock order

One transactional upgrade performs these bounded operations in order:

1. add the five nullable columns without defaults, so PostgreSQL performs no
   table rewrite;
2. add the three RESTRICT FKs as `NOT VALID` and add new customer/purpose
   checks as `NOT VALID`;
3. validate those new constraints, then drop the superseded customer
   draft-only and OTP LOGIN-only checks;
4. transactionally replace the OTP-event check;
5. transactionally replace the four audit registry/object/payload checks in
   their existing names.

`ADD/DROP COLUMN` and `ADD/DROP CONSTRAINT` take brief `ACCESS EXCLUSIVE`
table locks. `VALIDATE CONSTRAINT` uses the weaker PostgreSQL validation lock
and scans existing rows without a rewrite. The migration runs in a maintenance
window; it contains no retry, lock timeout, sleep, concurrent-index workaround,
or advisory lock. New constraints protect new writes even while `NOT VALID`.

### Fail-closed downgrade and walk

Before changing any schema, downgrade executes one no-identifier precondition:

```text
fail if EXISTS customers WHERE onboarding_status = 'active'
                           OR activated_at IS NOT NULL
```

On failure it leaves the complete M11 schema and data untouched. If the guard
passes, downgrade restores the exact M10 audit and OTP-event checks, restores
`ck_otp_challenges_purpose_login`, removes only the three M11 FKs and four
context columns, restores `ck_customers_onboarding_status_draft_only`, and
drops `customers.activated_at`. It never rewrites active to draft.

Real-PostgreSQL migration tests use Alembic, never `create_all` or manual DDL,
for both an empty base-to-head walk and a populated
`b0c1d2e3f4a5 -> c1d2e3f4a5b6 -> b0c1d2e3f4a5 -> c1d2e3f4a5b6` walk. The
populated walk pins existing customer `draft / NULL`, LOGIN context NULLs,
M10 audit acceptance, the single head/parent, zero new tables/indexes, and the
active-row downgrade refusal.

### CR-M11-02 recovery schema and walk

Revision `d2e3f4a5b6c7`, down revision `c1d2e3f4a5b6`, is the sole recovery
child and adds exactly:

```text
telegram_link_tokens.pending_contact_binding_mac VARCHAR(64) NULL
telegram_link_tokens.contact_requested_at        TIMESTAMPTZ NULL
telegram_links.phone_verified_at                  TIMESTAMPTZ NULL
```

Exact schema names and predicates:

```text
ck_telegram_link_tokens_pending_contact_binding_mac_format
  pending_contact_binding_mac IS NULL
  OR pending_contact_binding_mac ~ '^[0-9a-f]{64}$'

ck_telegram_link_tokens_pending_contact_state_consistent
  (pending_contact_binding_mac IS NULL) = (contact_requested_at IS NULL)
  AND (consumed_at IS NULL AND invalidated_at IS NULL
       OR pending_contact_binding_mac IS NULL)

ck_telegram_link_tokens_pending_contact_timestamp_order
  contact_requested_at IS NULL OR contact_requested_at >= created_at

uq_telegram_link_tokens_pending_contact_binding_mac_outstanding
  UNIQUE (pending_contact_binding_mac)
  WHERE pending_contact_binding_mac IS NOT NULL
    AND consumed_at IS NULL AND invalidated_at IS NULL

ck_telegram_links_phone_verification_consistent
  phone_verified_at IS NULL
  OR (unlinked_at IS NULL AND phone_verified_at = linked_at)

ck_users_phone_canonical_uz_e164
  phone ~ '^\+998[0-9]{9}$'
```

Upgrade first performs a read-only user-phone precondition and stops on any
noncanonical value; it rewrites no phone and leaves every existing link with
`phone_verified_at = NULL`. No raw phone/chat/user identity is stored in new
columns. The migration creates no table, enum, sequence, trigger, function or
view and adds no runtime dependency.

Recovery downgrade first fails, before DDL, if any pending binding or verified
link exists. After explicit cleanup it drops only the CR-M11-02 index, checks
and three columns and restores head `c1d2e3f4a5b6`; the original active-
customer downgrade guard remains independent. Real-PostgreSQL walks cover
`b0c1d2e3f4a5 -> c1d2e3f4a5b6 -> d2e3f4a5b6c7`, recovery
`d2e3f4a5b6c7 -> c1d2e3f4a5b6 -> d2e3f4a5b6c7`, and the original M10/M11
walk without manual DDL or `create_all`.

## M11 Threat-To-Test Matrix — 79 Threats / 31 Files

Each node ID below is exact. Prevention names the owning source boundary;
the assertion is deterministic and mandatory.

| ID | Exact future node ID | Prevention/source | Deterministic GREEN assertion |
|---|---|---|---|
| T-M11-001 | `tests/test_m11_baseline_and_migration.py::test_m10_baseline_and_protected_source_pins_are_exact` | Git/TT/freeze/CR pins | Exact SHAs/hashes and M10 `2735`, `8/8`, run evidence match. |
| T-M11-002 | `tests/test_m11_baseline_and_migration.py::test_m11_migrations_are_single_linear_zero_table_chain` | Alembic revision | Exact linear `b0 -> c1 -> d2`; both M11 revisions create zero tables/enums/sequences/triggers/functions/views. |
| T-M11-003 | `tests/test_m11_baseline_and_migration.py::test_original_m11_and_recovery_migration_walk_is_deterministic` | migration downgrade guards | Empty/populated walks succeed; original active-customer and recovery pending/verified guards each fail before rewrite/DDL. |
| T-M11-004 | `tests/test_m11_otp_purpose_crypto.py::test_otp_purpose_and_event_action_sets_are_exact` | OTP enums/models | Exactly LOGIN/REGISTRATION and one new invalidation action. |
| T-M11-005 | `tests/test_m11_otp_purpose_crypto.py::test_login_mac_golden_vector_is_unchanged` | OTP crypto | Pinned LOGIN MAC bytes remain identical. |
| T-M11-006 | `tests/test_m11_otp_purpose_crypto.py::test_registration_mac_is_domain_separated_and_uses_compare_digest` | OTP crypto | REGISTRATION vector passes; cross-purpose substitution fails via compare_digest. |
| T-M11-007 | `tests/test_m11_otp_purpose_crypto.py::test_otp_code_mac_and_keys_remain_redacted_from_all_sinks` | wrappers/static leakage | Raw code/MAC/key absent from repr/log/error/report/HTML/URL. |
| T-M11-008 | `tests/test_m11_registration_rate_policy.py::test_registration_settings_defaults_and_bounds_fail_closed` | Settings validation | Exact defaults/bounds; malformed operation config yields redacted unavailable. |
| T-M11-009 | `tests/test_m11_registration_rate_policy.py::test_registration_rate_uses_server_phone_user_and_trusted_ip_once` | rate policy/IP resolver | One allowed POST records three HMAC buckets once; accepts no client phone. |
| T-M11-010 | `tests/test_m11_registration_rate_policy.py::test_registration_scopes_and_cooldown_are_isolated_from_login` | rate/cooldown purpose | LOGIN counters/challenge remain unchanged at 59/60-second boundaries. |
| T-M11-011 | `tests/test_m11_dispatcher_registration.py::test_dispatcher_prepares_typed_registration_message_after_live_recheck` | dispatch service/message | Draft+same phone-verified link generation prepares safe code/TTL text and purpose MAC only. |
| T-M11-012 | `tests/test_m11_dispatcher_registration.py::test_dispatcher_external_send_has_no_open_session_and_records_result_once` | dispatcher TX-D1/send/TX-D2 | Session closed during fake send; one result and one typed event. |
| T-M11-013 | `tests/test_m11_dispatcher_registration.py::test_dispatcher_failure_unknown_and_stopped_modes_preserve_existing_process` | dispatcher recovery/health | No resend/retry/new process; web/LOGIN health semantics stay exact. |
| T-M11-014 | `tests/test_m11_registration_readiness.py::test_each_missing_readiness_gate_is_safe_and_side_effect_free` | readiness service | Every absent/ineligible gate, including legacy/unverified link, yields safe code and zero OTP/audit writes. |
| T-M11-015 | `tests/test_m11_registration_readiness.py::test_readiness_snapshot_contains_only_exact_redacted_evidence` | snapshot contract | Exact fields, positive revision, redacted repr; forbidden values absent. |
| T-M11-016 | `tests/test_m11_registration_readiness.py::test_current_acceptance_selects_earliest_accepted_at_then_id` | offer acceptance selector | Multiple accepted languages deterministically return earliest row. |
| T-M11-017 | `tests/test_m11_registration_issue_postgresql.py::test_registration_issue_atomically_creates_snapshot_dispatch_and_event` | issue service/repositories | One verified-generation-bound challenge, dispatch and ISSUED event, or all zero on failure. |
| T-M11-018 | `tests/test_m11_registration_issue_postgresql.py::test_login_and_registration_coexist_without_cross_purpose_supersession` | purpose queries/partial uniques | One outstanding per purpose/browser/user and LOGIN row unchanged. |
| T-M11-019 | `tests/test_m11_registration_issue_postgresql.py::test_new_code_cooldown_and_supersession_are_registration_local` | new-code service | 59s zero write; 60s cancels/supersedes old REGISTRATION and creates one fresh. |
| T-M11-020 | `tests/test_m11_registration_verify_postgresql.py::test_verify_resolves_browser_registration_candidate_without_client_ids` | verify command/repository | Forged UUID/purpose fields cannot select or mutate another candidate. |
| T-M11-021 | `tests/test_m11_registration_verify_postgresql.py::test_snapshot_mismatch_invalidates_before_mac_without_attempt` | verify live recheck | Verification/generation mismatch invalidates with one state event; attempts/customer/audit/session unchanged. |
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
| T-M11-034 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_active_customer_ordinary_unlink_is_zero_write_denied` | token-first Telegram/Customer guard | Exact stable error; verified link/tokens/events/challenges unchanged. |
| T-M11-035 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_active_customer_protected_relink_is_atomic_and_invalidates_both_purposes` | token-first verified link change | Same-phone verified generation commits with invalidations; mismatch/collision preserves old verified generation. |
| T-M11-036 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_activation_unlink_and_relink_barriers_never_leave_active_unlinked` | global order/barriers | Final state only draft-unlinked or active-phone-verified; no inverse/deadlock/timeout. |
| T-M11-037 | `tests/test_m11_activation_web_postgresql.py::test_activation_routes_ignore_forged_authority_identifiers` | actor/router commands | Foreign IDs/purpose/session fields select nothing and alter no foreign row. |
| T-M11-038 | `tests/test_m11_activation_web_postgresql.py::test_activation_posts_require_session_bound_csrf_before_any_write` | auth deps/CSRF | Missing/forged CSRF returns CSRF_FAILED with zero rate/domain writes. |
| T-M11-039 | `tests/test_m11_activation_web_security.py::test_activation_templates_autoescape_and_csp_forbid_inline_code` | Jinja/CSP | Synthetic markup is escaped; no unsafe/inline script/style handler. |
| T-M11-040 | `tests/test_m11_activation_web_security.py::test_activation_prg_urls_flash_and_headers_are_no_store_and_secret_free` | router/security headers | Fixed PRG targets, no PII/OTP/IDs; every response no-store. |
| T-M11-041 | `tests/test_m11_activation_web_security.py::test_activation_copy_has_matching_uz_latn_and_ru_keys_with_fallback` | presentation catalog | Exact key parity and UZ fallback; messages remain safe. |
| T-M11-042 | `tests/test_m11_activation_web_security.py::test_activation_mobile_controls_and_otp_input_contract_are_exact` | template/CSS | 320–430px, labels/focus/44px and exact text OTP attributes. |
| T-M11-043 | `tests/test_m11_sensitive_data_leakage.py::test_registration_forbidden_values_never_reach_db_audit_log_error_html_url_or_repr` | models/redaction/static/runtime sweep | Every supplied sentinel, including contact/binding values, is absent outside approved typed DB columns. |
| T-M11-044 | `tests/test_m11_sensitive_data_leakage.py::test_web_never_calls_telegram_and_dispatcher_never_leaks_transport_values` | import/runtime boundaries | Zero web bot calls; fake contact/reply and dispatcher logs/errors contain no sensitive sentinel. |
| T-M11-045 | `tests/test_m11_scope_containment.py::test_m11_adds_no_public_bootstrap_user_customer_lead_or_shop_customer` | route/model inventory | Forbidden symbols/routes/tables absent. |
| T-M11-046 | `tests/test_m11_scope_containment.py::test_m11_adds_no_table_dependency_worker_dispatcher_or_out_scope_capability` | metadata/lock/process inventory | Table/dependency/process sets equal the original plus exact three-column recovery delta only. |
| T-M11-047 | `tests/test_m11_scope_containment.py::test_m1_through_m10_targeted_contracts_remain_green` | inherited regression inventory | Exact inherited target list executes with zero non-pass outcomes. |
| T-M11-048 | `tests/test_m11_scope_containment.py::test_transaction_lock_and_test_static_guards_are_exact` | source/test AST guards | No borrowed commit/full rollback/close, post-Dispatch token lock, sleep/retry correctness, SQLite/create_all/skip/xfail. |
| T-M11-049 | `tests/test_telegram_consume_start_token_service.py::test_matching_self_contact_consumes_bound_token_and_creates_verified_link` | contact coordinator | Matching private self-contact yields one consumed token and one verified generation. |
| T-M11-050 | `tests/test_telegram_consume_start_token_service.py::test_contact_phone_mismatch_is_generic_and_zero_write` | contact equality boundary | Mismatch returns a generic result; token/link/OTP/customer/session/audit/event/attempt state is unchanged. |
| T-M11-051 | `tests/test_telegram_update_parser.py::test_contact_requires_present_contact_user_id` | typed inbound parser | Missing `contact.user_id` is rejected without exposing contact data. |
| T-M11-052 | `tests/test_telegram_update_parser.py::test_contact_user_id_must_equal_sender_user_id` | self-contact parser | Contact user unequal to sender is rejected before persistence. |
| T-M11-053 | `tests/test_telegram_update_parser.py::test_contact_requires_private_chat` | private-chat parser | Group/channel contact is rejected with a fixed safe outcome. |
| T-M11-054 | `tests/test_telegram_update_parser.py::test_forwarded_or_other_person_contact_is_rejected` | self-contact parser | Forwarded/other-person contact cannot become link authority. |
| T-M11-055 | `tests/test_telegram_consume_start_token_service.py::test_expired_consumed_or_invalidated_bound_token_is_rejected` | token lifecycle recheck | Every non-live pending token fails closed and creates no link. |
| T-M11-056 | `tests/test_telegram_consume_start_token_service.py::test_verified_contact_token_replay_is_rejected` | token one-time state | Successful contact replay creates no second link/event/invalidation. |
| T-M11-057 | `tests/test_telegram_consume_start_token_service.py::test_bound_token_rejects_other_chat_or_sender` | binding MAC | A different chat/sender cannot select or consume the pending token. |
| T-M11-058 | `tests/test_telegram_consume_concurrency.py::test_same_pending_binding_mac_has_exactly_one_winner` | token unique/index locks | Concurrent same-binding attempts deterministically produce one pending winner. |
| T-M11-059 | `tests/test_telegram_consume_start_token_service.py::test_contact_chat_collision_preserves_token_and_existing_link` | link collision/savepoint | Chat already actively linked elsewhere is rejected without partial mutation. |
| T-M11-060 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_legacy_unverified_link_cannot_issue_login_otp` | LOGIN verified-link gate | Legacy null verification creates no LOGIN challenge/dispatch/event. |
| T-M11-061 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_legacy_unverified_link_cannot_issue_registration_otp_or_activate` | REGISTRATION verified-link gate | Legacy null verification creates no registration OTP or activation effects. |
| T-M11-062 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_phone_verified_link_permits_login_and_registration_otp` | shared eligibility predicate | One current verified generation permits both approved OTP purposes. |
| T-M11-063 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_same_phone_reverify_rotates_generation_and_stales_both_purposes` | verified generation rotation | Re-verification rotates equal timestamps and stales both older purpose challenges. |
| T-M11-064 | `tests/test_telegram_consume_start_token_service.py::test_mismatching_relink_preserves_old_verified_generation` | protected relink equality | Mismatching contact preserves old verified link/customer and all other state. |
| T-M11-065 | `tests/test_telegram_consume_concurrency.py::test_token_first_contact_unlink_relink_and_dispatcher_barriers_never_deadlock` | CR-M11-02 hierarchy | Deterministic real-PostgreSQL barriers prove token-first order and convergent outcomes without sleeps/retries. |
| T-M11-066 | `tests/test_telegram_post_commit_reply.py::test_contact_request_and_success_replies_send_without_open_session` | worker TX/reply seam | Fixed request/removal replies execute only after Session/transaction closure. |
| T-M11-067 | `tests/test_m11_sensitive_data_leakage.py::test_contact_phone_chat_user_token_binding_mac_and_secret_never_reach_forbidden_sinks` | sensitive wrappers/static/runtime sweep | Raw contact/Telegram/token/MAC/key sentinels are absent from every forbidden sink. |
| T-M11-068 | `tests/test_m11_baseline_and_migration.py::test_original_m11_and_recovery_migration_walk_is_deterministic` | two-revision Alembic walk | Empty/populated `b0 -> c1 -> d2` and `d2 -> c1 -> d2` walks preserve approved data/schema. |
| T-M11-069 | `tests/test_m11_baseline_and_migration.py::test_recovery_downgrade_fails_closed_with_pending_or_verified_state` | recovery downgrade guard | Pending binding or verified link blocks downgrade before any schema mutation. |
| T-M11-070 | `tests/test_m11_scope_containment.py::test_cr_m11_02_adds_only_three_columns_and_no_table_dependency_or_process` | recovery containment | Exact three columns/checks/index only; no table/dependency/process/OUT capability. |
| T-M11-071 | `tests/test_m11_scope_containment.py::test_otp_sensitive_link_policy_calls_are_explicitly_owner_aware` | ownerless policy-call guard | Every runtime eligibility call supplies a non-null server-derived `expected_user_id`. |
| T-M11-072 | `tests/test_otp_issuance_postgresql.py::test_login_issue_and_new_code_reject_cross_owner_verified_link`; `tests/test_otp_dispatch_service_postgresql.py::test_tx_d1_cross_owner_verified_link_cancels_before_code_generation`; `tests/test_otp_verification_postgresql.py::test_verify_rejects_cross_owner_verified_link_before_mac_or_attempt`; `tests/test_otp_session_login_postgresql.py::test_cross_owner_verified_link_never_rotates_login_session` | shared owner-aware gate | A verified link owned by another user cannot issue/refresh/send/consume, increment attempts, rotate a session, or create central audit evidence; required fail-closed lifecycle is exact. |
| T-M11-073 | `tests/test_otp_issuance_postgresql.py::test_successful_initial_coordinator_uses_closed_distinct_phases_and_scalar_receipt`; `tests/test_otp_request_post.py::test_post_otp_request_http_phases_close_before_domain_lock`; `tests/test_otp_new_code_post.py::test_post_new_code_http_cooldown_and_closed_rate_to_domain_phases` | LOGIN closed pre-phases | Real HTTP/coordinator evidence forces due auth touch and existing rate rows, transfers scalars only, and proves every auth/rate/discovery Session closed before the domain phase. |
| T-M11-074 | `tests/test_auth_telegram_routes.py::test_telegram_mutation_routes_close_auth_and_rate_prephases_before_domain`; `tests/test_auth_telegram_routes.py::test_link_issue_rate_row_barrier_stops_domain_after_closed_auth_prephase`; `tests/test_auth_telegram_routes.py::test_reauth_rate_row_barrier_stops_domain_after_closed_password_phase` | Telegram route phase order | Auth/CSRF, password reauth and rate-only transactions commit and close in order; rate denial never enters token/link domain work. |
| T-M11-075 | `tests/test_m11_scope_containment.py::test_auth_rate_limit_and_otp_telegram_domain_sessions_never_mix`; `tests/test_m11_scope_containment.py::test_misleading_verified_private_chat_runtime_names_are_absent` | phase/legacy static containment | Runtime code cannot mix one borrowed Session across rate and OTP/Telegram domain work or retain misleading verified-name aliases for unverified helpers. |
| T-M11-076 | `tests/test_m11_registration_issue_postgresql.py::test_new_registration_code_cross_owner_preserves_existing_capability`; `tests/test_m11_dispatcher_registration.py::test_registration_dispatcher_cross_owner_cancels_before_code_or_provider`; `tests/test_m11_registration_verify_postgresql.py::test_full_registration_verify_cross_owner_has_exact_zero_activation_lifecycle`; `tests/test_m11_activation_web_postgresql.py::test_wrong_owner_web_new_code_and_verify_are_safe_and_contained` | REGISTRATION owner-aware matrix | Cross-owner verified links cannot refresh, dispatch, activate, increment attempts, call the provider, rotate a session, or write central audit; exact invalidation/cancellation remains safe. |
| T-M11-077 | `tests/test_m11_active_telegram_invariant_postgresql.py::test_activation_verify_and_link_token_issue_barrier_converges_without_deadlock`; `tests/test_telegram_consume_concurrency.py::test_start_pending_bind_and_unlink_barrier_converges_token_first`; `tests/test_telegram_consume_concurrency.py::test_contact_success_and_protected_relink_issue_barrier_converges`; `tests/test_telegram_consume_concurrency.py::test_simultaneous_protected_relink_token_issue_same_user_has_one_winner` | missing CR-M11-01/02 race edges | Deterministic PostgreSQL barriers complete activation/token issue, start-bind/unlink, contact/protected issue and same-user protected-issue races with token-first convergence and no deadlock. |
| T-M11-078 | `tests/test_csrf_route_inventory.py::test_production_unsafe_routes_are_csrf_protected`; `tests/test_csrf_route_inventory.py::test_detached_transaction_dependencies_validate_csrf_before_returning`; `tests/test_telegram_scope_regression.py::test_no_production_telegram_route_webhook_callback_or_public_csrf_bypass` | detached auth-phase CSRF inventory | Every unsafe production route remains CSRF-protected while the detached dependency opens and closes its own auth transaction. |
| T-M11-079 | `tests/test_auth_dependencies.py::test_current_session_context_repr_redacts_session_and_user_ids` | auth context redaction | Session and user UUIDs never enter the auth-context repr while typed runtime authority remains available in memory. |

File inventory (exactly 31):

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
tests/test_telegram_update_parser.py
tests/test_telegram_consume_start_token_service.py
tests/test_telegram_consume_concurrency.py
tests/test_telegram_post_commit_reply.py
tests/test_auth_telegram_routes.py
tests/test_otp_dispatch_service_postgresql.py
tests/test_otp_issuance_postgresql.py
tests/test_otp_new_code_post.py
tests/test_otp_request_post.py
tests/test_otp_session_login_postgresql.py
tests/test_otp_verification_postgresql.py
tests/test_telegram_link_repository.py
tests/test_auth_dependencies.py
tests/test_csrf_route_inventory.py
tests/test_telegram_scope_regression.py
```

## Implementation Stop Conditions

Stop on protected-source drift, unexplained dirty state, a required semantic
change, non-executable CR order, need for a new dependency/table/process/
infrastructure component, OUT-scope requirement, production secret/PII,
destructive recovery, unavailable mandatory external evidence, or a non-GREEN
exact gate. A repository observation never grants authority to bypass a stop.
