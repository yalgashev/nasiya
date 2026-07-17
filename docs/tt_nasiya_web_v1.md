# Texnik Topshiriq — Nasiya Web Ilovasi

Status: Yakuniy texnik topshiriq — implementatsiyaga tayyor
Versiya: 1.0 (web)
Sana: 2026-07-14
Doira: bitta mobile-first web ilova (FastAPI + Jinja2 + HTMX) + PostgreSQL

> Eslatma: Bu hujjat yangi, mustaqil mahsulot sifatida yoziladi. Boshqa
> tizimlardan migratsiya yoki ma'lumot ko'chirish bu hujjat doirasiga
> kirmaydi. Hujjat o'zini-o'zi to'liq izohlaydi — boshqa hujjatlarga
> havolasiz o'qiladi va implementatsiya qilinadi.

---

## 1. Loyihaning maqsadi

Do'konlar mijozlarga tovarni nasiyaga (kredit/qarzga) sotadi va bu jarayonni —
qarz berish, to'lovlarni kuzatish, mijoz ishonchliligini baholash, huquqiy
rozilikni tasdiqlash va hisobotlarni yuritish — bitta web ilova orqali
boshqarishni ta'minlaydi.

Ilova ikkita asosiy foydalanuvchi guruhiga xizmat qiladi:

- **Do'kon tomoni** (egasi, menejer, kassir) — kunlik ish oqimi: mijoz qo'shish,
  qarz ochish, to'lov qabul qilish, hisobotlarni ko'rish.
- **Mijoz tomoni** — ro'yxatdan o'tish, ofertani qabul qilish, qarzni
  qabul qilish/rad etish, to'lov tarixini ko'rish, do'kon yangiliklarini olish.

Mahsulot **bitta web ilova** sifatida quriladi va asosan **mobil telefon
brauzerlarida** ishlatiladi (mobile-first). Alohida native ilova o'rnatish
talab qilinmaydi; xohlagan foydalanuvchi ilovani PWA sifatida bosh ekranga
qo'shib olishi mumkin (6.13).

Foydalanuvchi roli va huquqiga qarab ilova ichida tegishli rejimlar
ko'rinadi: mijoz rejimi, do'kon rejimi yoki ikkalasi. Bitta foydalanuvchi
bir vaqtning o'zida mijoz ham, do'kon xodimi ham bo'lishi mumkin. Backend
har doim rol va tenant scope bo'yicha tekshiradi; interfeysdagi rejim
almashinuvi xavfsizlik chegarasi hisoblanmaydi.

Platforma administratori shu web ilovaning alohida admin bo'limi orqali
ishlaydi (6.11); MVP uchun funksional minimal admin interfeysi yetarli,
alohida vosita talab qilinmaydi.

---

## 2. Arxitektura

- **Web ilova**: FastAPI + Jinja2 server-rendered HTML, mobile-first
  responsive dizayn. Interaktivlik uchun **HTMX** (fragment yangilash) va
  minimal vanilla JS. Og'ir SPA framework (React/Vue va h.k.)
  ishlatilmaydi — asosiy texnologiya Python bo'lib qoladi.
