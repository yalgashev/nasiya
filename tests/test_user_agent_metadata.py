import logging

import pytest

from app.auth.user_agent import (
    MAX_USER_AGENT_LENGTH,
    UNKNOWN_BROWSER_LABEL,
    UNKNOWN_DEVICE_LABEL,
    get_user_agent_metadata,
    truncate_user_agent,
)

CHROME_WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
FIREFOX_LINUX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) "
    "Gecko/20100101 Firefox/127.0"
)
SAFARI_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
    "Mobile/15E148 Safari/604.1"
)
EDGE_WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
)
CHROME_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


@pytest.mark.parametrize(
    ("raw_user_agent", "browser_label", "device_label"),
    [
        (CHROME_WINDOWS_UA, "Chrome", "Windows"),
        (FIREFOX_LINUX_UA, "Firefox", "Linux"),
        (SAFARI_IOS_UA, "Safari", "iOS"),
        (EDGE_WINDOWS_UA, "Edge", "Windows"),
        (CHROME_ANDROID_UA, "Chrome", "Android"),
    ],
)
def test_common_user_agents_get_conservative_display_labels(
    raw_user_agent: str,
    browser_label: str,
    device_label: str,
) -> None:
    metadata = get_user_agent_metadata(raw_user_agent)

    assert metadata.browser_label == browser_label
    assert metadata.device_label == device_label
    assert metadata.raw_user_agent == raw_user_agent


@pytest.mark.parametrize("raw_user_agent", [None, "", "custom-client/1.0"])
def test_unknown_user_agent_gets_unknown_labels(raw_user_agent: str | None) -> None:
    metadata = get_user_agent_metadata(raw_user_agent)

    assert metadata.browser_label == UNKNOWN_BROWSER_LABEL
    assert metadata.device_label == UNKNOWN_DEVICE_LABEL


def test_raw_user_agent_is_truncated_to_safe_length() -> None:
    raw_user_agent = "A" * (MAX_USER_AGENT_LENGTH + 100)

    safe_user_agent = truncate_user_agent(raw_user_agent)
    metadata = get_user_agent_metadata(raw_user_agent)

    assert safe_user_agent == "A" * MAX_USER_AGENT_LENGTH
    assert metadata.raw_user_agent == safe_user_agent


def test_user_agent_metadata_is_plain_display_text_for_jinja_autoescape() -> None:
    raw_user_agent = "<script>alert('ua')</script>"

    metadata = get_user_agent_metadata(raw_user_agent)

    assert metadata.raw_user_agent == raw_user_agent
    assert metadata.browser_label == UNKNOWN_BROWSER_LABEL
    assert metadata.device_label == UNKNOWN_DEVICE_LABEL


def test_raw_user_agent_is_not_logged(caplog) -> None:
    raw_user_agent = "Sensitive-UA-Value/1.0"
    logger = logging.getLogger("tests.user_agent_metadata")

    with caplog.at_level(logging.INFO):
        metadata = get_user_agent_metadata(raw_user_agent)
        logger.info(
            "browser=%s device=%s",
            metadata.browser_label,
            metadata.device_label,
        )

    assert raw_user_agent not in caplog.text
