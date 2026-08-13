"""Small localized copy surface for M13 debt SSR pages."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage, get_debt_web_error_message

COPY: Mapping[DebtWebLanguage, Mapping[str, str]] = MappingProxyType(
    {
        DebtWebLanguage.UZ_LATN: MappingProxyType(
            {
                "debts": "Qarzlar",
                "new_debt": "Yangi qarz taklifi",
                "amount": "Asl summa (so‘m)",
                "discount": "Chegirma (%)",
                "discounted": "Chegirmali summa",
                "due": "To‘lov sanasi",
                "expiry": "Qabul qilish muddati",
                "status": "Holat",
                "status_pending": "Kutilmoqda",
                "status_active": "Faol",
                "status_paid": "To'langan",
                "status_overdue": "Muddati o'tgan",
                "status_written_off": "Undirishdan chiqarilgan",
                "status_written_off_settled": "Undirish qarzi yopilgan",
                "status_rejected": "Rad etilgan",
                "status_cancelled": "Bekor qilingan",
                "status_expired": "Muddati tugagan",
                "current_balance": "Hozirgi qoldiq",
                "discounted_basis": "Chegirmali hisob",
                "original_basis": "Asl summa bo'yicha hisob",
                "clawback": "Muddat o'tgani uchun chegirma bekor qilindi.",
                "paid_late": "Muddatdan keyin to'langan",
                "refresh": "Holatni yangilash",
                "navigation": "Sahifa navigatsiyasi",
                "create": "Taklif yaratish",
                "accept": "Qabul qilish",
                "reject": "Rad etish",
                "cancel": "Bekor qilish",
                "payment_history": "To'lovlar tarixi",
                "record_payment": "To'lov kiritish",
                "reason_optional": "Sabab (ixtiyoriy)",
                "reason_required": "Sabab",
                "empty": "Qarzlar topilmadi.",
                "legal": "Qarz shartlari",
                "back": "Ortga",
                "created": "Qarz taklifi yaratildi.",
                "accepted": "Qarz qabul qilindi.",
                "rejected": "Qarz rad etildi.",
                "cancelled": "Qarz bekor qilindi.",
                "unavailable": "Qarz hozir mavjud emas.",
            }
        ),
        DebtWebLanguage.RU: MappingProxyType(
            {
                "debts": "Долги",
                "new_debt": "Новое предложение долга",
                "amount": "Исходная сумма (сум)",
                "discount": "Скидка (%)",
                "discounted": "Сумма со скидкой",
                "due": "Дата платежа",
                "expiry": "Срок принятия",
                "status": "Статус",
                "status_pending": "Ожидается",
                "status_active": "Активен",
                "status_paid": "Оплачен",
                "status_overdue": "Срок оплаты истёк",
                "status_written_off": "Списан для взыскания",
                "status_written_off_settled": "Списанный долг погашен",
                "status_rejected": "Отклонён",
                "status_cancelled": "Отменён",
                "status_expired": "Срок истёк",
                "current_balance": "Текущий остаток",
                "discounted_basis": "Расчёт со скидкой",
                "original_basis": "Расчёт по первоначальной сумме",
                "clawback": "Скидка отменена из-за просрочки.",
                "paid_late": "Оплачен после срока",
                "refresh": "Обновить данные",
                "navigation": "Навигация по странице",
                "create": "Создать предложение",
                "accept": "Принять",
                "reject": "Отклонить",
                "cancel": "Отменить",
                "payment_history": "История платежей",
                "record_payment": "Внести платёж",
                "reason_optional": "Причина (необязательно)",
                "reason_required": "Причина",
                "empty": "Долги не найдены.",
                "legal": "Условия долга",
                "back": "Назад",
                "created": "Предложение долга создано.",
                "accepted": "Долг принят.",
                "rejected": "Долг отклонён.",
                "cancelled": "Долг отменён.",
                "unavailable": "Долг сейчас недоступен.",
            }
        ),
    }
)


def resolve_debt_web_language(accept_language: str | None) -> DebtWebLanguage:
    if accept_language and accept_language.casefold().lstrip().startswith("ru"):
        return DebtWebLanguage.RU
    return DebtWebLanguage.UZ_LATN


def debt_error_message(language: DebtWebLanguage, raw_error: str | None) -> str | None:
    if raw_error is None:
        return None
    try:
        error = ErrorCode(raw_error)
    except ValueError:
        return None
    return get_debt_web_error_message(language, error) or COPY[language]["unavailable"]


def debt_notice(language: DebtWebLanguage, raw_notice: str | None) -> str | None:
    if raw_notice in {"created", "accepted", "rejected", "cancelled"}:
        return COPY[language][raw_notice]
    return None
