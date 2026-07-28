from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.models import User
from app.telegram.repository import get_telegram_link_token_by_id_for_user
from app.telegram.service import TelegramLinkStatus, get_link_status

TELEGRAM_ATTEMPT_POLL_INTERVAL_SECONDS: Final = 3


class TelegramWebLanguage(StrEnum):
    UZ_LATN = "uz"
    RU = "ru"


class TelegramLinkAttemptPresentation(StrEnum):
    WAITING = "WAITING"
    LINKED = "LINKED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def is_terminal(self) -> bool:
        return self is not TelegramLinkAttemptPresentation.WAITING


_UZ_LATN_COPY: Final[Mapping[str, str]] = MappingProxyType(
    {
        "page_title": "Telegram",
        "status_heading": "Holat",
        "actions_heading": "Amallar",
        "linked": "Bog'langan",
        "unlinked": "Bog'lanmagan",
        "issue": "Telegramda bog'lash",
        "reissue": "Telegramda qayta bog'lash",
        "unlink": "Telegramni uzish",
        "unavailable_heading": "Hozircha mavjud emas",
        "bot_unavailable": "Telegram bog'lash havolasi sozlanmagan.",
        "bot_config_error": "Telegram bot havolasi hali sozlanmagan.",
        "back": "Hisobga qaytish",
        "privacy_guidance": (
            "Telegram hisobi boshqa Nasiya hisobiga bog'langan bo'lsa, "
            "xavfsizlik sababli bog'lash bajarilmaydi. Boshqa hisob "
            "tafsilotlari ko'rsatilmaydi."
        ),
        "javascript_required": (
            "Havola yaratilmadi. Sahifadagi Telegram tugmasidan qayta urinib ko'ring."
        ),
        "reveal_heading": "Telegram havolasi",
        "link_hint": "Telegramda bog'lashni tasdiqlang.",
        "relink_hint": "Telegramda qayta bog'lashni tasdiqlang.",
        "open_telegram": "Telegramda ochish",
        "waiting": "Telegramdagi tasdiq kutilmoqda.",
        "attempt_linked": "Telegram muvaffaqiyatli bog'landi.",
        "superseded": "Bu havola yangiroq havola bilan almashtirilgan.",
        "expired": "Bu havolaning muddati tugagan.",
        "attempt_unavailable": "Bu bog'lash urinishining holati mavjud emas.",
        "loading": "Holat tekshirilmoqda",
        "client_ip_error": "Telegram bog'lashni hozir boshlash mumkin emas.",
        "request_failed": "So'rov bajarilmadi.",
        "rate_limited": "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.",
        "already_linked": "Telegram allaqachon bog'langan.",
        "not_linked": "Telegram bog'lanmagan.",
        "unlinked_notice": "Telegram bog'lanishi uzildi.",
    }
)

_RU_COPY: Final[Mapping[str, str]] = MappingProxyType(
    {
        "page_title": "Telegram",
        "status_heading": "Статус",
        "actions_heading": "Действия",
        "linked": "Подключен",
        "unlinked": "Не подключен",
        "issue": "Подключить в Telegram",
        "reissue": "Переподключить в Telegram",
        "unlink": "Отключить Telegram",
        "unavailable_heading": "Временно недоступно",
        "bot_unavailable": "Ссылка подключения Telegram пока не настроена.",
        "bot_config_error": "Ссылка Telegram-бота пока не настроена.",
        "back": "Вернуться к аккаунту",
        "privacy_guidance": (
            "Если Telegram уже подключен к другому аккаунту Nasiya, "
            "подключение не будет выполнено. Данные другого аккаунта не раскрываются."
        ),
        "javascript_required": (
            "Ссылка не создана. Повторите попытку кнопкой Telegram на этой странице."
        ),
        "reveal_heading": "Ссылка Telegram",
        "link_hint": "Подтвердите подключение в Telegram.",
        "relink_hint": "Подтвердите переподключение в Telegram.",
        "open_telegram": "Открыть в Telegram",
        "waiting": "Ожидаем подтверждение в Telegram.",
        "attempt_linked": "Telegram успешно подключен.",
        "superseded": "Эта ссылка заменена более новой.",
        "expired": "Срок действия этой ссылки истек.",
        "attempt_unavailable": "Статус этой попытки подключения недоступен.",
        "loading": "Проверяем статус",
        "client_ip_error": "Сейчас нельзя начать подключение Telegram.",
        "request_failed": "Не удалось выполнить запрос.",
        "rate_limited": "Слишком много попыток. Повторите позже.",
        "already_linked": "Telegram уже подключен.",
        "not_linked": "Telegram не подключен.",
        "unlinked_notice": "Telegram отключен.",
    }
)

_COPY: Final[Mapping[TelegramWebLanguage, Mapping[str, str]]] = MappingProxyType(
    {
        TelegramWebLanguage.UZ_LATN: _UZ_LATN_COPY,
        TelegramWebLanguage.RU: _RU_COPY,
    }
)


def resolve_telegram_web_language(
    accept_language: str | None,
) -> TelegramWebLanguage:
    if accept_language is None:
        return TelegramWebLanguage.UZ_LATN

    weighted: list[tuple[float, int, str]] = []
    for index, item in enumerate(accept_language.split(",")):
        pieces = [piece.strip() for piece in item.split(";")]
        language_tag = pieces[0].casefold()
        quality = 1.0
        for parameter in pieces[1:]:
            if parameter.casefold().startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        if language_tag and quality > 0:
            weighted.append((quality, -index, language_tag))

    for _quality, _position, language_tag in sorted(weighted, reverse=True):
        if language_tag == "ru" or language_tag.startswith("ru-"):
            return TelegramWebLanguage.RU
        if language_tag == "uz" or language_tag.startswith("uz-"):
            return TelegramWebLanguage.UZ_LATN
    return TelegramWebLanguage.UZ_LATN


def get_telegram_web_copy(
    language: TelegramWebLanguage,
) -> Mapping[str, str]:
    return _COPY[language]


def get_link_attempt_presentation(
    session: Session,
    current_user: User,
    attempt_id: UUID,
    now: datetime,
) -> TelegramLinkAttemptPresentation:
    token = get_telegram_link_token_by_id_for_user(
        session,
        current_user,
        attempt_id,
    )
    if token is None:
        return TelegramLinkAttemptPresentation.UNAVAILABLE

    if get_link_status(session, current_user) is TelegramLinkStatus.LINKED:
        return TelegramLinkAttemptPresentation.LINKED
    if token.invalidated_at is not None:
        return TelegramLinkAttemptPresentation.SUPERSEDED
    if token.consumed_at is not None:
        return TelegramLinkAttemptPresentation.UNAVAILABLE
    if _as_utc(token.expires_at) <= _as_utc(now):
        return TelegramLinkAttemptPresentation.EXPIRED
    return TelegramLinkAttemptPresentation.WAITING


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