- **Biznes-qoidalar**: barchasi backend **service qatlamida**; routerlar va
  template'lar yupqa. Bu kelajakda JSON API yoki native mijoz qo'shish
  imkonini saqlab qoladi, lekin ommaviy API MVP doirasiga kirmaydi
  (12-bo'lim).
- **Ma'lumotlar bazasi**: PostgreSQL, faqat backend orqali kirish mumkin.
- **Hujjat/rasm saqlash**: S3-mos obyekt saqlash xizmati. Development/local
  muhitda MinIO ishlatiladi. Production muhitda lokal yoki regional S3-mos
  provayder tanlanadi. Backend fayl saqlash bilan faqat `ObjectStorageService`
  abstraction orqali ishlaydi. Bazada faqat havola (reference) saqlanadi,
  fayllarga to'g'ridan-to'g'ri ochiq URL berilmaydi — faqat vaqtinchalik,
  ruxsat tekshirilgan havolalar.
- **Telegram bot**: OTP yetkazish va bildirishnomalarning tashqi kanali
  (Telegram Bot API orqali). MVP'da OTP faqat shu kanal orqali; SMS — Phase 2.
  Arxitekturada `OtpDeliveryProvider` abstraction bo'ladi.
- **Bildirishnoma kanallari (MVP)**: ilova ichi (in-app) + Telegram bot.
  Native push (FCM) bu mahsulotda ishlatilmaydi; **Web Push — Phase 2**
  (6.8).
- **Autentifikatsiya**: server tomonida saqlanadigan sessiya + HttpOnly
  cookie; CSRF himoyasi majburiy (8-bo'lim). Brauzerda token saqlanmaydi.
- **PWA qatlami**: manifest + service worker faqat statik resurslarni
  (app-shell) keshlaydi. Moliyaviy amallar oflayn bajarilmaydi.
- **Scheduler**: xuddi shu kod bazasidan alohida protsess sifatida ishlaydi
  (6.10).
- **URL nomfazolari**: `/auth/*`, `/customer/*`, `/shop/*`, `/admin/*`,
  `/health`, `/static/*`.
- Moliyaviy hisob-kitoblar (balans, chegirma, clawback, limit exposure)
  faqat serverda hisoblanadi; brauzerda qayta hisoblash yo'q.

---

## 3. Umumiy texnik qoidalar

### 3.1 Pul va aniqlik

- Valyuta: faqat UZS (so'm). Ko'p valyuta MVP doirasida yo'q.
- Barcha pul hisob-kitoblari **Decimal** turida bajariladi; `float`
  ishlatish **taqiqlanadi** (baza, backend, template kontekst — hamma joyda).
- Yaxlitlash qoidasi: **ROUND_HALF_UP**.
- Yakuniy to'lanadigan summalar butun so'mgacha yaxlitlanadi (tiyin
  ishlatilmaydi).
- Chegirma, clawback va kredit exposure formulalari bitta joyda (domen
  qatlamida) yoziladi va testlar bilan qotiriladi.

### 3.2 Vaqt va biznes sana

- Bazada barcha vaqtlar **UTC** da saqlanadi (`timestamptz`).
- **Biznes sana** (to'lov muddati, "muddati o'tdi" aniqlash, hisobot
  kunlari, e'lonlar kunlik limiti, anti-farming kunlik cheklovlari)
  **Asia/Tashkent** vaqt zonasi bo'yicha hisoblanadi.
- Muddat chegarasi qoidasi: qarz to'lov muddati kuni Toshkent vaqti bilan
  23:59:59 gacha to'langan hisoblanadi; keyingi biznes kundan overdue.
- Pending qarz TTL muddati `created_at + 72 soat` bo'yicha aniq timestamp
  sifatida hisoblanadi; scheduler shu timestampdan keyin `expired` qiladi.
- UTC kalendar sanasini biznes sana sifatida ishlatish taqiqlanadi.

### 3.3 Identifikatorlar

- Tashqi ko'rinadigan barcha ID'lar (URL route parametrlari, formalar,
  fragmentlar) taxmin qilib bo'lmaydigan bo'lishi kerak (UUID tavsiya
  etiladi) — ketma-ket raqamli ID'lar orqali ob'ektlarni sanab chiqish
  (enumeration) hujumining oldini olish uchun.

### 3.4 Idempotentlik va takroriy yuborish himoyasi

- Moliyaviy yozuv yaratadigan barcha POST amallar (qarz yaratish, to'lov
  yaratish) **idempotency kaliti** talab qiladi.
- Kalit ikki ko'rinishda qabul qilinadi va server tomonida bir xil qayta
  ishlanadi:
  - oddiy HTML formada — forma render bo'lganda server yaratib beradigan
    yashirin `idempotency_key` maydoni;
  - HTMX/fetch so'rovlarida — `Idempotency-Key` sarlavhasi.
- Bir xil kalit bilan takroriy so'rov ikkinchi yozuv yaratmaydi — avvalgi
  natija qaytariladi yoki `IDEMPOTENCY_CONFLICT` xatosi beriladi.
- Idempotency kaliti actor, endpoint, request hash va natija bilan
  bog'lanadi.
- Barcha holat o'zgartiruvchi formalarda **POST → Redirect → GET (PRG)**
  naqshi majburiy — sahifani yangilash (refresh) takroriy yubormaydi.
- Bu sekin mobil tarmoqda "ikki marta bosildi" muammosining asosiy
  himoyasi.

---

## 4. Mahsulot modeli (biznes qoidalari)

### 4.1 Chegirma va clawback

- Do'kon ro'yxatdan o'tgan mijozlar uchun chegirma foizini belgilashi mumkin.
- Har bir qarzda ikkita summa saqlanadi: **asl summa** va **chegirmali summa**.
- Mijoz o'z vaqtida to'liq to'lasa — chegirma kuchda qoladi va qarz chegirmali
  summa asosida `paid` bo'ladi.
- Mijoz muddatni o'tkazib yuborsa (overdue) — **clawback** ishga tushadi:

```text
qoldiq = max(original_amount − total_non_voided_payments, 0)
```

- Bu formula qisman to'lovdan keyin ham o'zgarmaydi. Masalan:

```text
original_amount = 1 000 000
chegirma = 10%
discounted_amount = 900 000
muddatgacha to'langan = 300 000
overdue paytidagi qoldiq = 1 000 000 − 300 000 = 700 000
```

- Agar mijoz muddatgacha chegirmali summani to'liq to'lasa, qarz `paid`
  bo'ladi va clawback ishlamaydi.
- Clawback bir marta, `active → overdue` o'tish paytida qo'llanadi.
- Clawback hodisasi `debt.clawback_applied` sifatida audit qilinadi.
- Clawback natijasida qoldiq manfiy bo'lishi mumkin emas.

### 4.2 Qarz modeli — bitta to'lov muddati

- MVP'da qarz **bitta to'lov muddatiga** ega (single due date).
- Qisman to'lovlar muddatgacha istalgan vaqtda qabul qilinadi, lekin
  rasmiy bo'lib to'lash jadvali (installment/grafik) MVP doirasida **yo'q**.

### 4.3 Kredit limiti va ochiq qarzlar cheklovi

- Har bir do'kon–mijoz bog'lamida **kredit limiti** mavjud.
- Standart kredit limiti: **1 000 000 UZS**.
- Standart ochiq qarzlar soni cheklovi: **2 ta**.
- Do'kon egasi do'kon sozlamalarida bu qiymatlarni o'zgartirishi mumkin;
  yangi mijoz-link yaratilganda default qiymatlar do'kon sozlamasidan olinadi.
- Yangi qarz ochishda tekshiruv:

```text
current_exposure + new_debt_original_amount ≤ customer_shop_credit_limit
```

- `current_exposure` pending/active/overdue qarzlar bo'yicha asl summa
  asosidagi qoldiq exposure sifatida hisoblanadi:

```text
exposure = max(original_amount − total_non_voided_payments, 0)
```

- Chegirma kredit limitini sun'iy kattalashtirmaydi; limit tekshiruvida
  yangi qarz uchun `original_amount` ishlatiladi.
- Limit oshsa `CREDIT_LIMIT_EXCEEDED` qaytadi.
- Bir mijozning bitta do'konda bir vaqtda ochiq (`pending`/`active`/`overdue`)
  qarzlar soni `MAX_OPEN_DEBTS` bilan cheklanadi.
- Agar mijozda shu do'konda qoldig'i bor `overdue` qarz bo'lsa, yangi qarz
  ochilmaydi. Global hard block buni barcha do'konlar bo'yicha ham tekshiradi.

### 4.4 Har bir do'kon ichidagi oq/qora ro'yxat

- Har bir do'kon o'z mijozlari uchun oq/qora ro'yxat (whitelist/blacklist)
  yuritadi — faqat shu do'kon doirasida amal qiladi.
- Qora ro'yxatdagi mijozga shu do'konda yangi qarz ochilmaydi.

### 4.5 Global reyting va bloklash

- Mijozning global reytingi barcha do'konlar bo'yicha nasiya olish
  imkoniyatini belgilaydi. Reyting butun son, [0, 100] oralig'ida
  qisqartiriladi (clamp).
- Boshlang'ich reyting balli: **60**.
- Yetarli tarix yo'q mijoz disclosure'da `NEW` band sifatida ko'rsatiladi.
  Ichki score 60 bo'lsa ham, birinchi rating-eligible hodisagacha do'konlarga
  "tarixsiz mijoz" signali beriladi.
- Rating bandlari:

| Band | Shart |
| --- | --- |
| `NEW` | Rating-eligible tarix hali yo'q |
| `GREEN` | 75–100 |
| `YELLOW` | 50–74 |
| `RED` | 0–49 |
| `BLOCKED` | unpaid overdue yoki unresolved written_off mavjud |

- Reyting hodisalari:

| Hodisa | Ball ta'siri | Qoida |
| --- | ---: | --- |
| O'z vaqtida to'liq to'lash | +5 | Faqat rating-eligible qarz uchun |
| `active → overdue` | −15 | Bir qarz bo'yicha bir marta |
| `overdue → written_off` | −40 | Global hard block yuzaga keladi |
| `written_off → written_off_settled` | +10 | Hard block yechiladi, tarix saqlanadi |

- Ball va hard block alohida tushuncha. Mijozda yuqori ball bo'lsa ham,
  unpaid overdue yoki unresolved written_off mavjud bo'lsa, u barcha
  do'konlarda bloklanadi.
- To'lanmagan muddati o'tgan yoki unresolved `written_off` qarzlar mijozni
  **barcha** do'konlarda bloklaydi — bitta do'konda yo'q qilingan qarz
  mijozni boshqa do'konda "toza" qilib ko'rsatmaydi.
- Overdue qarz keyin to'liq to'lansa, global hard block yo'qoladi, lekin
  `−15` reyting tarixi o'chmaydi va avtomatik +5 berilmaydi.
- `pending` holatidan chiqishlar (`rejected`, `cancelled`, `expired`)
  reytingga ta'sir qilmaydi.
- `+5` berilishi uchun qarz 4.6 dagi rating-eligible shartlarga to'liq mos
  bo'lishi kerak.
- Written-off qarz keyin to'liq qoplansa, status `written_off_settled`
  bo'ladi, global hard block yechiladi, +10 tiklanish hodisasi yoziladi,
  lekin written-off tarixi disclosure logikasi va admin hisobotlarida
  saqlanadi.
- Global hard block service qatlamida yagona `EligibilityService` orqali
  hisoblanadi.

### 4.6 Farming'ga qarshi himoya

Reytingni sun'iy ko'tarishning oldini olish uchun quyidagi minimal qoidalar
MVP'da majburiy:

- Ratingga ijobiy ta'sir qiladigan minimal qarz summasi:

```text
rating_eligible_min_amount = 100 000 UZS
```

- 100 000 UZSdan kichik qarz ochish mumkin, lekin u ijobiy reyting bermaydi.
- Bir kunda ochilib-yopilgan qarz ijobiy reyting bermaydi.
- Bitta mijoz–do'kon juftligida bir biznes kunda faqat 1 ta qarz ijobiy
  reyting berishi mumkin.
- Void qilingan to'lov avval bergan reyting foydasini qaytaradi.
- Reyting eventlari idempotent bo'ladi: bir debt/event turi bo'yicha bir xil
  reyting o'zgarishi ikki marta yozilmaydi.
- Ushbu qoidalar testlar bilan qotiriladi.

### 4.7 Do'konlararo oshkoralik — faqat daraja (band)

- Bir do'kon boshqa do'konlarda tarixi bor mijoz haqida faqat tasdiqlangan
  **risk/to'lov qobiliyati darajasi**ni ko'radi (`NEW`, `GREEN`, `YELLOW`,
  `RED`, `BLOCKED`).
- Boshqa do'konning nomi, summasi, xom agregatlar yoki sonlar (nechta qarz
  borligi ham) ko'rsatilmaydi.
- Xom agregat ma'lumotlar faqat platforma admini uchun.
- Har bir band ko'rish hodisasi audit qilinadi (`disclosure.risk_band_viewed`).

### 4.8 Ommaviy oferta (legal offer)

- Ro'yxatdan o'tish va qarzni qabul qilish **joriy oferta matni**ga
  bog'lanadi. Tizim oferta hayotiy siklini qo'llab-quvvatlaydi:
  **qoralama (draft) → tasdiqlangan → joriy**.
- **Hozirgi bosqichda oferta matnlari qoralama holatida yuritiladi** —
  ishlab chiqish va sinov shu qoralama matnlar bilan olib boriladi.
  Haqiqiy mijozlar bilan production ishga tushirishdan oldin matnlar
  yuridik tekshiruvdan o'tkazilib, "tasdiqlangan" holatga o'tkazilishi
  kerak.
- Hech bir matn yuridik tasdiqsiz tizimda "tasdiqlangan" deb belgilanmaydi;
  AI/kod vositalari qoralama tayyorlashda yordam berishi mumkin, lekin
  yakuniy yuridik matn manbasi bo'la olmaydi.
- Tillar: o'zbek lotin, o'zbek kirill, rus — qoralama bosqichida ham
  uchala til varianti yuritiladi.
- **Fail-closed**: tegishli tilda joriy oferta bo'lmasa — ro'yxatdan
  o'tish va qarz qabul qilish rad etiladi (`OFFER_UNAVAILABLE`).
- Har bir qabul (acceptance) ofertaning versiyasi, tili va kontent-xeshi
  bilan bog'lab saqlanadi — keyinchalik "mijoz aynan qaysi matnni qabul
  qilgan" degan savolga javob berish uchun.

### 4.9 Audit va maxfiylik

- Barcha muhim o'zgarishlar audit jurnaliga yoziladi (append-only).
- Audit JSON ichida xom shaxsiy ma'lumotlar (JSHSHIR, pasport raqami, xom
  hujjat havolalari, rasm/selfie referenslari) **hech qachon** saqlanmaydi —
  yozishdan oldin markazlashgan **redaction** xizmatidan o'tadi.
- Barcha yon ta'sirlar (audit, bildirishnoma, reyting o'zgarishi, bloklash)
  faqat backend **service qatlamida** bajariladi; router yoki template
  bu logikani takrorlamaydi.

### 4.10 Platforma billing modeli

- MVP'da platforma pullik obuna modeli bilan boshlanmaydi.
- Subscription, invoice, tarif rejasi va avtomatik to'lov sababli suspend
  qilish MVP doirasiga kirmaydi.
- `shop.suspended` holati faqat platforma admini tomonidan qo'lda, sabab
  bilan qo'yiladi: qoidabuzarlik, firibgarlik gumoni, yuridik/compliance
  muammo yoki operatsion xavfsizlik.

---

## 5. Rollar va ruxsatlar

| Rol | Tavsif |
| --- | --- |
| Platforma admini | Butun tizim ustidan nazorat — web admin bo'limi orqali, to'liq ro'yxat 6.11-bo'limda |
| Do'kon egasi | O'z do'koni sozlamalari, xodimlar, barcha operatsiyalar |
| Menejer | Kundalik operatsiyalar, cheklangan sozlamalar |
| Kassir | Mijoz qo'shish, qarz ochish, to'lov qabul qilish |
| Mijoz | Ro'yxatdan o'tish, ofertani qabul qilish, qarz/to'lovlarini ko'rish |

Har bir so'rov actor roli va do'kon a'zoligi (tenant scope) bo'yicha
tekshiriladi. Bir do'kon xodimi boshqa do'konning ma'lumotiga (band'dan
tashqari) kira olmaydi (IDOR himoyasi). Bu tekshiruv **har bir route'da**
server tomonida bajariladi — sahifada havolaning ko'rinmasligi himoya
hisoblanmaydi.

