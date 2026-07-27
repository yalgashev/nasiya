# M5 Scope Contract

## 1. 12 muzlatilgan product/engineering qarori

| # | Qaror | Scope ta'siri |
| --- | --- | --- |
| 1 | Qarz `debt.due_date` orqali bitta to'lov muddatiga ega bo'ladi. Grafik, schedule item va bo'linma overdue modeli M5ga kirmaydi. | TT 4.2 tasdiqlanadi. |
| 2 | Kassir qarz yaratishda `due_date` ni tahrirlay oladi. Standart muddat keyin do'kon sozlamasidan keladi. | Debt formasi keyingi debt milestone'da shu semantikaga mos bo'ladi. |
| 3 | Ustama uchun TTdagi ikki-summa modeli saqlanadi: `discounted_amount` naqd narx, `original_amount` nasiya narxi. | Alohida `cash_price` ustuni qo'shilmaydi. |
| 4 | Oldindan to'lov qarz yoki `payment` emas. `original_amount` faqat nasiyaga berilgan summani bildiradi. | Badal uchun alohida naqd savdo kvitansiyasi M5/MVP debt modelidan tashqarida. |
| 5 | Qisman to'lov uchun minimal summa yo'q. | Payment formasi keyin qoldiq bilan ishlaydi. |
| 6 | Qoldiqdan ortiq to'lov qat'iy rad etiladi. | Mijoz krediti subsystemi qo'shilmaydi. |
| 7 | Kechikishda imtiyoz muddati, kunlik jarima va avtomatik `written_off` yo'q. Sabab bilan vakolatli clawback bekor qilish kerak. | Clawback reversal roli keyingi CR/TT aniqlashtirishida belgilanadi. |
| 8 | Bitta `user` bir nechta `shop`ga a'zo bo'la oladi. `shop_staff` uniqueness `(shop_id, user_id)` bo'yicha bo'ladi. | Kelajakdagi multi-shop owner/account migratsiyasidan qochiladi. |
| 9 | Shop switcher faqat faol shop a'zoligi bittadan ko'p bo'lganda ko'rinadi. | Bitta shopli do'kon oqimi oddiy qoladi. |
| 10 | Suspend qilingan shopda barcha faol xodimlar o'z roli doirasida read-only ko'radi, barcha write amallar `SHOP_SUSPENDED` bilan rad etiladi. | Deaktivatsiya qilingan membership read-only huquq olmaydi. |
| 11 | M5da xodim faqat bazada mavjud authenticated userni kanonik telefon orqali shopga bog'lash bilan qo'shiladi. | Invitation va owner tomonidan yangi account yaratish keyingi milestone. |
| 12 | M5da shop faqat identity va tenant chegarasini oladi; shop sozlamalari M6 prerequisite. Owner M5da mavjud M2 CLI userdan olinib, CLI orqali shopga owner sifatida ulanadi. | Kredit limiti, discount, default due date, news limit maydonlari front-load qilinmaydi. |

## 2. M5.02 TT xaritasi

