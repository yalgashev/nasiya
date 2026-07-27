# M5.00 -- Erta product discovery natijalari

Status: MAZMUNIY JAVOBLAR OLINDI
Sana: 2026-07-26
Respondentlar: 2 ta real nasiya do'koni egasi
Manba: yozma savol-javob, Product Owner tomonidan umumlashtirilgan
Suhbat davomiyligi: tasdiqlanmagan

## 1. Kontekst va dalil chegarasi

Codex ownerlar bilan o'tkazilgan suhbatlarda ishtirok etmadi. Ushbu hujjat
Product Owner taqdim etgan, ikki owner javoblaridan umumlashtirilgan
natijalarni qayd etadi.

Quyidagi matnlar so'zma-so'z sitata emas. Respondentlarning shaxsiy
ma'lumotlari saqlanmaydi. Ikki owner orasidagi alohida farqlar taqdim
etilmagani uchun hujjatda faqat umumiy xulosa yozilgan.

M5.00 topilmasi M5 scope yoki TT ni avtomatik o'zgartirmaydi:

```text
topilma
-> change request
-> Product Owner qarori
-> zarur bo'lsa scope yoki TT yangi versiyasi
-> implementatsiya
```

M5.01 bu hujjat yopilishini kutmasdan davom etishi mumkin. M5 ga aniq zid
topilma bo'lsa, unga tegishli kod vazifasidan oldin change request hal
qilinadi.

## 2. Umumlashtirilgan natijalar

### Q1. Bitta xodim bir nechta filial yoki do'konda ishlaydimi?

**Javob:** Yo'q. So'ralgan ikki do'konda xodim bir nechta do'kon o'rtasida
ishlamaydi.

**Talqin:**

- Bu hozirgi pilot segmentida ko'p do'konli cashier oqimiga talab
  topilmaganini ko'rsatadi.
- Bu bitta owner bir nechta do'konga egalik qilishi mumkinligini rad etmaydi.
- Har do'kon uchun alohida subscription olish billing masalasini hal qiladi,
  lekin bitta ownerning bir nechta tenantga kirish masalasini hal qilmaydi.
- Subscription MVP doirasiga kirmaydi va shop membership modelidan mustaqil.

**M5 uchun amaliy qaror:**

- Bitta `user` bir nechta `shop`ga a'zo bo'la oladigan model saqlanadi.
- `shop_staff` uniqueness `(shop_id, user_id)` bo'yicha bo'ladi.
- Shop switcher faqat foydalanuvchining faol shop a'zoligi bittadan ko'p
  bo'lganda ko'rinadi.
- Kelajakda billing qo'shilsa, har bir `shop` alohida subscriptionga ega
  bo'lishi mumkin; buning uchun ownerga alohida user akkaunti ochilmaydi.

Bu topilma PO-1 ni o'zgartirish uchun yetarli qarama-qarshi dalil emas.

### Q2. Owner, manager va cashier amalda nimasi bilan farq qiladi?

**Javob:** Kichik do'kon segmentida manager roli kerak emas. Amaldagi
rollar owner va cashier.

Ikki owner javobidan quyidagi ruxsat matritsasi kelib chiqadi:

| Amal | Owner | Cashier |
| --- | --- | --- |
| Yangi nasiya ochish | Ha | Ha |
| To'lov qabul qilish | Ha | Ha |
| Operatsiya uchun zarur mijoz va qarzni ko'rish | Ha | Ha |
| Barcha qarzlar va hisobotlarni ko'rish | Ha | Yo'q |
| Xodim qo'shish va boshqarish | Ha | Yo'q |
| Do'kon sozlamalarini o'zgartirish | Ha | Yo'q |

Cashierga to'lov qabul qilish va nasiya ochish uchun zarur read huquqi
beriladi, lekin bu barcha qarzlar, umumiy hisobotlar yoki eksportga kirish
huquqini bermaydi.

**Qarama-qarshilik:** TT 5-bo'limida manager alohida MVP roli sifatida
berilgan. Discovery signali manager rolini avtomatik olib tashlamaydi.

**M5 disposition:** Keyingi muzlatilgan M5 vazifalari `manager` role qiymati,
staff lifecycle va basic read contextni aniq talab qildi. Shuning uchun
CR-M5-01 M5 uchun quyidagicha yopildi:

- `manager` schema, service va UI role qiymati sifatida saqlanadi;
- manager owner-only staff mutation route'laridan `FORBIDDEN` oladi;
- managerga alohida settings yoki owner vakolati berilmaydi;
- rolni butun MVPdan olib tashlash faqat yangi change request va TT
  revisiyasi bilan amalga oshiriladi.

### Q3. Yangi xodimni tizimga telefon orqali bog'lash tabiiy oqimmi?

**Javob:** Ha.

**M5 uchun xulosa:** PO-3 tasdiqlandi. M5 da owner bazada mavjud,
authenticated userni kanonik telefon raqami orqali shopga tanlangan staff
roli bilan bog'laydi. Invitation va owner tomonidan yangi user akkaunti
yaratish keyingi milestone masalasi bo'lib qoladi.

### Q4. Xodim bo'shatilganda huquqi darhol yopilishi kerakmi?

**Javob:** Ha.

**M5 uchun xulosa:**

- Membership deaktivatsiya qilingach, xodimning shu shopga kirishi darhol
  yopiladi.
- Oldin ochilgan sessiya tenant huquqini saqlab qolmaydi; membership har
  so'rovda server tomonida tekshiriladi.
- Deaktivatsiya qilingan xodimda read-only kirish ham qolmaydi.
- Deaktivatsiya va oldingi amallar audit jurnalida saqlanadi.

Xodim deaktivatsiyasi shop suspend holatidan alohida policy hisoblanadi.

### Q5. Suspend paytida eski ma'lumotni read-only ko'rish kerakmi?

**Javob:** Ha, barcha faol xodimlarga.

**Aniq semantika:**

- Suspend mavjud read huquqlarini saqlaydi, lekin yangi read huquqi bermaydi.
- Owner barcha tarixiy qarzlar va hisobotlarni ko'rishda davom etadi.
- Cashier faqat odatdagi roli doirasidagi operatsion ma'lumotni ko'radi.
- Barcha tenant business write amallari `SHOP_SUSPENDED` bilan rad etiladi.
- `/shop/select` session-context mutation bo'lib, suspend policy doirasiga
  kirmaydi; user suspended shopdan boshqa active membershipiga o'ta oladi.
- Deaktivatsiya qilingan membership suspend sabab qayta read huquqi olmaydi.

Bu TT dagi suspend siyosatini aniqlashtiradigan talab. CR-M5-02 keyingi
muzlatilgan M5 policy va route matritsasi bilan shu semantikada yopildi.

### Q6. Qarz bitta to'lov muddati bilanmi yoki grafik bilanmi?

**Javob:** Bitta to'lov muddati. Grafik qurilmaydi.

Bu natija TT 4.2-bo'limidagi single due date modelini tasdiqlaydi. Rasmiy
installment schedule, schedule item yoki bo'linma darajasidagi overdue
modeli qo'shilmaydi.

### Q7. Ustama, oldindan to'lov, qisman to'lov va kechikish qanday yuritiladi?

#### Q7.1. Ustama

**Javob:** TT dagi ikki-summa modeli saqlanadi, lekin kiritish formasi
do'kon ishlatadigan tilda bo'ladi.

Forma quyidagilarni so'raydi:

- naqd narx;
- ustama foizi yoki to'g'ridan-to'g'ri nasiya narxi.

Server nasiya narxini hisoblaydi yoki to'g'ridan-to'g'ri qabul qiladi:

```text
discounted_amount = naqd narx
original_amount = nasiya narxi
```

Alohida `cash_price` ustuni qo'shilmaydi. Naqd narx TT modelidagi
`discounted_amount` sifatida, nasiya narxi esa `original_amount` sifatida
saqlanadi.

Ustama qarz yaratilganda qat'iy belgilanadi. Ustamaning vaqt o'tishi bilan
o'sishi, masalan har oy qo'shimcha foiz yoki kechikkan sari ko'payish,
TT 12-bo'limiga zid va alohida katta revisiyani talab qiladi.

#### Q7.2. Oldindan to'lov

**Javob:** Oldindan to'lov qarzga kirmaydi.

`original_amount` faqat nasiyaga berilgan summani ifodalaydi. Masalan:

```text
Tovar narxi:          1 200 000
Oldindan to'lov:        400 000
Nasiyaga berilgan:      800 000
Kredit limiti:        1 000 000
```

Limit tekshiruvida 800 000 ishlatiladi. Badal debt yaratilgandan keyingi
`payment` sifatida yozilmaydi, chunki u imzodan oldin olinadi va uni void
qilish qarzni noto'g'ri qayta oshirishi mumkin.

