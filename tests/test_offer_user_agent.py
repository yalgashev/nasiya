import pytest

from app.auth.user_agent import MAX_USER_AGENT_LENGTH
from app.offers.user_agent import normalize_offer_acceptance_user_agent


@pytest.mark.parametrize(
    ("raw_user_agent", "expected"),
    [
        (None, None),
        ("", None),
        (" \t\n ", None),
        ("A" * MAX_USER_AGENT_LENGTH, "A" * MAX_USER_AGENT_LENGTH),
        ("B" * (MAX_USER_AGENT_LENGTH + 1), "B" * MAX_USER_AGENT_LENGTH),
        (
            " Browser\x00\u200b\n\t Test ",
            "Browser Test",
        ),
    ],
)
def test_offer_acceptance_user_agent_normalization(
    raw_user_agent: str | None,
    expected: str | None,
) -> None:
    assert normalize_offer_acceptance_user_agent(raw_user_agent) == expected


def test_user_agent_is_bounded_before_normalization() -> None:
    raw_user_agent = "A" * (MAX_USER_AGENT_LENGTH - 1) + "\x00Z"

    assert normalize_offer_acceptance_user_agent(raw_user_agent) == (
        "A" * (MAX_USER_AGENT_LENGTH - 1)
    )