| TT bo'limi | Qisqa talab | M5 holati |
| --- | --- | --- |
| 3.2 | `shop`, `shop_staff`, suspend/deactivate/audit vaqtlarini UTC saqlash | Kiradi |
| 3.2 | Biznes sana: due date, overdue, scheduler, hisobot | Keyinga qoladi |
| 3.3 | Tashqi ko'rinadigan shop/staff/application ID taxmin qilib bo'lmasin | Kiradi |
| 3.4 | Qarz/to'lov yaratishda idempotency key | Keyinga qoladi |
| 3.4 | M5 formalarida PRG va double-submit himoya | Kiradi |
| 5 | Tenant scope har route'da server tomonda tekshiriladi | Kiradi |
| 5 | Owner roli: shop, staff, barcha operatsiyalar | Kiradi |
| 5 | Cashier roli: mijoz qo'shish, qarz ochish, to'lov qabul qilish | Permission modelga kiradi; debt/payment amallari keyinga qoladi |
| 5 | Manager roli | M5dan tashqarida; TT role scope uchun CR-M5-01 |
| 6.2 | Shop yaratish va shop identity/tenant chegarasi | Kiradi |
| 6.2 | Shop sozlamalari: limitlar, discount, default due date, news limit | Keyinga qoladi, M6 prerequisite |
| 6.2 | Staff qo'shish, rol o'zgartirish, deactivate | Kiradi |
| 6.2 | `LAST_OWNER` himoyasi | Kiradi |
| 6.2 | Owner application, hujjat bilan ro'yxatdan o'tish | Keyinga qoladi, M5 CLI bootstrap ishlatiladi |
| 6.11.2 | Admin owner applicationni tasdiqlaydi/rad etadi | Keyinga qoladi |
| 6.11.7 | Manual suspend, sabab majburiy, billing suspend yo'q | Kiradi |
| 6.13 | Mobile-first, Jinja2/HTMX, PRG, CSRF, form xatolari | M5 ekranlariga kiradi |
| 6.13 | PWA, service worker, offline shell | Keyinga qoladi |
| 7 `shop` | Shop profili, tenant identity, suspend holati | Kiradi |
| 7 `shop_staff` | Shop-user a'zoligi va roli | Kiradi |
| 7 `owner_application` | Owner ariza obyekti | Keyinga qoladi |
| 8 tenant | Cross-shop IDOR/leakage yo'q, har route'da tenant check | Kiradi |
| 8 session | Server-side session ishlatiladi; membership har so'rovda tekshiriladi | Kiradi |
| 8 CSRF | Barcha state-changing M5 POSTlar CSRF bilan | Kiradi |
| 8 header | CSP, frame/options, nosniff, referrer policy | M5 sahifalariga kiradi |
| 8 leakage | Stack trace, ichki tafsilot, boshqa tenant xom ma'lumoti chiqmaydi | Kiradi |
| 14 | `UNAUTHORIZED`, `FORBIDDEN`, `VALIDATION_ERROR`, `SESSION_EXPIRED`, `CSRF_FAILED` | Kiradi |
| 14 | `LAST_OWNER`, `SHOP_SUSPENDED` | Kiradi |
| 14 | `APPLICATION_PENDING` | Keyinga qoladi |
| 14 | File/document errorlari | Keyinga qoladi |

## 3. M5.03 real repository konvensiyalari

