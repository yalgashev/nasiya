import unicodedata

from app.auth.user_agent import MAX_USER_AGENT_LENGTH


def normalize_offer_acceptance_user_agent(
    raw_user_agent: str | None,
) -> str | None:
    if raw_user_agent is None:
        return None
    bounded = raw_user_agent[:MAX_USER_AGENT_LENGTH]
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in bounded
    )
    normalized = " ".join(without_controls.split())
    return normalized or None