Badal uchun hujjatli iz talab qilinsa, u nasiya paymentidan alohida naqd
savdo kvitansiyasi bo'ladi. Bu alohida funksional scope hisoblanadi.

#### Q7.3. Qisman to'lov

**Javob:**

- Minimal qisman to'lov summasi yo'q.
- Qoldiqdan ortiq to'lov qat'iy rad etiladi.
- To'lov summasi mavjud qoldiq bilan cheklanadi.

Ortiqcha payment saqlansa, to'lov jurnali va
`max(original_amount - payments, 0)` bilan hisoblangan balans orasida
nomuvofiqlik yuzaga keladi. Ortiqcha summa uchun "mijoz krediti" yaratish
esa alohida subsystem talab qiladi va joriy scopega kirmaydi.

To'lov formasida joriy qoldiq oldindan to'ldiriladi va "to'liq to'lash"
amali mavjud bo'ladi.

#### Q7.4. Kechikish

**Javob:**

- Imtiyoz muddati yo'q.
- Kunlik jarima yo'q.
- Avtomatik `written_off` yo'q.
- Sabab bilan vakolatli clawback bekor qilish amali kerak.

Clawback muddat o'tganda bir marta ishlaydi. Chegaraviy yoki nizoli holatda
vakolatli actor clawbackni bekor qila oladi; sabab majburiy va amal audit
qilinadi. Ushbu actorning aniq roli change request yoki TT aniqlashtirishida
belgilanadi.

Imtiyoz muddati overdue semantikasini scheduler, hisobot, reyting va hard
block bo'yicha o'zgartirishi sabab tanlanmadi. Kunlik jarima clawback bilan
parallel ikkinchi jarima mexanizmini yaratadi. Avtomatik `written_off` esa
global blok va reyting oqibatini inson qarorisiz qo'llamasligi kerak.

## 3. Change request nomzodlari

| ID | Topilma | Tegishli joy | Qaror yoki keyingi qadam |
| --- | --- | --- | --- |
| CR-M5-01 | Manager pilot segmentida kerak emas | TT 5 va role/policy scope | **YOPILDI (M5):** role qiymati/basic read saqlanadi; owner vakolati berilmaydi |
| CR-M5-02 | Suspendda faol xodimlar role-scoped read-only ko'radi | TT suspend siyosati | **YOPILDI (M5):** business write bloklanadi; session-context switch mustasno |
| CR-M6-01 | Badal debt/payment tarkibiga kirmaydi | TT 4.3 va debt yaratish | **OCHIQ (PRE-M6):** nasiyaga berilgan net summani saqlashni TTda aniqlashtirish |
| CR-M6-02 | Ortiqcha to'lov rad etiladi | TT 6.5 | **OCHIQ (PRE-M6):** qoldiqdan katta paymentni rad etishni TTga yozish |
| CR-M6-03 | Vakolatli clawback bekor qilish kerak | TT 6.11 | **OCHIQ (PRE-M6):** rol, sabab va audit talabini belgilash |

M5 dispositionlari keyingi muzlatilgan M5 vazifalarida berilgan aniq
talablarni qayd etadi. M6 satrlari esa discovery topilmalaridan kelib chiqqan
ochiq change request nomzodlari bo'lib, o'zicha TTni o'zgartirmaydi.

## 4. Tasdiqlangan va ochiq holatlar

| Mezon | Holat |
| --- | --- |
| Real nasiya do'koni ownerlari | 2/2 |
| M5.00 mazmuniy savollari | 7/7 |
| Javoblar hujjatlashtirildi | Ha |
| Respondentlar PII saqlandi | Yo'q |
| Har bir suhbat 30-40 daqiqa bo'lgani tasdiqlandi | Yo'q |
| M5 change request dispositionlari | 2/2 yopildi |
| PRE-M6 change requestlar bo'yicha PO/TT qarori | Kutilmoqda |

Mazmuniy discovery yakunlangan. Biroq yozma savol-javoblarning har biri
30-40 daqiqalik suhbatga teng bo'lgani tasdiqlanmagan. M5.00 ni to'liq
`TEKSHIRILGAN` deb yopish uchun Product Owner yozma formatni acceptance
criterionga teng deb tasdiqlashi yoki davomiylik talabini qoplaydigan
follow-up suhbatlar o'tkazishi kerak.

Bu ochiq protsess mezoni M5.01 boshlanishi yoki davom etishini bloklamaydi.