| Konvensiya | Dalil |
| --- | --- |
| UUID primary key ORMda `PostgresUUID(as_uuid=True)` va `default=uuid4` bilan beriladi. | `app/auth/models.py:26` `id: Mapped[UUID] = mapped_column(`; `app/auth/models.py:27` `PostgresUUID(as_uuid=True),`; `app/auth/models.py:29` `default=uuid4,` |
| Migrationda UUID PK server default `gen_random_uuid()` bilan yaratiladi. | `alembic/versions/843f79654ade_create_users_table.py:28` `postgresql.UUID(as_uuid=True),`; `alembic/versions/843f79654ade_create_users_table.py:29` `server_default=sa.text("gen_random_uuid()"),` |
| Timestamplar timezone-aware `DateTime(timezone=True)` va UTC helper bilan ishlaydi. | `app/auth/models.py:19` `def utc_now() -> datetime:`; `app/auth/models.py:20` `return datetime.now(UTC)`; `app/auth/models.py:44` `DateTime(timezone=True),` |
| Naive timestamp rad etiladi. | `app/auth/sessions.py:353` `def _as_utc(value: datetime) -> datetime:`; `app/auth/sessions.py:354` `if value.tzinfo is None or value.utcoffset() is None:` |
| DB statuslar SQLAlchemy `Enum` emas, `String` va check constraint bilan saqlanadi. | `app/customer/models.py:38` `onboarding_status: Mapped[str] = mapped_column(`; `app/customer/models.py:39` `String(32),`; `app/customer/models.py:22` `CheckConstraint(` |
| Python ichki enumlari `StrEnum`. | `app/auth/error_codes.py:9` `class ErrorCode(StrEnum):`; `app/auth/sessions.py:64` `class UserSessionStatus(StrEnum):` |
| Constraint/index nomlari manual `pk_`, `fk_`, `ck_`, `uq_`, `ix_` prefixlari bilan yuritiladi. | `alembic/versions/352b864d3118_create_sessions_table.py:57` `name=op.f("fk_sessions_user_id_users_id"),`; `app/telegram/models.py:36` `name="ck_telegram_links_state_consistent",`; `app/telegram/models.py:39` `"uq_telegram_links_active_chat_id",` |
| `Base.metadata.naming_convention` mavjud emas. | `app/db.py:10` `class Base(DeclarativeBase):`; `app/db.py:11` `pass` |
| Repository/service imzolarida `session` birinchi parametr bo'ladi. | `app/customer/repository.py:16` `def create_customer_draft_if_missing(`; `app/customer/repository.py:17` `session: Session,`; `app/telegram/repository.py:235` `def insert_telegram_link_token(` |
| Keyword-only parametrlar bor, lekin universal qoida emas. | `app/telegram/repository.py:330` `def get_telegram_link_tokens_eligible_for_purge(`; `app/telegram/repository.py:333` `*,`; `app/customer/repository.py:16` positional uslubda davom etadi |
| Request transaction boundary dependencyda: success commit, exception rollback, finally close. | `app/db.py:25` `def get_database_session()`; `app/db.py:29` `session.commit()`; `app/db.py:31` `session.rollback()`; `app/db.py:34` `session.close()` |
| CLI o'z transaction boundarysiga ega. | `app/cli.py:63` `with session_factory() as session:`; `app/cli.py:99` `session.commit()`; `app/cli.py:102` `except SQLAlchemyError:` |
| Expected conflict pathlarda savepoint ishlatiladi. | `app/telegram/repository.py:269` `with session.begin_nested():`; `app/telegram/service.py:454` `with session.begin_nested():` |
| Alembic revisionlar typed metadata, `upgrade()` va `downgrade()` uslubida. | `alembic/versions/4f9c2d7a1b03_create_telegram_linking_tables.py:16` `revision: str = "4f9c2d7a1b03"`; `alembic/versions/4f9c2d7a1b03_create_telegram_linking_tables.py:22` `def upgrade() -> None:`; `alembic/versions/4f9c2d7a1b03_create_telegram_linking_tables.py:139` `def downgrade() -> None:` |
| Test DB URL guard PostgreSQL va `_test` suffixini talab qiladi. | `tests/postgresql.py:19` `def validate_test_database_url`; `tests/postgresql.py:25` `if driver_name.startswith("sqlite"):`; `tests/postgresql.py:29` `not url.database.endswith("_test")` |
| Cleanup allowlist child-first tartibda. | `tests/postgresql.py:8` `M2_CLEANUP_TABLE_NAMES = (`; `tests/postgresql.py:9` `"telegram_link_events",`; `tests/postgresql.py:14` `"sessions",`; `tests/postgresql.py:15` `"users",` |
| Concurrency harness thread/barrier bilan yoziladi. | `tests/test_telegram_consume_concurrency.py:112` `token_lock_barrier = Barrier(2)`; `tests/test_telegram_consume_concurrency.py:179` `executor = ThreadPoolExecutor(max_workers=2)` |
| Auth dependency nomlari: `get_current_session_context`, `require_user`. | `app/auth/deps.py:113` `def get_current_session_context(`; `app/auth/deps.py:152` `def require_user(` |
| Session ORM modeli `sessions` jadvali, update servisi `touch_session`. | `app/auth/models.py:56` `class Session(Base):`; `app/auth/models.py:57` `__tablename__ = "sessions"`; `app/auth/sessions.py:187` `def touch_session(` |
| CSRF form/header va no-store mexanizmi mavjud. | `app/auth/deps.py:34` `CSRF_FORM_FIELD_NAME = "csrf_token"`; `app/auth/deps.py:35` `CSRF_HEADER_NAME = "X-CSRF-Token"`; `app/security_headers.py:61` `def mark_auth_response_no_store` |
| Security headers middleware mavjud. | `app/security_headers.py:21` `SECURITY_HEADERS`; `app/security_headers.py:23` `"X-Frame-Options": "DENY"`; `app/security_headers.py:24` `"X-Content-Type-Options": "nosniff"` |
| Telefon kanoniklashtirish funksiyasi bitta joyda. | `app/auth/phone.py:18` `def normalize_uzbekistan_phone(raw_phone: str) -> str:`; `app/auth/phone.py:38` `canonical_phone = f"+{candidate}"` |
| Stable error katalogi immutable mapping sifatida. | `app/auth/error_codes.py:9` `class ErrorCode(StrEnum):`; `app/auth/error_codes.py:28` `ERROR_CATALOG: Final[Mapping[ErrorCode, ErrorDefinition]] = MappingProxyType(` |
| CLI registration va production guard local-only. | `app/cli.py:30` `create_local_user = subparsers.add_parser("create-local-user")`; `app/cli.py:19` `LOCAL_ENVIRONMENTS = frozenset({"development", "local", "testing"})`; `app/cli.py:44` `def ensure_local_environment(settings: Settings) -> None:` |

