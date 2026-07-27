# M5 Final Report

**Status: M5 TECHNICAL GREEN — REMOTE CI PENDING**

Sana: 2026-07-27

Bu status faqat local validationga tegishli. Branch push qilinmagan, GitHub
Actions ishga tushmagan va remote CI GREEN deb tasdiqlanmagan.

## 1. Fayllar inventari

| Qatlam | Fayllar |
| --- | --- |
| Shop domain | `app/shop/__init__.py`, `enums.py`, `values.py`, `models.py`, `repository.py`, `service.py`, `context.py`, `dependencies.py`, `policy.py`, `router.py` |
| Session/auth integratsiyasi | `app/auth/models.py`, `app/auth/sessions.py`, `app/auth/error_codes.py`, `app/auth/phone.py` |
| Application va CLI | `app/main.py`, `app/cli.py` |
| Migration | `alembic/versions/a6b4c2d8e9f1_create_m5_shop_tables.py`, `alembic/env.py` metadata wiring |
| UI | `app/templates/shop/workspace.html`, `select.html`, `staff.html`, `app/static/css/app.css` |
| Asosiy M5 testlari | `tests/test_shop_*.py`, `tests/test_cli_shop.py` |
| Shared regression | `tests/postgresql.py` va M1-M4 auth/customer/Telegram testlaridagi M5 compatibility yangilanishlari |
| Formatter-only compatibility | Mavjud M1-M4 migration va auth/Telegram source fayllaridagi semantik bo'lmagan Ruff format o'zgarishlari |
| Hujjatlar | `docs/m5_discovery_notes.md`, `m5_scope_contract.md`, `ownership_model.md`, `m5_shop_viewport_checklist.md`, ushbu report |
| Developer/CI | `README.md`, `.github/workflows/ci.yml` |

Local full suite hozircha untracked bo'lgan
`tests/test_shop_session_desync_http.py` va
`tests/test_shop_get_leakage_xss_audit.py` fayllarini ham qamradi. Remote CI
ularni faqat kelajakdagi commitga kiritilgandan keyin ko'radi.

## 2. Schema

| Obyekt | Schema |
| --- | --- |
| `shops` | `id`, `name`, `phone`, `address_text`, `status`, `created_at`, `updated_at` |
| `shop_staff` | `id`, `shop_id`, `user_id`, `role`, `is_active`, `created_at`, `updated_at`, `revoked_at` |
| `shop_status_events` | `id`, `shop_id`, `action`, `actor_user_id`, `reason`, `created_at` |
| `shop_staff_events` | `id`, `shop_id`, `subject_user_id`, `action`, `old_role`, `new_role`, `actor_user_id`, `created_at` |
| `sessions` o'zgarishi | Nullable UUID `active_shop_id`, named FK `shops.id`, `ON DELETE RESTRICT` |

Barcha M5 primary keylari UUID. Timestamp ustunlari timezone-aware. Barcha M5
FKlari `ON DELETE RESTRICT`. `(shop_id, user_id)` unique; faqat `user_id`
bo'yicha unique yo'q. Status, role, active/revoked va event semantic
kombinatsiyalari named CHECK constraintlar bilan himoyalangan. Shop settings,
`owner_id`, `pending`, JSON va payload maydonlari yo'q.

Alembic revision: `a6b4c2d8e9f1`; yagona head.

## 3. Service signatures

```text
provision_active_shop(
    session, *, shop_id, name, phone, address_text,
    owner_user_id, actor_user_id=None, now=None
)
suspend_shop(
    session, *, shop_id, actor_user_id, reason, now=None
)
reactivate_shop(
    session, *, shop_id, actor_user_id, reason, now=None
)
add_staff(
    session, *, shop_id, actor_user_id, phone, role, now=None
)
change_staff_role(
    session, *, shop_id, actor_user_id, target_staff_id, new_role, now=None
)
revoke_staff(
    session, *, shop_id, actor_user_id, target_staff_id, now=None
)
resolve_current_shop(
    session, *, auth_session, user_id
)
```

Servislar commit, full rollback yoki session close qilmaydi. Caller transaction
egasi. Existing-shop mutation lock tartibi `shop -> staff`; `_LockedShop`
markeri staff lock helperlaridan oldin shu sessionda shop lock olinganini
kuchaytiradi. Savepoint expected conflictdan keyin caller sessionni usable
qoldiradi.

