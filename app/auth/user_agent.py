from dataclasses import dataclass
from typing import Final

MAX_USER_AGENT_LENGTH: Final = 512
UNKNOWN_BROWSER_LABEL: Final = "Noma'lum brauzer"
UNKNOWN_DEVICE_LABEL: Final = "Noma'lum qurilma"


@dataclass(frozen=True)
class UserAgentMetadata:
    raw_user_agent: str | None
    browser_label: str
    device_label: str


def truncate_user_agent(raw_user_agent: str | None) -> str | None:
    if raw_user_agent is None:
        return None
    return raw_user_agent[:MAX_USER_AGENT_LENGTH]


def get_user_agent_metadata(raw_user_agent: str | None) -> UserAgentMetadata:
    safe_user_agent = truncate_user_agent(raw_user_agent)
    user_agent = safe_user_agent or ""
    return UserAgentMetadata(
        raw_user_agent=safe_user_agent,
        browser_label=_get_browser_label(user_agent),
        device_label=_get_device_label(user_agent),
    )


def _get_browser_label(user_agent: str) -> str:
    if "Edg/" in user_agent or "Edge/" in user_agent:
        return "Edge"
    if "Firefox/" in user_agent or "FxiOS/" in user_agent:
        return "Firefox"
    if "Chrome/" in user_agent or "CriOS/" in user_agent:
        return "Chrome"
    if _looks_like_safari(user_agent):
        return "Safari"
    return UNKNOWN_BROWSER_LABEL


def _looks_like_safari(user_agent: str) -> bool:
    if "Safari/" not in user_agent:
        return False
    excluded_tokens = ("Chrome/", "Chromium/", "CriOS/", "FxiOS/", "Edg/", "OPR/")
    return not any(token in user_agent for token in excluded_tokens)


def _get_device_label(user_agent: str) -> str:
    if "Android" in user_agent:
        return "Android"
    if any(token in user_agent for token in ("iPhone", "iPad", "iPod")):
        return "iOS"
    if "Windows" in user_agent:
        return "Windows"
    if "Linux" in user_agent:
        return "Linux"
    return UNKNOWN_DEVICE_LABEL