## 4. M5 artefaktlari

| Tur | Artefakt | M5 mazmuni |
| --- | --- | --- |
| Jadval | `shops` | Shop identity, tenant root, suspend status/reason/timestamps. Shop settings maydonlari yo'q. |
| Jadval | `shop_staff` | `shop_id`, `user_id`, role, active/deactivated metadata, unique `(shop_id, user_id)`, `LAST_OWNER` himoyasi. |
| Model o'zgarishi | `sessions.active_shop_id` | Joriy session tanlagan shop; permission manbai emas, har requestda membership tekshiriladi. |
| Model | `Shop`, `ShopStaff`, `StaffRole`, `ShopStatus` | ORM M1-M4 konvensiyalariga mos: UUID, aware timestamps, String/check status. |
| Servis | Shop context service | Active shopni tanlash, membershipni resolve qilish, tenant guard, role guard. |
| Servis | Staff service | Existing userni canonical phone orqali owner/cashier sifatida qo'shish, rol o'zgartirish, deactivate, last owner guard. |
| Servis | Suspend policy | Suspended shopda write rad etish, role-scoped read-only saqlash. |
| Route | `/shop/*` | Shop dashboard, staff ro'yxati, staff add/update/deactivate, active shop switch, read-only suspend UI. |
| CLI | Local shop bootstrap | Mavjud M2 CLI userni canonical phone orqali topib shop yaratish va owner membershipga ulash; local-only production guard. |
| Docs | `docs/m5_scope_contract.md` | M5 scope va green gate contracti. |
| Docs | `docs/m5_discovery_notes.md` | M5.00 discovery natijalari va CR nomzodlari. |

## 5. M5 scope tashqarisi

| Tashqarida | Sabab |
| --- | --- |
| Owner application va hujjat yuklash oqimi | M5da owner CLI bootstrap orqali ulanadi. |
| Invitation flow va owner tomonidan yangi user account yaratish | M5da faqat mavjud authenticated user canonical phone orqali ulanadi. |
| Shop sozlamalari: credit limit, discount, default due date, max open debts, news limit | M6 prerequisite. |
| Debt/payment domain implementatsiyasi | M5 faqat shop/staff/tenant foundation. |
| Installment schedule yoki grafik | Muzlatilgan qarorga ko'ra kirmaydi. |
| Manager role UI/policy | M5dan tashqarida; CR-M5-01 yoki yangi TT versiyasi kerak. |
| Billing, subscription va billing sababli suspend | TT 4.10 bo'yicha MVPdan tashqarida. |
| PWA/service worker/offline shell | M5 shop foundation uchun shart emas. |
| Platform admin owner approval UI | Owner application oqimi bilan keyinga qoladi. |
| Customer credit subsystem yoki overpayment credit | Ortiqcha payment rad etiladi. |
| Naqd savdo kvitansiyasi subsystemi | Badal debt/payment emas. |
| Clawback reversal implementatsiyasi | Debt/clawback milestone va actor CR orqali keyinga qoladi. |

