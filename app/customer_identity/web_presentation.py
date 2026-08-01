from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode
from app.otp.web_presentation import OtpWebLanguage, resolve_otp_web_language

CUSTOMER_IDENTITY_LOCALE_COOKIE_NAME: Final = "nasiya_otp_locale"


class CustomerIdentityWebLanguage(StrEnum):
    UZ_LATN = "uz"
    RU = "ru"


_UZ_LATN_MESSAGES: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.DUPLICATE_JSHSHIR: ("Bu JSHSHIR bilan mijoz allaqachon mavjud."),
        ErrorCode.CUSTOMER_DRAFT_REQUIRED: "Avval mijoz qoralamasini yarating.",
        ErrorCode.CUSTOMER_IDENTITY_CHANGED: (
            "Shaxsiy ma'lumotlar o'zgargan. Sahifani yangilang."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED: ("Hujjat o'zgargan. Sahifani yangilang."),
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE: (
            "Shaxsiy ma'lumotlar hozir mavjud emas."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE: "Hujjat hozir mavjud emas.",
    }
)
_RU_MESSAGES: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.DUPLICATE_JSHSHIR: ("Клиент с таким ПИНФЛ уже существует."),
        ErrorCode.CUSTOMER_DRAFT_REQUIRED: ("Сначала создайте черновик клиента."),
        ErrorCode.CUSTOMER_IDENTITY_CHANGED: (
            "Персональные данные изменились. Обновите страницу."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED: ("Документ изменился. Обновите страницу."),
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE: (
            "Персональные данные сейчас недоступны."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE: ("Документ сейчас недоступен."),
    }
)
_MESSAGES: Final[Mapping[CustomerIdentityWebLanguage, Mapping[ErrorCode, str]]] = (
    MappingProxyType(
        {
            CustomerIdentityWebLanguage.UZ_LATN: _UZ_LATN_MESSAGES,
            CustomerIdentityWebLanguage.RU: _RU_MESSAGES,
        }
    )
)


@dataclass(frozen=True, slots=True)
class CustomerIdentityWebCopy:
    page_title: str
    heading: str
    intro: str
    draft_status: str
    identity_heading: str
    missing_identity: str
    first_name_label: str
    last_name_label: str
    middle_name_label: str
    jshshir_label: str
    jshshir_help: str
    document_type_label: str
    passport_label: str
    id_card_label: str
    document_number_label: str
    document_number_help: str
    save_button: str
    saved_notice: str
    document_heading: str
    document_missing: str
    document_current: str
    document_file_label: str
    document_file_help: str
    upload_button: str
    uploaded_notice: str
    open_document: str
    account_link: str
    profile_link: str
    navigation_label: str


_UZ_LATN_COPY: Final = CustomerIdentityWebCopy(
    page_title="Shaxsiy ma'lumotlar",
    heading="Shaxsiy ma'lumotlar va hujjat",
    intro="Ma'lumotlar faqat o'zingizning mijoz qoralamangiz uchun saqlanadi.",
    draft_status="Qoralama",
    identity_heading="Shaxsiy ma'lumotlar",
    missing_identity="Shaxsiy ma'lumotlar hali kiritilmagan.",
    first_name_label="Ism",
    last_name_label="Familiya",
    middle_name_label="Otasining ismi (ixtiyoriy)",
    jshshir_label="JSHSHIR",
    jshshir_help="14 ta raqamni kiriting. Saqlangan qiymat qayta ko'rsatilmaydi.",
    document_type_label="Hujjat turi",
    passport_label="Pasport",
    id_card_label="ID-karta",
    document_number_label="Hujjat raqami",
    document_number_help="Saqlangan raqam qayta ko'rsatilmaydi.",
    save_button="Ma'lumotlarni saqlash",
    saved_notice="Shaxsiy ma'lumotlar saqlandi.",
    document_heading="Hujjat rasmi",
    document_missing="Joriy hujjat rasmi yo'q.",
    document_current="Joriy hujjat rasmi mavjud.",
    document_file_label="Hujjat rasmini tanlang",
    document_file_help="JPEG, PNG yoki WebP rasm. Eng ko'pi 10 MiB.",
    upload_button="Hujjat rasmini yuklash",
    uploaded_notice="Hujjat rasmi yuklandi.",
    open_document="Joriy hujjat rasmini ochish",
    account_link="Hisobga qaytish",
    profile_link="Mijoz profiliga qaytish",
    navigation_label="Shaxsiy ma'lumotlar navigatsiyasi",
)
_RU_COPY: Final = CustomerIdentityWebCopy(
    page_title="Персональные данные",
    heading="Персональные данные и документ",
    intro="Данные сохраняются только для вашего черновика клиента.",
    draft_status="Черновик",
    identity_heading="Персональные данные",
    missing_identity="Персональные данные еще не указаны.",
    first_name_label="Имя",
    last_name_label="Фамилия",
    middle_name_label="Отчество (необязательно)",
    jshshir_label="ПИНФЛ",
    jshshir_help="Введите 14 цифр. Сохраненное значение повторно не показывается.",
    document_type_label="Тип документа",
    passport_label="Паспорт",
    id_card_label="ID-карта",
    document_number_label="Номер документа",
    document_number_help="Сохраненный номер повторно не показывается.",
    save_button="Сохранить данные",
    saved_notice="Персональные данные сохранены.",
    document_heading="Изображение документа",
    document_missing="Текущего изображения документа нет.",
    document_current="Текущее изображение документа загружено.",
    document_file_label="Выберите изображение документа",
    document_file_help="Изображение JPEG, PNG или WebP. Не более 10 МиБ.",
    upload_button="Загрузить изображение документа",
    uploaded_notice="Изображение документа загружено.",
    open_document="Открыть текущее изображение документа",
    account_link="Вернуться к аккаунту",
    profile_link="Вернуться к профилю клиента",
    navigation_label="Навигация по персональным данным",
)
_COPY: Final[Mapping[CustomerIdentityWebLanguage, CustomerIdentityWebCopy]] = (
    MappingProxyType(
        {
            CustomerIdentityWebLanguage.UZ_LATN: _UZ_LATN_COPY,
            CustomerIdentityWebLanguage.RU: _RU_COPY,
        }
    )
)