## 4. Route security matrix

| Method va route | Klass | Dependency/policy | Stable error yoki outcome |
| --- | --- | --- | --- |
| `GET /shop/select` | Context | `require_user`; membership shart emas | `UNAUTHORIZED`, `SESSION_EXPIRED` yoki 200 |
| `POST /shop/select` | Context mutation | `require_user` + CSRF + active membership | `CSRF_FAILED`, `FORBIDDEN`; suspended target ruxsat |
| `GET /shop` | Tenant read | `require_user` + `require_shop_staff` | Login/select redirect; active va suspended 200 |
| `GET /shop/staff` | Tenant read | `require_user` + `require_shop_staff` | Login/select redirect; active va suspended 200 |
| `POST /shop/staff/add` | Owner business mutation | Owner + CSRF + service policy | `FORBIDDEN`, `CSRF_FAILED`, `SHOP_SUSPENDED`, `VALIDATION_ERROR` |
| `POST /shop/staff/{staff_id}/role` | Owner business mutation | Owner + CSRF + tenant-scoped staff service | `FORBIDDEN`, `CSRF_FAILED`, `SHOP_SUSPENDED`, `LAST_OWNER`, `VALIDATION_ERROR` |
| `POST /shop/staff/{staff_id}/revoke` | Owner business mutation | Owner + CSRF + tenant-scoped staff service | `FORBIDDEN`, `CSRF_FAILED`, `SHOP_SUSPENDED`, `LAST_OWNER`; safe not-found/no-op |

Har request membership, role va shop statusini DBdan qayta resolve qiladi.
Suspended shop role-scoped read-only; `/shop/select` session-context mutation
bo'lgani uchun `SHOP_SUSPENDED` bilan bloklanmaydi. Barcha shop sahifalarida
no-store va mavjud security headerlar saqlangan.

## 5. CLI

```text
shop create --name --phone [--address] --owner-phone
shop suspend <shop_uuid> --reason "..."
shop reactivate <shop_uuid> --reason "..."
demo seed
```

Buyruqlar development/local/testing muhitlari bilan cheklangan. CLI transaction
va commit egasi; service emas. `shop create` yangi UUID yaratadi. Demo seed
Shop A/B uchun source-coded fixed UUID ishlatadi, mavjudlikni faqat UUID
bo'yicha aniqlaydi, mismatchda fail-closed va takroriy runda state/event
countlarini o'zgartirmaydi.

## 6. Test kategoriyalari

Full collection: **1113 test**.

Dedicated M5 collection: **300 test**.

| Kategoriya | Test soni |
| --- | ---: |
| Persistence, constraint, metadata va repository | 71 |
| Context, policy, dependency, isolation va containment | 35 |
| Lifecycle service, transaction, exactly-once va concurrency | 74 |
| HTTP, route matrix, IDOR, accessibility, desync, leakage va XSS | 109 |
| Development CLI va idempotent demo seed | 11 |

Skip: **0**. Xfail: **0**. Warning: **1**, mavjud dependency warning:
FastAPI `TestClient` ichidagi Starlette `httpx` deprecation. Application
failure yoki yangi M5 regressiyasi emas.

## 7. TT qamrovi

### Qoplangan

- TT 3.2: M5 UTC/timestamptz va lifecycle timestamps.
- TT 3.3: taxmin qilib bo'lmaydigan UUID shop/staff identifikatorlari.
- TT 3.4: M5 POSTlarda CSRF, PRG va double-submit regressiya qamrovi.
- TT 5: owner/manager/cashier role qiymatlari va server-side tenant check.
- TT 6.2: minimal shop identity, staff add/role/revoke va `LAST_OWNER`.
- TT 6.11.7: sabab bilan manual suspend/reactivate primitive.
- TT 6.13: mobile-first shop UI, 320/430px checklist, 44px target va label/focus talablari.
- TT 7: `shop` va `shop_staff` obyektlari; append-only M5 lifecycle eventlari.
- TT 8: server-side session context, CSRF, security headers, no-store,
  cross-shop IDOR va leakage himoyasi.
- TT 14: M5 ishlatadigan `FORBIDDEN`, `SHOP_SUSPENDED`, `LAST_OWNER`,
  `REASON_REQUIRED` va reused auth/validation kodlari.

