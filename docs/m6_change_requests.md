# M6 Change Requests

Status: PRE-M6 GATE CLOSED
Sana: 2026-07-27

Bu hujjat M5 discoverydan M6ga olib o'tilgan change requestlar bo'yicha yozma
qarorlarni qayd etadi. `docs/tt_nasiya_web_v1.md` M5 closure baselineidan keyin
semantik o'zgartirilmaydi; M6 implementation tafsilotlari ushbu hujjat va
`docs/m6_scope_contract.md` orqali yuritiladi.

## Qarorlar

| ID | Holat | Qaror | Implementation ta'siri |
| --- | --- | --- | --- |
| CR-M6-01 | Yopildi | Badal debt yoki payment emas. Debt `original_amount`i faqat nasiyaga qoldirilgan net summani bildiradi. | M6 debt yaratishda badal uchun yozuv yaratilmaydi; naqd savdo kvitansiyasi subsystemi scope tashqarisida qoladi. |
| CR-M6-02 | Yopildi | Qoldiqdan katta to'lov qat'iy rad etiladi. | `PAYMENT_EXCEEDS_BALANCE` M6 debt/payment scopeida stable error sifatida kiritiladi; customer credit/wallet subsystemi qo'shilmaydi. |
| CR-M6-03 | Yopildi | Clawback reversal faqat shop owneri yoki platforma admini tomonidan, sabab bilan, auditlangan holda bajariladi. | Cashier/manager reversal qilmaydi; reversal faqat `overdue` holatda va `written_off`dan oldin ruxsat etiladi. |

## Joriy M6 disposition

CR-M6-01...03 product qarorlari saqlanadi, ammo ular joriy M6 capability'sini
kengaytirmaydi. Product Ownerning `defaul qarorlar bilan yopilsin` qarori va
v0.9 execution authorizationiga ko'ra joriy M6 faqat **Production Telegram
Linking**:

- authenticated account deep-link/QR va HTMX status;
- dedicated long-polling worker;
- M4 linking domainiga atomic consume bridge;
- cursor, heartbeat, quarantine va post-commit private bot reply;
- current-password protected unlink/relink.

Shop settings, debt/payment, badal, overpayment va clawback keyingi alohida
milestonega qoldiriladi. Ular M6.01 yoki boshqa M6 mikro-vazifasiga yashirincha
kiritilmaydi.