## 6. Ownership klassifikatsiyasi

| Obyekt/amal | Ownership | Qoida |
| --- | --- | --- |
| `user` | Global auth identity | Shopga tegishli emas; bir user bir nechta shopga a'zo bo'lishi mumkin. |
| `session` | User/session-owned | `active_shop_id` faqat tanlangan context, permission manbai emas. |
| `shop` | Tenant root, owner-managed, platform-suspendable | Owner shop identityni boshqaradi; platform admin sabab bilan suspend qiladi. |
| `shop_staff` | Shop-owned membership | Owner boshqaradi; har requestda active membership tekshiriladi. |
| Staff add/deactivate | Owner action | Actor owner bo'lishi va shu shopda active membershipga ega bo'lishi kerak. |
| Shop switch | User action, membership-gated | Faqat userning active membershipi bor shopga switch qilinadi. |
| Suspend | Platform action | Active staff read-only scope saqlanadi; write rad etiladi. |
| Audit metadata | Platform integrity | Xom PII va secret saqlanmaydi; actor/action/reason yetarli bo'ladi. |

## 7. Lock order: shop -> staff

| Vaziyat | Lock order |
| --- | --- |
| Shop va staffni bir transactionda o'qish/o'zgartirish | Avval `shop`, keyin shu shopga tegishli `shop_staff`. |
| Bir nechta staff qatori kerak bo'lsa | `shop` lockdan keyin staff qatorlari deterministic tartibda: `user_id` yoki `id` bo'yicha. |
| Last owner guard | Avval `shop`, keyin owner role active `shop_staff` qatorlari. |
| Deactivate/role change | Avval `shop`, keyin target `shop_staff`, zarur bo'lsa owner count staff qatorlari. |
| Active shop switch | Write bo'lsa avval target `shop`, keyin actor `shop_staff`, keyin `session.active_shop_id` update. |

Hech bir M5 code path `shop_staff -> shop` teskari tartibda lock olmaydi.

## 8. Ishlatiladigan TT error kodlari

| Kod | M5da ishlatiladigan joy |
| --- | --- |
| `UNAUTHORIZED` | Sessiya yo'q/yaroqsiz yoki user inactive. |
| `SESSION_EXPIRED` | Session muddati tugagan. |
| `CSRF_FAILED` | State-changing shop/staff POSTlarda CSRF xatosi. |
| `FORBIDDEN` | Role yoki tenant membership ruxsati yo'q. |
| `VALIDATION_ERROR` | Shop/staff input noto'g'ri. |
| `LAST_OWNER` | Oxirgi active ownerni deactivate qilish yoki owner rolsiz qoldirish. |
| `SHOP_SUSPENDED` | Suspended shopdagi barcha write amallar. |

`APPLICATION_PENDING` M5da ishlatilmaydi, chunki owner application oqimi scope tashqarisida.

## 9. Green gate ta'riflari

| Gate | Ta'rif |
| --- | --- |
| M5 TECHNICAL GREEN | M5 artefaktlari implementatsiya qilingan; migration bitta Alembic head beradi; test DB guard saqlangan; M5 testlari va mavjud regression testlar o'tgan; `git status --short` toza; M5 scope contractga zid code path yo'q. |
| M5 REMOTE GREEN | M5 TECHNICAL GREEN commit qilingan va `origin/main` bilan sinxron; remote divergence `0 behind / 0 ahead`; remote CI/checklar mavjud bo'lsa yashil; M5 closeout commit logda ko'rinadi. |
| PRE-M6 PRODUCT GATE | M5.00 discovery acceptance holati PO tomonidan yopilgan; M6 shop settings, debt/payment, overpayment, badal va clawback reversal CR/TT aniqlashtirishlari hal qilingan; M5 scope tashqarisidagi ish M6 scopega qayta kiritilgan yoki alohida qoldirilgan. |
