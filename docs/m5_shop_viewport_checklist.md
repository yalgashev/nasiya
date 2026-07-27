# M5 Shop Viewport Checklist

TT §6.13 bo'yicha shop sahifalari uchun qo'lda smoke checklist.

Viewportlar:
- 320 px kenglik: `/shop`, `/shop/select`, `/shop/staff`.
- 430 px kenglik: `/shop`, `/shop/select`, `/shop/staff`.

Har viewportda tekshir:
- Horizontal scroll yo'q.
- Har sahifada bitta `h1` ko'rinadi.
- Tugma, link, input va select targetlari kamida 44x44 px.
- Keyboard focus holati aniq ko'rinadi.
- Status rangdan tashqari matn bilan ham berilgan: `faol`, `to'xtatilgan`,
  `faqat ko'rish rejimi`.
- Xato va muvaffaqiyat xabarlari matn sifatida ko'rinadi.
- Staff sahifasidagi har input/select o'z labeli bilan bog'langan.
- To'liq telefon, user UUID, shop telefon va raw DB xato matni ko'rinmaydi.
- Yangi CSS framework, icon pack yoki font dependency yuklanmaydi.
