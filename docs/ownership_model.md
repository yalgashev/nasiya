# Ownership Model

## Ownership turlari

| Tur | Ma'nosi | Xavfsizlik oqibati |
| --- | --- | --- |
| tenant-owned | Qator bitta `shop` tenant chegarasiga tegishli. | Har read/write requested yoki resolved `shop_id` bilan chegaralanadi. |
| user-owned | Qator global `user` identity yoki user sessioniga tegishli. | User o'z resursini ko'radi; shop huquqi alohida membership orqali tekshiriladi. |
| platform-owned | Qator platforma konfiguratsiyasi, audit yoki scheduler nazoratida. | Oddiy shop owner/cashier uni bevosita o'zgartirmaydi. |
| explicit-scope | Qator bitta turga sig'maydi; owner scope ustunlari yoki parent zanjiri aniq ko'rsatiladi. | Query har doim shu explicit scope bo'yicha tekshiradi. |

## TT §7 obyektlari klassifikatsiyasi

| Obyekt | Ownership | Scope qoidasi |
| --- | --- | --- |
| `user` | user-owned | Auth identity; shop tenant emas. Shop huquqi `shop_staff` orqali olinadi. |
| `session` | user-owned | `active_shop_id` tanlangan context xolos; permission manbai emas. |
| `telegram_link` | user-owned | `user_id` orqali userga tegishli; shop tenantga ulanmaydi. |
| `otp_challenge` | explicit-scope | Telefon/kanal/maqsad scope'i bilan transient auth resurs. |
| `shop` | tenant-owned | Tenant root. |
| `shop_staff` | tenant-owned | `shop_id` orqali tenantga tegishli membership. |
| `owner_application` | explicit-scope | Ariza user/applicant scope'ida, platform qarori bilan shopga aylanishi mumkin. |
| `customer_lead` | tenant-owned | Lead qaysi shop tomonidan yaratilgan bo'lsa, shu shop scope'ida. |
| `customer` | user-owned | Mijoz global identity/profile; shop bilan aloqa `shop_customer` orqali. |
| `customer_document` | explicit-scope | Hujjat owner scope'i (`customer`, `owner_application` yoki boshqa parent) orqali aniqlanadi. |
| `shop_customer` | tenant-owned | `shop_id` orqali tenant-customer bog'lami. |
| `debt` | tenant-owned | Shop bevosita `shop_id` yoki `shop_customer` parent zanjiri orqali derivable bo'lishi shart. |
| `payment` | tenant-owned | Tenant scope `debt` parentidan olinadi. |
| `offer` | platform-owned | Oferta versiyalari global platform hujjati. |
| `acceptance` | explicit-scope | User, oferta versiyasi va zarur bo'lsa debt/shop parenti bilan scope qilinadi. |
| `rating_event` | explicit-scope | Global ratingga ta'sir qiladi, lekin actor/source parenti aniq yoziladi. |
| `shop_news` | tenant-owned | `shop_id` orqali tenantga tegishli. |
| `notification` | explicit-scope | Recipient user va source object scope'i alohida ko'rsatiladi. |
| `idempotency_key` | explicit-scope | Actor, endpoint va request scope'i bo'yicha ajratiladi. |
| `audit_log` | platform-owned | Append-only platform integrity log; source scope redaksiyalangan holda yoziladi. |
| `disclosure_view_log` | platform-owned | Ko'rish hodisasi platform audit resursi; actor va viewed object explicit scope. |
| `job_run` | platform-owned | Scheduler/platform ishga tushish jurnali. |
| `object_file` | explicit-scope | `owner_scope` bucket/key metadata orqali aniq belgilanadi. |
| `system_setting` | platform-owned | Global konfiguratsiya; tenant querylariga kirmaydi. |

## Tenant derivability qoidasi

Tenant-owned qator uchun `shop_id` bevosita ustun sifatida yoki majburiy parent
zanjiri orqali deterministik derivable bo'lishi kerak. Query `id` bo'yicha
topish bilan cheklanmaydi: requested/resolved `shop_id` ham query predicate yoki
parent join ichida qatnashadi.

Parent zanjiri nullable bo'lmasligi yoki service darajasida aniq rad etilishi
kerak. Tenant scope "keyin route tekshiradi" degan yashirin taxminga tayanmaydi;
repository queryning o'zi boshqa tenant qatorini qaytara olmaydigan shaklda
yoziladi.

## Payment/debt misoli

`debt` tenant-owned. Agar debt jadvalida `shop_id` bevosita bo'lsa, read/write:

```text
WHERE debt.id = :debt_id AND debt.shop_id = :current_shop_id
```

Agar debt scope'i `shop_customer` orqali derivable bo'lsa, query parent orqali
join qiladi:

```text
debt -> shop_customer -> shop_id = :current_shop_id
```

`payment` ham tenant-owned, lekin tenantni `debt` parentidan oladi. Shuning
uchun `payment.id = :payment_id` yetarli emas; payment query debtga join qilib
debtning shop scope'ini ham tekshiradi. Qisman to'lov, void yoki ortiqcha
to'lov rad etish logiclari ham xuddi shu parent scope ichida bajariladi.

## Direct query boundary

M5 shop modellariga DML/select faqat [app/shop/repository.py](../app/shop/repository.py)
orqali yoziladi:

- `Shop`
- `ShopStaff`
- `ShopStatusEvent`
- `ShopStaffEvent`

Model importi taqiqlanmaydi: type annotation, context dataclass, metadata,
testlar va migrationlar modelni import qilishi mumkin. Boundaryning maqsadi
shundaki, shop-scoped querylar bitta joyda ko'rinadi va tenant predicate drift
qilmaydi.

`tests/`, `docs/` va migration fayllari containment guard scope'idan tashqarida.
Production `app/` ichida yangi to'g'ridan-to'g'ri query kerak bo'lsa, avval
repository API qo'shiladi. Allowlist faqat izohli va ko'rib chiqilgan istisno
bilan kengaytiriladi.

## Lock order: shop -> staff

Existing shop mutation bitta transactionda `shop` va `shop_staff`ni o'qisa yoki
o'zgartirsa, lock tartibi:

```text
shop -> staff
```

Bir nechta staff qatori kerak bo'lsa, `shop` lockdan keyin staff qatorlari
deterministic tartibda olinadi: `user_id` yoki `id` bo'yicha. Last-owner guard,
role change va revoke flowlari shu tartibdan chiqmaydi.

Cheklov: docstring yoki AST guard bu lock orderni matematik isbotlamaydi. To'g'ri
yo'lni majburiyga yaqinlashtirish uchun shop repositoryda private lock helper
va `_LockedShop` marker ishlatiladi; service faqat shu marker orqali staff lock
helperlariga o'tadi. Haqiqiy dalil esa PostgreSQL concurrency testlari bilan
olinadi.

Yangi existing-shop mutation qo'shilsa, lock order tekshiruvi va race test
majburiy. Race test bir vaqtning o'zida ikki transactionni yurgizib, deadlock,
last-owner buzilishi yoki cross-shop mutation yo'qligini ko'rsatishi kerak.