_UZ_LATN_GENERIC_MESSAGES: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.UNAUTHORIZED: "Kirish talab qilinadi.",
        ErrorCode.CSRF_FAILED: "So'rov xavfsizlik tekshiruvidan o'tmadi.",
        ErrorCode.RATE_LIMITED: "Juda ko'p urinish. Keyinroq qayta urinib ko'ring.",
        ErrorCode.VALIDATION_ERROR: "Kiritilgan ma'lumotlarni tekshiring.",
        ErrorCode.FILE_ACCESS_DENIED: "Hujjatni ochishga ruxsat yo'q.",
        ErrorCode.FILE_STORAGE_ERROR: "Hujjat fayli hozir mavjud emas.",
        ErrorCode.FILE_TOO_LARGE: "Fayl hajmi ruxsat etilgan chegaradan katta.",
        ErrorCode.UNSUPPORTED_FILE_TYPE: "Bu fayl turi qabul qilinmaydi.",
    }
)
_RU_GENERIC_MESSAGES: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.UNAUTHORIZED: "Требуется вход.",
        ErrorCode.CSRF_FAILED: "Запрос не прошел проверку безопасности.",
        ErrorCode.RATE_LIMITED: "Слишком много попыток. Повторите позже.",
        ErrorCode.VALIDATION_ERROR: "Проверьте введенные данные.",
        ErrorCode.FILE_ACCESS_DENIED: "Нет доступа к документу.",
        ErrorCode.FILE_STORAGE_ERROR: "Файл документа сейчас недоступен.",
        ErrorCode.FILE_TOO_LARGE: "Размер файла превышает допустимый предел.",
        ErrorCode.UNSUPPORTED_FILE_TYPE: "Этот тип файла не поддерживается.",
    }
)


def resolve_customer_identity_web_language(
    locale_cookie: str | None,
    accept_language: str | None,
) -> CustomerIdentityWebLanguage:
    inherited = resolve_otp_web_language(locale_cookie, accept_language)
    if inherited is OtpWebLanguage.RU:
        return CustomerIdentityWebLanguage.RU
    return CustomerIdentityWebLanguage.UZ_LATN


def get_customer_identity_web_message(
    language: CustomerIdentityWebLanguage,
    code: ErrorCode,
) -> str | None:
    message = _MESSAGES[language].get(code)
    if message is not None:
        return message
    generic = (
        _RU_GENERIC_MESSAGES
        if language is CustomerIdentityWebLanguage.RU
        else _UZ_LATN_GENERIC_MESSAGES
    )
    return generic.get(code)


def get_customer_identity_web_copy(
    language: CustomerIdentityWebLanguage,
) -> CustomerIdentityWebCopy:
    return _COPY[language]
