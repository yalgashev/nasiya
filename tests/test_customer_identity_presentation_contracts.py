from app.auth.error_codes import ErrorCode
from app.customer_identity.web_presentation import (
    CustomerIdentityWebLanguage,
    get_customer_identity_web_message,
    resolve_customer_identity_web_language,
)


def test_identity_language_resolution_reuses_uz_ru_fallback() -> None:
    assert (
        resolve_customer_identity_web_language(None, None)
        is CustomerIdentityWebLanguage.UZ_LATN
    )
    assert (
        resolve_customer_identity_web_language("ru", "uz")
        is CustomerIdentityWebLanguage.RU
    )
    assert (
        resolve_customer_identity_web_language("unsupported", "ru-RU")
        is CustomerIdentityWebLanguage.RU
    )


def test_identity_error_messages_are_safe_complete_and_localized() -> None:
    codes = {
        ErrorCode.DUPLICATE_JSHSHIR,
        ErrorCode.CUSTOMER_DRAFT_REQUIRED,
        ErrorCode.CUSTOMER_IDENTITY_CHANGED,
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED,
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
    }
    for code in codes:
        uz = get_customer_identity_web_message(
            CustomerIdentityWebLanguage.UZ_LATN,
            code,
        )
        ru = get_customer_identity_web_message(
            CustomerIdentityWebLanguage.RU,
            code,
        )
        assert uz
        assert ru
        assert uz != ru
        rendered = f"{uz} {ru}".casefold()
        for forbidden in (
            "cipher",
            "nonce",
            "key_id",
            "constraint",
            "object_file",
            "presigned",
        ):
            assert forbidden not in rendered


def test_unrelated_error_has_no_feature_local_message() -> None:
    assert (
        get_customer_identity_web_message(
            CustomerIdentityWebLanguage.UZ_LATN,
            ErrorCode.UNAUTHORIZED,
        )
        is None
    )