### Keyinga qolgan

- `owner_application`, platform admin approval UI va production admin authorization.
- Shop settings: credit limit, discount, default due date va boshqa M6 prerequisite maydonlari.
- Debt/payment, badal, overpayment, due-date formasi va clawback reversal.
- PWA/service worker/offline shell.
- Manager-specific settings yoki owner vakolati.
- PRE-M6 CR-M6-01, CR-M6-02 va CR-M6-03 TT/PO dispositionlari.
- M5.00dagi har bir owner suhbati 30-40 daqiqa bo'lganini tasdiqlash process mezoni.

## 8. Local validation dalillari

| Tekshiruv | Natija |
| --- | --- |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS, 165 files already formatted |
| `pytest -q` | 1113 passed, 0 skip, 0 xfail, 1 warning, 77.20s |
| `pytest --durations=10` | 1113 passed, 0 skip, 0 xfail, 1 warning, 78.58s |
| `alembic heads` | `a6b4c2d8e9f1 (head)`, yagona head |
| `git diff --check` | PASS |
| Tracked secret/PII audit | PASS, quyidagi klassifikatsiya bilan |
| M5.42 targeted walk | `head -> -1 -> head`, PASS |
| M5.42 empty-DB walk | `base -> head -> base`, application table qoldig'i 0 |
| M5.43 clean Docker rebuild | `docker compose build --no-cache`, PASS |
| M5.43 16-qadamli smoke | 16/16 PASS, runtime 5xx 0 |

Docker builddagi `pip` root-user warning avvaldan mavjud builder-stage
`Dockerfile` oqimidan keladi; runtime regressiyasi emas. Container runtime
loglarida warning/error/traceback topilmadi.

Tracked audit dalili:

- tracked `.env`, private key, `.pem`, `.key`, `.p12`, `.pfx`, `.log` yoki
  `.snap` fayl yo'q; faqat `.env.example` tracked;
- private-key header, AWS access key, GitHub token, OpenAI-style key va
  Telegram bot token patternlari topilmadi;
- `TELEGRAM_BOT_TOKEN` credential assignmenti topilmadi;
- `dev_pass`, `change-me-local...` va `ci-test-only...` topilmalari faqat
  documented local/CI placeholderlar;
- `app/cli.py`dagi aniq nomlangan fixed demo telefon konstantalaridan tashqari
  production `app/` ichida full phone/JSHSHIR literal yo'q;
- README telefonlari development misoli, testlardagi PII qiymatlar synthetic
  fixture; `uv.lock`dagi raqamli hitlar package hash/metadata false positive.

### Slowest 10

| Vaqt | Test |
| ---: | --- |
| 1.03s | `test_post_login_unknown_wrong_null_hash_and_inactive_share_generic_message` |
| 0.68s | `test_customer_web_flow_is_own_only_for_two_authenticated_users` |
| 0.60s | `test_revoke_others_revokes_second_and_third_clients_but_keeps_current` |
| 0.59s | `test_demo_seed_first_run_creates_expected_state_and_second_run_is_idempotent` |
| 0.58s | `test_issue_link_token_parallel_shared_ip_respects_twenty_ceiling` |
| 0.48s | `test_demo_seed_uses_fixed_uuid_even_when_another_shop_has_same_name` |
| 0.47s | `test_two_clients_can_revoke_one_session_without_logging_out_current_client` |
| 0.45s | `test_current_session_revoke_logs_out_that_client_only` |
| 0.45s | `test_create_local_user_reset_password_updates_existing_user_only` |
| 0.41s | `test_demo_seed_fixed_uuid_conflict_fails_closed_without_mutation` |

## 9. CI holati

Mavjud `.github/workflows/ci.yml` ichidagi bitta job saqlandi; yangi job
yaratilmadi. U quyidagilarni bajaradi:

1. PostgreSQL `nasiya_test` service.
2. `alembic upgrade head` va M5 head assertioni.
3. `ruff check` va `ruff format --check`.
4. Nomlangan M5 containment guard.
5. Full pytest.

Workflow local statik auditdan o'tdi, lekin ushbu o'zgarishlar push qilinmagan.
Shuning uchun remote GitHub Actions holati ochiq va **PENDING**.