Bitta `user` bir nechta rolda bo'lishi mumkin: masalan, do'kon kassiri va
oddiy mijoz. Har bir amal bajarilayotgan rejim/context bilan bog'lanadi.

---

## 6. Funksional talablar (modullar)

### 6.1 Autentifikatsiya, sessiya va Telegram bog'lash

- Login: telefon raqami + parol, yoki telefon + OTP.
- **OTP kanali — Telegram bot** (MVP). SMS orqali OTP Phase 2 da qo'shiladi.
- Arxitektura OTP yetkazishni kanal-mustaqil qilib quradi:

```text
OtpDeliveryProvider
- TelegramOtpProvider  # MVP
- SmsOtpProvider       # Phase 2
```

Telegram bog'lash oqimi:

1. Foydalanuvchi web sahifada telefon raqamini kiritadi.
2. Backend bir martalik, qisqa muddatli **bog'lash tokeni** yaratadi.
3. Sahifa foydalanuvchiga bot deep-link tugmasini ko'rsatadi:
   `t.me/<bot_nomi>?start=<token>`. Sessiya desktop brauzerda bo'lsa,
   xuddi shu havola **QR kod** ko'rinishida ham chiqariladi (telefondagi
   Telegram bilan skanerlash uchun).
4. Bot tokenni backend'da tekshiradi va Telegram `chat_id` ni foydalanuvchi
   akkauntiga bog'laydi. Token shu zahoti yaroqsiz bo'ladi.
5. Shundan keyin OTP kodlari va bildirishnomalar shu chatga yuboriladi.
   Web sahifa bog'lanish holatini HTMX polling orqali kuzatib, bog'langach
   avtomatik davom etadi.

Qoidalar:

