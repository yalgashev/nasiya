# M6 Scope Contract

Status: M6.00 PRE-M6 PRODUCT GATE CLOSED
Sana: 2026-07-27

Bu hujjat M5dan qolgan PRE-M6 savollarini yopadi va M6.01 boshlanishi uchun
implementation chegarasini belgilaydi. Kanonik product talablari
`docs/tt_nasiya_web_v1.md`da qoladi; bu contract M6 ishlarini shu TTga qanday
tartibda tushirishni aniqlashtiradi.

## 1. M6.00 dispositionlari

| ID | Qaror | Scope ta'siri |
| --- | --- | --- |
| M6-D01 | M5.00 yozma discovery natijalari M6 boshlash uchun yetarli dalil sifatida qabul qilindi. Suhbat davomiyligi alohida tasdiqlanmagani M6 delivery blocker emas. | PRE-M6 process gate yopildi; respondent PII saqlanmaydi. |
| CR-M6-01 | Badal debt yoki payment emas. Debt `original_amount`i faqat nasiyaga qoldirilgan net summani bildiradi. | M6 debt yaratishda badal uchun yozuv yaratilmaydi; naqd savdo kvitansiyasi subsystemi scope tashqarisida qoladi. |
| CR-M6-02 | Qoldiqdan katta to'lov qat'iy rad etiladi. | `PAYMENT_EXCEEDS_BALANCE` stable error kodi ishlatiladi; customer credit/wallet subsystemi qo'shilmaydi. |
| CR-M6-03 | Clawback reversal faqat shop owneri yoki platforma admini tomonidan, sabab bilan, auditlangan holda bajariladi. | Cashier/manager reversal qilmaydi; reversal faqat `overdue` holatda va `written_off`dan oldin ruxsat etiladi. |
| M6-D02 | Shop settings M6.01 prerequisite. | Debt/payment implementatsiyasi shop defaultlari tayyor bo'lgandan keyin boshlanadi. |

## 2. M6.01 scope

M6.01 faqat shop settings foundation:

- `shops` jadvaliga settings ustunlarini qo'shish:
  `default_discount_percent`, `default_due_days`, `default_credit_limit`,
  `default_max_open_debts`, `shop_news_daily_limit`.
- Existing shoplar uchun server/default qiymatlar:
  `0`, `30`, `1 000 000`, `2`, `2`.
- ORM, repository va service qatlamida settingsni o'qish/yangilash.
- Owner-only `/shop/settings` UI: GET va POST, PRG, CSRF, form field xatolari,
  mobile-first layout.
- Tenant, role va suspend guardlari: owner yozadi; manager/cashier faqat
  o'qish kontekstiga ega; suspended shopda settings write `SHOP_SUSPENDED`.
- Validation service qatlamida: discount 0..100, due days >= 1, credit limit
  >= 0, max open debts >= 1, news limit >= 0.

## 3. M6.01 scope tashqarisi

| Tashqarida | Sabab |
| --- | --- |
| Debt/payment jadvali va lifecycle implementatsiyasi | M6.02+ ga qoladi; settings foundation avval kerak. |
| Idempotency key storage uchun moliyaviy endpointlar | Debt/payment endpointlari bilan birga keladi. |
| Badal kvitansiyasi yoki naqd savdo subsystemi | CR-M6-01 bo'yicha MVP debt modelidan tashqarida. |
| Customer credit/wallet/overpayment balance | CR-M6-02 bo'yicha ortiqcha to'lov rad etiladi. |
| Clawback reversal UI/service | Debt/clawback modeli paydo bo'lgach M6.0x da implementatsiya qilinadi. |
| Owner application/admin approval/object storage/PWA | M6.01 shop settings foundation uchun shart emas. |

## 4. M6.01 engineering contract

| Yo'nalish | Qoida |
| --- | --- |
| Migration | Alembic orqali bitta yangi revision; `a6b4c2d8e9f1`dan keyin yagona head saqlanadi. |
| Money | `default_credit_limit` Decimal/NUMERIC bilan, `float` ishlatilmaydi. |
| Percent | `default_discount_percent` Decimal/NUMERIC; formulalar keyingi debt domainida bitta joyda ishlaydi. |
| Time | `default_due_days` Toshkent biznes sanasidan `debt.due_date` defaultini hisoblash uchun ishlatiladi; actual `due_date` qarzda sana bo'lib qoladi. |
| Transaction | Service commit/rollback/close qilmaydi; caller transaction egasi. |
| Lock order | Existing shop mutationlarda M5 lock tartibi saqlanadi: `shop -> staff`. |
| Errorlar | Mavjud `FORBIDDEN`, `VALIDATION_ERROR`, `SHOP_SUSPENDED`, `CSRF_FAILED` kodlari qayta ishlatiladi. |
| UI | Cards ichiga card qo'yilmaydi; 320-430px mobil viewportda label/input/buttonlar sig'ishi kerak. |
| Tests | Model metadata, migration, service validation, owner/cashier/manager/tenant/suspend HTTP matrix, CSRF va mobile render regressiyalari. |

## 5. M6.01 green gate

M6.01 tugagan hisoblanadi, agar:

- settings ustunlari bo'sh va existing bazada defaultlar bilan migrate bo'lsa;
- owner settingsni ko'ra va yangilay olsa;
- cashier/manager settingsni o'zgartira olmasa;
- suspended shopda settings POST `SHOP_SUSPENDED` bilan rad etilsa;
- form validatsiyasi field-level xato ko'rsatsa va noto'g'ri qiymat DBga
  yozilmasa;
- `uv run ruff check .`, `uv run ruff format --check .`, tegishli pytestlar va
  `git diff --check` o'tsa.
