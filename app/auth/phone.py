UZBEKISTAN_COUNTRY_CODE = "998"
UZBEKISTAN_LOCAL_LENGTH = 9
CANONICAL_PHONE_LENGTH = 13
REMOVABLE_PHONE_CHARACTERS = str.maketrans(
    {
        " ": "",
        "-": "",
        "(": "",
        ")": "",
    }
)


class PhoneNormalizationError(ValueError):
    pass


def normalize_uzbekistan_phone(raw_phone: str) -> str:
    normalized = raw_phone.translate(REMOVABLE_PHONE_CHARACTERS)

    if normalized.startswith("+"):
        candidate = normalized[1:]
    else:
        candidate = normalized

    if not candidate.isdecimal():
        raise PhoneNormalizationError("Invalid phone number format")

    if len(candidate) == UZBEKISTAN_LOCAL_LENGTH:
        candidate = f"{UZBEKISTAN_COUNTRY_CODE}{candidate}"

    if (
        len(candidate) != len(UZBEKISTAN_COUNTRY_CODE) + UZBEKISTAN_LOCAL_LENGTH
        or not candidate.startswith(UZBEKISTAN_COUNTRY_CODE)
    ):
        raise PhoneNormalizationError("Invalid phone number format")

    canonical_phone = f"+{candidate}"
    if len(canonical_phone) != CANONICAL_PHONE_LENGTH:
        raise PhoneNormalizationError("Invalid phone number format")
    return canonical_phone


def mask_phone_for_display(phone: str) -> str:
    if len(phone) <= 6:
        return "***"
    return f"{phone[:4]}{'*' * (len(phone) - 6)}{phone[-2:]}"
