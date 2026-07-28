import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.telegram.bot_api import (
    TELEGRAM_RETRY_AFTER_CAP_SECONDS,
    TelegramApiError,
    TelegramApiErrorCode,
    TelegramBotApiClient,
)
from app.telegram.update_processing import (
    BotReplyIntent,
    BotReplyKey,
    TelegramReplyLanguage,
)

LOGGER = logging.getLogger("nasiya.telegram.bot_reply")


class BotReplyDeliveryStatus(StrEnum):
    SENT = "SENT"
    NOT_SENT = "NOT_SENT"
    NO_REPLY = "NO_REPLY"


_BOT_REPLY_CATALOG: Final[Mapping[TelegramReplyLanguage, Mapping[BotReplyKey, str]]] = (
    MappingProxyType(
        {
            TelegramReplyLanguage.UZ_LATN: MappingProxyType(
                {
                    BotReplyKey.LINKED: (
                        "Telegram muvaffaqiyatli bog'landi. "
                        "Joriy holatni Nasiya veb-sahifasida tekshiring."
                    ),
                    BotReplyKey.ALREADY_LINKED: (
                        "Telegram bog'lanishi allaqachon faol. "
                        "Joriy holatni Nasiya veb-sahifasida tekshiring."
                    ),
                    BotReplyKey.LINK_FAILED: (
                        "Telegramni bog'lab bo'lmadi. Yangi havola yarating va "
                        "holatni Nasiya veb-sahifasida tekshiring."
                    ),
                }
            ),
            TelegramReplyLanguage.RU: MappingProxyType(
                {
                    BotReplyKey.LINKED: (
                        "Telegram успешно подключен. "
                        "Проверьте текущий статус на веб-странице Nasiya."
                    ),
                    BotReplyKey.ALREADY_LINKED: (
                        "Telegram уже подключен. "
                        "Проверьте текущий статус на веб-странице Nasiya."
                    ),
                    BotReplyKey.LINK_FAILED: (
                        "Не удалось подключить Telegram. Создайте новую ссылку "
                        "и проверьте статус на веб-странице Nasiya."
                    ),
                }
            ),
        }
    )
)


def render_bot_reply(intent: BotReplyIntent) -> str:
    return _BOT_REPLY_CATALOG[intent.language][intent.reply_key]


async def deliver_bot_reply_best_effort(
    client: TelegramBotApiClient,
    *,
    intent: BotReplyIntent | None,
    sleeper: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> BotReplyDeliveryStatus:
    if intent is None:
        return BotReplyDeliveryStatus.NO_REPLY

    try:
        await client.send_message(
            chat_id=intent.chat_identity,
            text=render_bot_reply(intent),
        )
    except asyncio.CancelledError:
        raise
    except TelegramApiError as exc:
        if (
            exc.code is TelegramApiErrorCode.TRANSIENT_RATE_LIMIT
            and exc.retry_after_seconds is not None
        ):
            interrupted = await sleeper(
                min(
                    exc.retry_after_seconds,
                    TELEGRAM_RETRY_AFTER_CAP_SECONDS,
                )
            )
            if interrupted is True:
                return BotReplyDeliveryStatus.NOT_SENT
            return await _retry_rate_limited_reply_once(client, intent=intent)
        LOGGER.warning("TELEGRAM_REPLY_NOT_SENT")
        return BotReplyDeliveryStatus.NOT_SENT
    except Exception:
        LOGGER.warning("TELEGRAM_REPLY_NOT_SENT")
        return BotReplyDeliveryStatus.NOT_SENT
    return BotReplyDeliveryStatus.SENT


async def _retry_rate_limited_reply_once(
    client: TelegramBotApiClient,
    *,
    intent: BotReplyIntent,
) -> BotReplyDeliveryStatus:
    try:
        await client.send_message(
            chat_id=intent.chat_identity,
            text=render_bot_reply(intent),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.warning("TELEGRAM_REPLY_NOT_SENT")
        return BotReplyDeliveryStatus.NOT_SENT
    return BotReplyDeliveryStatus.SENT