- Bog'lash tokeni bir martalik va muddati cheklangan: **10 daqiqa**.
- OTP kodi muddati va urinishlar soni cheklangan (standart: 3 daqiqa,
  5 urinish — konfiguratsiya orqali o'zgaradi).
- Telegram bog'lanmagan foydalanuvchi OTP talab qiladigan amalni bajara
  olmaydi — `TELEGRAM_NOT_LINKED` xatosi va bog'lash oqimiga yo'naltirish.
- Foydalanuvchi Telegram bog'lamini uzishi va qayta bog'lashi mumkin
  (masalan, Telegram akkaunti almashganda); bu amal audit qilinadi.

Sessiya (web):

- Sessiya **server tomonida** (PostgreSQL) saqlanadi; brauzerga faqat
  sessiya identifikatori HttpOnly cookie sifatida beriladi (8-bo'lim
  cookie talablari).
- Login muvaffaqiyatli bo'lganda sessiya identifikatori **rotatsiya
  qilinadi** (session fixation himoyasi).
- Sessiyaga brauzer/qurilma metadata (User-Agent'dan olingan brauzer, OS)
  va oxirgi faollik vaqti bog'lanadi.
- Foydalanuvchi o'z sessiyalari ro'yxatini ko'radi va istalganini bekor
  qiladi (logout, boshqa brauzerni chiqarib yuborish). Bekor qilingan
  sessiya cookie'si darhol ishlamay qoladi.
- Sessiya muddati konfiguratsiya orqali; standart: 30 kun, harakatsizlikda
  tugaydi (rolling).
- Login, OTP yaratish va bog'lash so'rovlari uchun rate-limit (telefon va
  IP bo'yicha; qiymatlar konfiguratsiya).
- Parol siyosati: minimal uzunlik va murakkablik talabi (konfiguratsiya).
- Parollar zamonaviy xesh algoritmi bilan saqlanadi (argon2 yoki bcrypt).

### 6.2 Do'kon va xodimlar

- **Do'kon egasi ro'yxatdan o'tishi ikki bosqichli**: avval do'kon egasi
  ariza yuboradi — do'kon ma'lumotlari, shaxsiy ma'lumotlari va **do'kon
  egasi ekanligini tasdiqlovchi hujjat** (tadbirkorlik guvohnomasi/patent
  yoki shunga o'xshash rasmiy hujjat) bilan.
- Ariza holati "kutilmoqda" (`pending`) — do'kon va akkaunt hali faollashmaydi;
  ariza holatini arizachi ilovada ko'ra oladi.
- Platforma admini arizani hujjat bilan ko'rib chiqadi va **tasdiqlaydi yoki
  sabab bilan rad etadi**. Faqat tasdiqdan keyin do'kon va egalik akkaunti
  faollashadi; natija haqida bildirishnoma yuboriladi.
- Do'kon profili va sozlamalari: chegirma foizi, standart to'lov muddati,
  standart kredit limiti, ochiq qarzlar soni cheklovi, e'lon limiti.
- Standart sozlamalar:

```text
default_credit_limit = 1 000 000 UZS
default_max_open_debts = 2
shop_news_daily_limit = 2
```

- Xodim qo'shish, rolini o'zgartirish, ishdan bo'shatish (deaktivatsiya).
- Oxirgi egani rolsiz qoldirib bo'lmaydi (`LAST_OWNER` himoyasi).

### 6.3 Mijozlar

- Mijoz ro'yxatdan o'tishi ikki yo'l bilan: **do'kon orqali** (kassir/menejer
  mijoz oldida ro'yxatdan o'tkazadi) yoki **mijozning o'zi** web ilova orqali.
- To'liq ro'yxatdan o'tish tarkibi: telefon + Telegram bog'lash + OTP tasdiq
  (6.1), F.I.Sh., JSHSHIR, hujjat (pasport/ID) foto — obyekt saqlashga
  yuklanadi, bazada faqat metadata va havola.
- Hujjat foto **brauzer orqali** yuklanadi: mobil brauzerda
  `<input type="file" accept="image/*" capture="environment">` kamerani
  ochadi. Qabul qilinadigan formatlar: JPEG, PNG, WebP; maksimal hajm
  10 MB (konfiguratsiya). Server MIME turini kontent bo'yicha tekshiradi
  va saqlashdan oldin EXIF metadata (shu jumladan GPS) ni olib tashlaydi.
- Ro'yxatdan o'tish yakuni oferta qabul qilish bilan bog'liq — joriy oferta
  bo'lmasa fail-closed (4.8).
- Telegram'i yo'q mijoz MVP'da **active customer** bo'la olmaydi va qarzni
  qabul qila olmaydi.
- Do'kon Telegram'i yo'q odamni faqat **customer draft/lead** sifatida
  kiritishi mumkin:

```text
customer_lead = minimal yozuv: telefon, ism/familiya ixtiyoriy, do'kon contexti
```

- Customer lead uchun cheklovlar:

```text
- active customer emas;
- unga pending/active qarz yaratilmaydi;
- oferta acceptance yozilmaydi;
- OTP talab qiladigan amal bajarilmaydi;
- JSHSHIR va hujjat rasmi to'liq ro'yxatdan o'tishdan oldin saqlanmaydi;
- Telegram bog'langach va oferta qabul qilingach active customerga aylanadi.
```

- Lead keyinchalik to'liq ro'yxatdan o'tsa (Telegram bog'lash + oferta
  qabul), u active customerga aylanadi va uni kiritgan do'kon bilan
  `shop_customer` bog'lami avtomatik o'rnatiladi; `DUPLICATE_JSHSHIR`
  tekshiruvi shu to'liq ro'yxatdan o'tish paytida bajariladi.
- Bir xil JSHSHIR bilan takroriy ro'yxatdan o'tish bloklanadi
  (`DUPLICATE_JSHSHIR`); mavjud mijoz yangi do'konga faqat **bog'lanadi**
  (link), qayta yaratilmaydi.
- Mijoz profili: mijozning o'zi cheklangan maydonlarni tahrirlaydi; do'konga
  ko'rsatiladigan versiya xavfsiz (PII kamaytirilgan) ko'rinish — do'kon
  sahifalarida xom JSHSHIR/pasport chiqarilmaydi.
- PII maydonlari (JSHSHIR, pasport) bazada shifrlangan holda saqlanadi.

### 6.4 Qarzlar (debt lifecycle)

Qarz holat mashinasi:

| Joriy holat | Hodisa (trigger) | Yangi holat | Izoh |
| --- | --- | --- | --- |
| — | Do'kon xodimi qarz yaratadi | `pending` | Barcha tekshiruvlar o'tsa (4.3–4.8) |
| `pending` | Mijoz qabul qiladi | `active` | Joriy oferta gate'i o'tishi shart |
| `pending` | Mijoz rad etadi | `rejected` | Ixtiyoriy sabab |
| `pending` | Do'kon/admin bekor qiladi | `cancelled` | Sabab majburiy |
| `pending` | 72 soatlik qabul muddati tugadi | `expired` | Scheduler orqali |
| `active` | Qoldiq 0 ga tushdi | `paid` | Chegirmali summa asosida |
| `active` | To'lov muddati o'tdi | `overdue` | Clawback shu yerda qo'llanadi |
| `overdue` | Qoldiq 0 ga tushdi | `paid` | Asl summa asosida; hard block yechiladi |
| `overdue` | Vakolatli yo'q qilish | `written_off` | Sabab + vakolat; −40 rating; hard block |
| `written_off` | Qisman to'lov | `written_off` | Qoldiq kamayadi, hard block qoladi |
| `written_off` | Qoldiq 0 ga tushdi | `written_off_settled` | +10 rating; hard block yechiladi; tarix saqlanadi |
| `paid` | To'lov void qilindi, qoldiq > 0 | `active` yoki `overdue` | Muddatga qarab qaytadi |
| `written_off_settled` | To'lov void qilindi, qoldiq > 0 | `written_off` | Hard block qayta paydo bo'ladi; `+10` balli qaytarib olinadi |

- Pending qabul qilish muddati: **72 soat**.
- Har bir o'tish audit qilinadi va tegishli bildirishnoma yaratadi.
- Qarz yaratishda tekshiruvlar: active customer, qora ro'yxat, global
  blok/reyting, kredit limiti, ochiq qarzlar soni, joriy oferta mavjudligi.
- Customer leadga qarz yaratilmaydi (`CUSTOMER_NOT_ACTIVE`).
- Idempotency kaliti majburiy (3.4).
- Mijoz qabul qilishida "rozilik isboti" (acceptance) yoziladi: oferta
  versiyasi/tili/xeshi, vaqt, brauzer metadata (User-Agent) — auditga
  PII'siz.

### 6.5 To'lovlar

- To'liq va qisman to'lov qabul qilinadi.
- To'lov qabul qilinadigan qarz holatlari:

```text
PAYABLE_DEBT_STATUSES = active, overdue, written_off
```

- `written_off` qarzga to'lov qabul qilishdan maqsad — qarzni keyinchalik
  qoplash va `written_off_settled` holatiga o'tkazish.
- To'lov usullari (qayd sifatida): naqd, karta (do'konning tashqi terminali
  orqali, tizimga qo'lda qayd etiladi), o'tkazma, boshqa. To'g'ridan-to'g'ri
  bank integratsiyasi yo'q.
- Balans hisob-kitobi faqat serverda; to'lovdan keyin sahifada qoldiq va
  kvitansiya (receipt) xulosasi ko'rsatiladi.
- Idempotency kaliti majburiy (3.4); PRG naqshi majburiy.
- To'lovni bekor qilish (void): sabab majburiy, faqat vakolatli rol;
  qoldiq qayta hisoblanadi, kerak bo'lsa qarz holati orqaga qaytadi va
  to'lov bergan reyting balli qaytarib olinadi. Agar void
  `written_off_settled` holatini buzsa, qarz `written_off` ga qaytadi va
  `+10` tiklanish balli ham qaytarib olinadi (6.4). Void alohida audit va
  bildirishnoma yaratadi.

### 6.6 Reyting va bloklash

- Reyting hodisalarini yozish (event history): har bir o'zgarish sabab va
  manba bilan saqlanadi.
- Rating konfiguratsiyasi MVP default qiymatlari:

```text
rating.initial_score = 60
rating.on_time_paid_delta = +5
rating.overdue_delta = -15
rating.written_off_delta = -40
rating.written_off_settled_delta = +10
rating_eligible_min_amount = 100 000 UZS
```

- Band konfiguratsiyasi:

```text
NEW = no rating-eligible history
GREEN = 75..100
YELLOW = 50..74
RED = 0..49
BLOCKED = unpaid overdue yoki unresolved written_off
```

- Global qattiq bloklash holati service qatlamida yagona joyda hisoblanadi
  (`EligibilityService`) — qarz yaratish, ro'yxat va disclosure shu
  xizmatdan foydalanadi.
- Farming'ga qarshi qoidalar (4.6) testlar bilan qotiriladi.
- Rating override faqat platforma admini tomonidan, sabab bilan, auditlangan
  holda bajariladi.

### 6.7 Do'konlararo oshkoralik

- Band (daraja) so'rovi alohida sahifa/fragment orqali bajariladi; so'rovda
  ish maqsadi (workflow purpose) tanlovi majburiy.
- Javob sahifasida/fragmentida faqat band ko'rsatiladi; taqiqlangan
  maydonlar (boshqa do'kon nomi, summalar, qarzlar soni) yo'qligi testlar
  bilan tasdiqlanadi.
- Har bir ko'rish `disclosure_view_log` ga yoziladi.

### 6.8 Bildirishnomalar (kanallar va hodisalar)

Kanallar (MVP):

1. **Ilova ichi (in-app)** — bazada saqlanadi, ilovada ro'yxat sifatida
   ko'rinadi; sarlavhada o'qilmaganlar hisoblagichi (badge) turadi va
   HTMX polling (standart: 60 soniya) orqali yangilanadi. WebSocket
   MVP'da ishlatilmaydi.
2. **Telegram bot xabari** — foydalanuvchining bog'langan `chat_id`siga
   (6.1). Bog'lanmagan bo'lsa bu kanal o'tkazib yuboriladi, in-app
   baribir ishlaydi. Telegram xabaridagi havola ilovaning tegishli
   sahifasiga olib boradi.

**Web Push (VAPID) — Phase 2** (iOS Safari cheklovlari va o'rnatilgan PWA
talabi tufayli MVP'ga kirmaydi). SMS bildirishnomalar — Phase 2.

Qoidalar:

- Bildirishnoma bitta yozuv sifatida yaratiladi va mavjud kanallarning
  barchasiga yetkaziladi; **har kanal bo'yicha yetkazish holati alohida
  kuzatiladi** (yuborildi/xato/o'qildi).
- Telegram xabarlarida xom PII (to'liq JSHSHIR, pasport) yuborilmaydi —
  faqat xavfsiz xulosa (masalan, "Sizga X do'konidan yangi nasiya taklifi
  keldi. Ilovada ko'ring").
- Yetkazilmagan xabarlar uchun qayta urinish siyosati va admin monitoring.
- Qarz/to'lov/legal/overdue bildirishnomalari marketing/e'lon limitlariga
  kirmaydi.

Hodisalar:

- Mijozga: yangi pending qarz, to'lov qabul qilindi, to'lov muddati
  yaqinlashmoqda (X kun oldin, konfiguratsiya), muddat o'tdi, qarz yo'q
  qilindi, written-off qarz qoplandi, **a'zo bo'lgan do'kon yangiligi/e'loni**
  (6.12), parol/telefon admin tomonidan o'zgartirildi.
- Do'konga: mijoz qarzni qabul qildi/rad etdi, pending qarz muddati tugadi,
  mijoz muddatni o'tkazdi, written-off qarz qoplandi, egalik arizasi
  tasdiqlandi/rad etildi.

### 6.9 Hisobotlar

- Do'kon darajasida: davr bo'yicha berilgan qarzlar, qabul qilingan
  to'lovlar, faol/overdue qoldiqlar, written_off yo'qotishlar,
  written_off_settled qoplangan qarzlar.
- `written_off` summalar hisobotlarda **yo'qotish (loss exposure)**
  sifatida ko'rinadi, yashirilmaydi.
- `written_off_settled` holatlari alohida ko'rinadi: oldin yo'qotishga
  chiqarilgan, keyin qoplangan qarz sifatida.
- Hisobot sahifalari mobil ekranga mos (6.13); davr filtri Toshkent biznes
  sanasi bo'yicha ishlaydi.
- Eksport: CSV va XLSX fayl yuklab olish; har bir eksport audit qilinadi.
- Platforma darajasidagi agregat — faqat admin.

### 6.10 Rejalashtiruvchi (scheduler)

Job'lar ro'yxati:

1. **Overdue aniqlash + clawback** — har biznes kun boshida (Toshkent
   vaqti), muddati o'tgan `active` qarzlarni `overdue` ga o'tkazadi va
   clawback qo'llaydi.
2. **To'lov eslatmalari** — muddatga X kun qolganda bildirishnoma.
3. **Pending muddati tugashi** — 72 soat ichida qabul qilinmagan pending
   qarzlarni `expired` ga o'tkazadi. Bu job kamida har soatda ishga tushishi
   mumkin.
4. **Bildirishnoma qayta urinish** — yetkazilmagan Telegram xabarlarini
   qayta yuborish.

Talablar: scheduler web protsessdan **alohida protsess** sifatida ishlaydi
(xuddi shu kod bazasi; APScheduler yoki cron orqali boshqariladigan
komanda — implementatsiya erkin, talablar quyidagicha qat'iy). Har bir job
**idempotent** (ikki marta ishga tushsa ikki marta ta'sir qilmaydi), har bir
ishga tushish `job_run` jurnaliga yoziladi (boshlanish/tugash, natija,
xatolar), muvaffaqiyatsiz ish qayta urinish siyosatiga ega, admin holatni
ko'ra oladi va (ruxsat bilan) qo'lda/dry-run ishga tushira oladi.

### 6.11 Platforma admini funksiyalari

Admin funksiyalari web ilovaning `/admin` bo'limi orqali bajariladi.
MVP uchun funksional minimal interfeys yetarli — dizayn jilosi talab
qilinmaydi, lekin barcha amallar quyidagi ro'yxat bo'yicha ishlashi shart:

1. **Boshqa admin yaratish** — mavjud admin yangi admin akkaunti ocha oladi.
   Oxirgi qolgan adminni o'chirib yoki huquqidan mahrum qilib bo'lmaydi.
2. **Do'kon egasini tasdiqlash** — 6.2-bo'limdagi ariza + tasdiqlovchi
   hujjat oqimi orqali; admin tasdiqlaydi yoki sabab bilan rad etadi.
3. **Global oq/qora ro'yxatni ko'rish** — barcha do'konlar bo'yicha (har
   bir do'kon faqat o'zinikini ko'radi, admin hammasini ko'radi).
4. **Mijozlar profilini to'liq ko'rish** — PII bilan; har bir bunday
   ko'rish audit qilinadi.
5. **Do'kon egalari profilini ko'rish** — tasdiqlangan hujjat ma'lumotlari
   bilan.
6. **Do'kon egasining telefon raqami va parolini o'zgartirib berish** —
   egasi so'rovi asosida (telefon yo'qolgan/almashgan holat); amal audit
   qilinadi va egasiga bildirishnoma yuboriladi.
7. **Do'konni vaqtincha to'xtatish/bloklash (suspend)** — qoidabuzarlik,
   firibgarlik gumoni yoki yuridik/compliance muammo bo'lganda, sabab bilan.
   MVP'da billing sababli avtomatik suspend yo'q.
8. **Oferta matnlarini boshqarish** — qoralama qo'shish, tasdiqlash,
   joriy qilib belgilash (4.8 ga mos).
9. **Written-off amalini bajarish/tasdiqlash** — yuqori vakolat, sabab
   majburiy.
10. **Reyting override** — sabab bilan qo'lda tuzatish.
11. **Platforma darajasidagi hisobotlar** — barcha do'konlar agregati.
12. **Audit jurnalini ko'rish** — redaksiya qilingan shaklda; kirishning
    o'zi ham qayd etiladi.
13. **Tizim sozlamalarini boshqarish** — reyting konstantalari, farming
    porog'lari, pending muddati, e'lon limiti kabi global konfiguratsiyalar.
14. **Bildirishnoma va job monitoring** — muvaffaqiyatsizlarni qayta yuborish,
    scheduler holatini kuzatish.
15. **Impersonation YO'Q** — admin boshqa foydalanuvchi nomidan tizimga
    kirmaydi; yordam faqat aniq, auditlangan amallar orqali.
16. **Qoidabuzar do'kon e'lonini olib tashlash** — 6.12 dagi e'lonlar
    ustidan nazorat.

### 6.12 Do'kon yangiliklari (e'lonlar)

- Do'kon egasi yoki menejeri do'kon nomidan **yangilik/e'lon** yaratadi:
  sarlavha, matn, ixtiyoriy rasm (6.3 dagi yuklash qoidalari bilan).
- E'lon shu do'konga bog'langan barcha mijozlarga **bildirishnoma sifatida**
  yuboriladi — in-app + Telegram (6.8 kanallari orqali).
- **Spamga qarshi cheklov**: bir do'kon kuniga ko'pi bilan **2 ta** e'lon
  yuborishi mumkin. Limit Asia/Tashkent biznes sanasi bo'yicha hisoblanadi.
  Limitdan oshsa — `NEWS_LIMIT_REACHED`.
- Limit faqat marketing/e'lon xabarlariga tegishli. Qarz, to'lov, overdue,
  legal va xavfsizlik bildirishnomalari bu limitga kirmaydi.
- Mijoz interfeysida do'kon sahifasida yangiliklar ro'yxati ko'rinadi
  (bildirishnomani o'tkazib yuborgan mijoz keyin ham o'qiy oladi).
- Mijoz muayyan do'kon e'lonlarini o'chirib qo'yishi (mute) mumkin — qarz
  bilan bog'liq bildirishnomalar bunga kirmaydi, ular har doim yetkaziladi.
- E'lon yaratish audit qilinadi (`shop_news.created`); admin qoidabuzar
  e'lonni olib tashlaydi yoki do'konni suspend qiladi (6.11).

### 6.13 Web interfeys talablari (mobile-first)

Interfeys birinchi navbatda telefon brauzeri uchun loyihalanadi, keyin
kengroq ekranlarga moslashadi:

- **Asosiy dizayn diapazoni**: 320–430 px kenglik; planshet/desktop uchun
  responsive kengayish (kontent markazda, maksimal kenglik chegarasi).
- **Touch talablari**: bosiladigan elementlar kamida 44×44 px; asosiy
  amallar bosh barmoq zonasida; telefonda asosiy navigatsiya — pastki
  panel (bottom nav); rejim almashtirgich (mijoz/do'kon) doim aniq
  ko'rinadigan joyda.
- **Render modeli**: sahifalar server tomonida Jinja2 bilan render
  qilinadi. Ro'yxat sahifalash, forma yuborish natijalari, o'qilmaganlar
  hisoblagichi kabi dinamik qismlar HTMX fragmentlari orqali yangilanadi.
  Har bir HTMX so'rovda loading indikator ko'rsatiladi; tugma yuborish
  vaqtida bloklanadi (double-click himoyasining UI qatlami; asl himoya —
  3.4 idempotentlik).
- **PRG va xabarlar**: barcha yozuv formalari POST → Redirect → GET bilan
  ishlaydi; muvaffaqiyat/xato flash xabar sifatida ko'rsatiladi; forma
  xatolari tegishli maydon yonida chiqadi.
- **Sahifa og'irligi byudjeti**: birinchi ochilish (app-shell, CSS, JS)
  gzip holda ≤ 300 KB; jami JS ≤ 100 KB (HTMX + minimal skriptlar);
  rasmlar lazy-load; keraksiz font/ikonka to'plamlari yuklanmaydi.
- **PWA**: web app manifest (nom, ikonka, standalone rejim) — foydalanuvchi
  ilovani bosh ekranga qo'sha oladi. Service worker faqat statik
  resurslarni keshlaydi (app-shell); ma'lumot sahifalari uchun
  network-first. Tarmoq bo'lmasa "oflayn" sahifasi ko'rsatiladi;
  **moliyaviy amallar oflayn bajarilmaydi** — tarmoq yo'q holatda amal
  rad etiladi, takroriy urinish idempotency kaliti bilan xavfsiz.
- **Brauzer qo'llab-quvvatlash**: Chrome (Android) va Safari (iOS) so'nggi
  2 major versiya; Firefox mobil/desktop. **Telegram in-app brauzerida**
  asosiy oqimlar (login, qarz qabul qilish, to'lov ko'rish) ishlashi
  majburiy — bildirishnoma havolalari aynan shu brauzerda ochiladi.
- **Accessibility (minimal)**: barcha input'larda label; yetarli kontrast;
  klaviatura focus holati ko'rinadi; xato xabarlari matn sifatida (faqat
  rang bilan emas).
- **Til almashtirgich** (o'zbek lotin / rus) har sahifadan topiladi (9-bo'lim).
- Og'ir SPA framework, WebSocket va murakkab client-side state MVP'da
  ishlatilmaydi.

---

## 7. Ma'lumotlar modeli — asosiy obyektlar

Quyidagi ro'yxat sxema emas, mo'ljal — aniq sxema loyihalash bosqichida
tasdiqlanadi:

| Obyekt | Vazifasi |
| --- | --- |
| `user` | Auth hisobi (telefon, parol xeshi, rol/mode imkoniyatlari, til) |
| `session` | Server-side sessiya: cookie ID xeshi, CSRF sekreti, brauzer metadata, muddati |
| `telegram_link` | Foydalanuvchi ↔ Telegram `chat_id` bog'lami |
| `otp_challenge` | OTP yaratish, kanal, muddati, urinishlar va holat |
| `shop` | Do'kon profili va sozlamalari |
| `shop_staff` | Do'kon–xodim a'zoligi va roli |
| `owner_application` | Do'kon egasi arizasi (hujjat, holat, admin qarori) |
| `customer_lead` | Telegram'i yo'q / hali active bo'lmagan minimal lead yozuvi |
| `customer` | Mijoz profili (PII shifrlangan) |
| `customer_document` | Hujjat metadata + saqlash havolasi |
| `shop_customer` | Do'kon–mijoz bog'lami: limit, oq/qora holat, mute |
| `debt` | Qarz: asl/chegirmali summa, muddat, holat, exposure maydonlari |
| `payment` | To'lov: summa, usul, void maydonlari |
| `offer` | Oferta versiyalari (til, xesh, holat: qoralama/tasdiqlangan/joriy) |
| `acceptance` | Rozilik isboti (oferta versiyasi/xeshi, vaqt, brauzer metadata) |
| `rating_event` | Reyting o'zgarishlari tarixi |
| `shop_news` | Do'kon e'lonlari (sarlavha, matn, rasm havolasi, holat) |
| `notification` | Bildirishnomalar va har kanal (in-app/Telegram) bo'yicha yetkazish holati |
| `idempotency_key` | Idempotency kalitlari: actor, endpoint, request hash, natija |
| `audit_log` | Append-only audit (redaksiyadan o'tgan JSON) |
| `disclosure_view_log` | Band ko'rish hodisalari |
| `job_run` | Scheduler ishga tushishlari jurnali |
| `object_file` | Fayl metadata: bucket/key, content type, size, checksum, owner scope |
| `system_setting` | Global konfiguratsiyalar: rating, TTL, limitlar, e'lon limiti |

---

## 8. Xavfsizlik va maxfiylik talablari

Transport va sessiya:

- Barcha trafik TLS (HTTPS); **HSTS** sarlavhasi yoqilgan.
- Sessiya cookie'si: **HttpOnly, Secure, SameSite=Lax**. Sessiya server
  tomonida saqlanadi va istalgan payt bekor qilinadi (revocation).
- Login paytida sessiya ID rotatsiyasi (session fixation himoyasi);
  logout server tomonida sessiyani o'chiradi.
- Brauzerda hech qanday token yoki PII saqlanmaydi: `localStorage` /
  `sessionStorage` da auth ma'lumoti taqiqlanadi — hammasi HttpOnly
  cookie orqali.

CSRF:

- Barcha holat o'zgartiruvchi so'rovlar (POST/PUT/DELETE) **CSRF token**
  bilan himoyalanadi: oddiy formalarda yashirin maydon, HTMX so'rovlarida
  `X-CSRF-Token` sarlavhasi.
- Token sessiyaga bog'langan; tekshiruvdan o'tmagan so'rov `CSRF_FAILED`
  bilan rad etiladi.
- `SameSite=Lax` cookie qo'shimcha himoya qatlami sifatida xizmat qiladi,
  lekin CSRF tokenni almashtirmaydi.

Kontent xavfsizligi:

- Jinja2 **autoescape majburiy yoqilgan**; `|safe` filtri faqat
  sanitizatsiyadan o'tgan kontent uchun, alohida asoslash bilan.
- **CSP** sarlavhasi: `default-src 'self'`; `script-src 'self'` (inline
  skript ishlatilmaydi); `img-src 'self' data:` + presigned havolalar
  beriladigan object storage domeni.
- Qo'shimcha sarlavhalar: `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`.
- PII ko'rsatiladigan sahifalar `Cache-Control: no-store` bilan qaytadi.

Backend qoidalari:

- Har bir so'rov faqat FastAPI orqali; to'g'ridan-to'g'ri baza kirishi yo'q.
- Do'konga ko'rinadigan sahifalarda/fragmentlarda boshqa do'konning xom
  ma'lumoti yo'q; tenant tekshiruvi har route'da (5-bo'lim).
- Audit yozuvlarida xom PII, hujjat URL'lari, rasm referenslari yo'q —
  markazlashgan redaction majburiy.
- PII ustunlari (JSHSHIR, pasport) bazada shifrlangan.
- Hujjat fayllariga faqat vaqtinchalik, ruxsat tekshirilgan presigned
  havolalar (standart TTL: 5 daqiqa, konfiguratsiya). Bucketlar private;
  public-read taqiqlanadi.
- Fayl yuklash: server MIME turini kontent bo'yicha aniqlaydi (kengaytmaga
  ishonilmaydi), ruxsat etilgan turlar JPEG/PNG/WebP, maksimal hajm 10 MB;
  EXIF metadata saqlashdan oldin olib tashlanadi. Buzilgan tekshiruv —
  `UNSUPPORTED_FILE_TYPE` yoki `FILE_TOO_LARGE`.
- Login, OTP, bog'lash va fayl yuklash uchun rate-limit.
- Maxfiy kalitlar (DB parol, sessiya/CSRF sekretlari, **Telegram bot
  tokeni**, object storage access key/secret key) faqat environment/secret
  manager orqali; kodda va repoda saqlanmaydi.
- Telegram bog'lash tokenlari bir martalik va qisqa muddatli; ishlatilgan
  token qayta ishlamaydi.
- Telegram xabarlari mazmunida xom PII yo'q (6.8).
- Parollar argon2 yoki bcrypt bilan xeshlanadi.
- Xatoliklar barqaror ichki kod bilan ifodalanadi (14-bo'lim): HTML
  sahifada lokalizatsiya qilingan xavfsiz xabar (flash yoki maydon yonida),
  HTMX fragment javobida ham xabar + barqaror kod. Stack trace yoki ichki
  tafsilotlar foydalanuvchiga chiqmaydi.

---

## 9. Lokalizatsiya

- Ilova interfeysi: **o'zbek lotin** (asosiy) va **rus**. O'zbek kirill UI
  — ixtiyoriy/keyingi bosqich.
- Oferta matni: uch tilda majburiy (4.8) — o'zbek lotin, o'zbek kirill, rus.
- **Telegram bot xabarlari** ham foydalanuvchi tiliga mos (o'zbek lotin /
  rus).
- Xatolik xabarlari foydalanuvchi tiliga mos; ichki kod har doim barqaror
  ingliz kodi (14-bo'lim).
- Sana/summa formatlari lokalga mos (masalan, 1 250 000 so'm).
- Til tanlovi foydalanuvchi profilida saqlanadi; sessiyasiz sahifalarda
  (login, ro'yxat) til almashtirgich cookie orqali ishlaydi.

---

## 10. Nofunksional talablar

- **Ishlash**: oddiy o'qish so'rovlari uchun p95 < 500 ms (server tomonida);
  ro'yxatlar paginatsiya bilan (standart sahifa 20–50 yozuv).
- **Sekin tarmoq**: app-shell keshlangandan keyin asosiy sahifalar o'rta
  darajali telefon va sekin mobil tarmoqda qulay ochilishi kerak (6.13
  og'irlik byudjeti shu maqsadga xizmat qiladi).
- **Barqarorlik**: web protsesslar stateless — sessiya bazada saqlanadi,
  shuning uchun gorizontal masshtablash mumkin; `/health` endpoint mavjud.
- **Zaxira**: kunlik avtomatik baza backup; tiklash (restore) tartibi
  hujjatlashtirilgan va kamida bir marta sinovdan o'tkazilgan.
- **Fayl saqlash zaxirasi**: object storage bucket siyosati, lifecycle,
  retention va backup/recovery tartibi alohida hujjatlashtiriladi.
- **Loglar**: strukturali (JSON), har so'rovda korrelyatsiya ID; loglar
  ichida xom PII yo'q.
- **Monitoring**: xatolik darajasi, job holati, Telegram yetkazish holati
  kuzatiladi.
- **Muhitlar**: kamida dev va production; migratsiyalar faqat Alembic orqali,
  qo'lda SQL o'zgartirish taqiqlanadi.
- **Telegram ishdan chiqsa**: bildirishnoma yetkazilmasa tizimning asosiy
  oqimlari (qarz, to'lov) to'xtamaydi — yetkazish holati `failed` bo'lib
  qayta urinishga tushadi; OTP yetkazilmasa foydalanuvchiga tushunarli
  xato ko'rsatiladi va qayta urinish taklif qilinadi.
- **Statik resurslar**: versiyalangan (cache-busting) holda uzoq muddatli
  kesh bilan beriladi.

---

## 11. Test talablari

Backend va domen (pytest, haqiqiy PostgreSQL bilan):

- Qarz holat mashinasining barcha o'tishlari va taqiqlangan o'tishlar,
  shu jumladan `written_off → written_off_settled` va voiddan keyingi qaytish.
- Pul matematikasi: chegirma, clawback, qisman to'lov, void — Decimal va
  ROUND_HALF_UP bilan chekka holatlar.
- Kredit exposure: limit tekshiruvi `original_amount` asosida ishlashi,
  chegirma limitni oshirmasligi.
- IDOR: bir do'kon xodimi boshqa do'kon resurslariga (route darajasida)
  kira olmasligi — 403/404 qaytishi.
- PII redaction: xom JSHSHIR/pasport/hujjat havolasi audit JSON'ga yetib
  bormasligi (test yozilgan bo'lishi shart).
- Idempotentlik: bir xil kalit (forma maydoni yoki sarlavha) bilan takroriy
  qarz/to'lov ikkinchi yozuv yaratmasligi.
- Biznes sana chegaralari: Toshkent vaqti bilan muddat kuni/keyingi kun
  o'tishlari, e'lon limiti va anti-farming kunlik cheklovlari.
- Fail-closed: joriy oferta yo'qligida ro'yxat/qabul rad etilishi.
- Telegram bog'lash: bir martalik token qayta ishlamasligi, muddati o'tgan
  token rad etilishi, bog'lanmagan foydalanuvchiga OTP yuborilmasligi
  (`TELEGRAM_NOT_LINKED`).
- Telegram'i yo'q customer lead: active customerga aylanmaguncha qarz
  yaratilmasligi (`CUSTOMER_NOT_ACTIVE`).
- Reyting: initial 60, NEW/GREEN/YELLOW/RED/BLOCKED bandlari, +5/−15/−40/+10
  eventlari, clamp [0,100], hard block alohida ishlashi.
- Farming'ga qarshi qoidalar: `rating_eligible_min_amount = 100 000 UZS`,
  same-day open-close ijobiy ball bermasligi, bir kunda bir customer-shop
  uchun faqat bitta ijobiy event.
- Written-off settlement: to'liq qoplanganda hard block yechilishi, +10
  rating event yozilishi, tarix o'chmasligi.
- Settlement to'lovi void qilinganda qarz `written_off` ga qaytishi, hard
  block qayta paydo bo'lishi va +10 qaytarib olinishi.
- E'lonlar: kunlik limit 2 (`NEWS_LIMIT_REACHED`), faqat bog'langan mijozlarga
  yetkazilishi, mute ishlashi.
- Object storage: fayl yuklashda public URL qaytmasligi, presigned URL faqat
  ruxsat tekshirilgandan keyin yaratilishi.
- Fayl yuklash validatsiyasi: noto'g'ri MIME (kengaytmasi to'g'ri bo'lsa
  ham) rad etilishi, hajm limiti, EXIF tozalanishi.
- Migratsiyalar: bo'sh bazada upgrade/downgrade ishlashi.
- Disclosure: band javobida taqiqlangan maydonlar yo'qligi.

Web qatlami:

- CSRF: tokensiz yoki noto'g'ri token bilan holat o'zgartiruvchi POST
  `CSRF_FAILED` bilan rad etilishi.
- Cookie flaglari: sessiya cookie'sida HttpOnly/Secure/SameSite mavjudligi;
  login'da sessiya ID rotatsiyasi.
- Sessiya bekor qilinganda eski cookie ishlamasligi.
- PRG: barcha yozuv POST'lari redirect qaytarishi.
- Auth talab qilinadigan sahifalar login'siz ochilmasligi (redirect).
- Asosiy sahifalarning smoke-render testlari (login qilingan sessiya bilan
  sahifa 200 qaytarishi va asosiy elementlar mavjudligi) va HTMX
  fragmentlari to'g'ri fragment qaytarishi.
- E2E (masalan, Playwright) bitta smoke oqim: login → mijoz → qarz →
  to'lov → hisobot — **tavsiya etiladi**, MVP uchun majburiy emas.

Umumiy: barcha testlar CI'da avtomatik ishga tushadi; test o'tmagan kod
asosiy branchga qo'shilmaydi.

---

## 12. MVP doirasiga KIRMAYDIGAN narsalar

- Oylik foiz, imtiyoz muddati (grace period), jarima rejimi, sodiqlik
  (loyalty) dasturi — faol funksionallik sifatida qo'shilmaydi.
- Bo'lib to'lash jadvali (installment/grafik).
- **SMS orqali OTP va bildirishnomalar** — Phase 2 (MVP Telegram + in-app
  bilan boshlanadi; lekin provider abstraction MVP'da tayyor bo'ladi).
- **Web Push bildirishnomalar (VAPID)** — Phase 2.
- **Native mobil ilovalar (Android/iOS)** — bu mahsulot faqat web.
- **Ommaviy/tashqi JSON API** — biznes-logika service qatlamida ajratilgan
  bo'lgani uchun keyinchalik qo'shish mumkin, lekin MVP doirasida emas.
- WebSocket / real-time kanal (in-app hisoblagich HTMX polling bilan).
- To'g'ridan-to'g'ri bank/to'lov tizimi integratsiyasi (alohida
  tasdiqlanmasa).
- Murakkab offline moliyaviy operatsiyalar (PWA offline faqat statik shell).
- AI asosida skoring.
- Ko'p valyuta.
- Pullik obuna/billing/subscription va avtomatik billing suspend.
- Boy dizaynli admin interfeysi (funksional minimal `/admin` yetarli).

---

## 13. Qabul mezonlari (Definition of Done — MVP)

- Har bir rol o'z ruxsat doirasida ishlaydi; IDOR testlari o'tgan.
- Bitta web ilova ichida mijoz/do'kon rejimlari ishlaydi; bitta user bir
  nechta rolga ega bo'lishi mumkin.
- Joriy oferta yo'q holatda ro'yxat/qabul ishlamasligi (fail-closed)
  testlangan (qoralama matn bilan bo'lsa ham gate mexanizmi ishlashi).
- Audit JSON'da xom PII yo'qligi testlar bilan tasdiqlangan.
- Written_off qarz mijozni barcha do'konlarda bloklashi tasdiqlangan.
- Written_off qarz keyin to'liq qoplanganda `written_off_settled` bo'lishi,
  hard block yechilishi, +10 rating yozilishi va tarix saqlanishi testlangan.
- Do'konlararo oshkoralik faqat band ekani tasdiqlangan.
- Idempotentlik: takroriy so'rov (forma qayta yuborish, sahifa yangilash,
  ikki marta bosish) ikkinchi moliyaviy yozuv yaratmasligi testlangan; PRG
  barcha yozuv formalariga qo'llangan.
- Qarz holat mashinasi testlari to'liq (ruxsat etilgan va taqiqlangan
  o'tishlar).
- Reyting konstantalari, bandlari va anti-farming qoidalari testlangan.
- Pending qarz 72 soatda `expired` bo'lishi testlangan.
- Default kredit limiti 1 000 000 UZS va default max open debts 2 ekanligi,
  limit exposure `original_amount` asosida hisoblanishi testlangan.
- Telegram bog'lash + OTP oqimi ishlaydi va testlangan, shu jumladan
  bog'lanmagan foydalanuvchi holati.
- Telegram'i yo'q mijoz faqat customer lead bo'lishi, qarz qabul qila
  olmasligi testlangan.
- Do'kon e'loni → mijozlarga in-app + Telegram orqali yetkazish oqimi
  testlangan (kunlik limit 2 bilan).
- S3-mos object storage abstraction ishlaydi; dev/local MinIO bilan smoke
  test bor; public URL qaytarilmasligi testlangan.
- CSRF himoyasi barcha holat o'zgartiruvchi so'rovlarda ishlashi testlangan;
  sessiya cookie flaglari tekshirilgan.
- Fayl yuklash validatsiyasi (MIME, hajm, EXIF tozalash) testlangan.
- Web ilova mobil brauzerlarda ishlaydi: **Chrome (Android) va Safari (iOS)**
  da asosiy oqim (login → mijoz → qarz → to'lov → hisobot) qo'lda
  tekshirilgan; **Telegram in-app brauzerida** ochilishi tekshirilgan.
- PWA manifest yaroqli; ilova bosh ekranga o'rnatiladi; service worker
  faqat statik resurslarni keshlashi tekshirilgan.
- Admin `/admin` bo'limidagi 6.11 funksiyalari ishlaydi.
- Scheduler deterministik va `job_run` orqali kuzatiluvchan.
- Backup/restore tartibi hujjatlashtirilgan va sinalgan.

---

## 14. Xatolik kodlari — boshlang'ich katalog

| Kod | Ma'nosi |
| --- | --- |
| `UNAUTHORIZED` | Sessiya yo'q/yaroqsiz |
| `FORBIDDEN` | Rol/tenant ruxsati yo'q |
| `VALIDATION_ERROR` | Kiritilgan ma'lumot noto'g'ri |
| `RATE_LIMITED` | So'rovlar chastotasi oshib ketdi |
| `SESSION_EXPIRED` | Sessiya muddati tugagan |
| `CSRF_FAILED` | CSRF token yo'q yoki noto'g'ri |
| `TELEGRAM_NOT_LINKED` | Telegram bog'lanmagan; OTP yuborib bo'lmaydi |
| `LINK_TOKEN_INVALID` | Bog'lash tokeni yaroqsiz yoki muddati o'tgan |
| `OFFER_UNAVAILABLE` | Joriy oferta yo'q (fail-closed) |
| `DUPLICATE_JSHSHIR` | Bu JSHSHIR bilan mijoz allaqachon mavjud |
| `CUSTOMER_NOT_ACTIVE` | Mijoz hali active emas; customer lead holatida |
| `CUSTOMER_BLACKLISTED` | Mijoz shu do'kon qora ro'yxatida |
| `CUSTOMER_RATING_BLOCKED` | Global reyting/blok tufayli rad |
| `CREDIT_LIMIT_EXCEEDED` | Kredit limiti oshib ketadi |
| `MAX_OPEN_DEBTS` | Ochiq qarzlar soni cheklovi |
| `DEBT_NOT_PENDING` | Amal faqat pending qarz uchun |
| `DEBT_EXPIRED` | Pending qarz qabul muddati tugagan |
| `DEBT_NOT_PAYABLE` | Bu holatdagi qarzga to'lov qabul qilinmaydi |
| `PAYMENT_NOT_VOIDABLE` | To'lovni bekor qilib bo'lmaydi |
| `REASON_REQUIRED` | Sabab ko'rsatilishi shart |
| `IDEMPOTENCY_CONFLICT` | Bir xil kalit, boshqa mazmunli so'rov |
| `APPLICATION_PENDING` | Egalik arizasi hali ko'rib chiqilmoqda |
| `LAST_OWNER` | Oxirgi egani olib tashlab bo'lmaydi |
| `SHOP_SUSPENDED` | Do'kon vaqtincha to'xtatilgan |
| `NEWS_LIMIT_REACHED` | Do'kon e'lonlari kunlik limiti tugadi |
| `FILE_ACCESS_DENIED` | Faylga kirish ruxsati yo'q |
| `FILE_STORAGE_ERROR` | Fayl saqlash xizmatida xato |
| `FILE_TOO_LARGE` | Fayl hajmi limitdan oshgan |
| `UNSUPPORTED_FILE_TYPE` | Fayl turi qabul qilinmaydi |

Kodlar foydalanuvchiga xom ko'rinishda chiqarilmaydi — HTML sahifada
lokalizatsiya qilingan xabar ko'rsatiladi (9-bo'lim), kod esa loglar,
testlar va ichki qayta ishlash uchun barqaror bo'lib qoladi. Ro'yxat
implementatsiya davomida kengayadi; kodlar o'zgarmas (stable) bo'lishi
shart.

---

## 15. Texnologik stack va integratsiyalar

| Qatlam | Texnologiya |
| --- | --- |
| Frontend | Jinja2 template'lar + HTMX + yengil CSS, mobile-first, PWA shell |
| Backend | FastAPI (Python) |
| Baza | PostgreSQL (pul — Decimal/NUMERIC, vaqt — timestamptz/UTC) |
| Migratsiyalar | Alembic |
| Autentifikatsiya | Server-side sessiya + HttpOnly cookie, CSRF token, rol va do'kon a'zoligi bilan |
| OTP kanali | Telegram Bot API (MVP); SMS — Phase 2 |
| OTP abstraction | `OtpDeliveryProvider`, `TelegramOtpProvider`, kelajakda `SmsOtpProvider` |
| Bildirishnoma kanallari | In-app + Telegram bot (MVP); Web Push/SMS — Phase 2 |
| Fayl saqlash | S3-mos obyekt saqlash; dev/local MinIO; production provider keyin tanlanadi |
| Scheduler | Alohida protsess (xuddi shu kod bazasi); `job_run` jurnali |
| Testlar | pytest + haqiqiy PostgreSQL; CI majburiy |
