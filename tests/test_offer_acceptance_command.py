from dataclasses import FrozenInstanceError, fields
from uuid import UUID

import pytest

from app.offers.commands import AcceptCurrentRegistrationOfferCommand
from app.offers.enums import OfferLanguage

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
TEXT_ID = UUID("22222222-2222-4222-8222-222222222222")


def test_acceptance_command_has_only_server_authenticated_and_displayed_input() -> None:
    command = AcceptCurrentRegistrationOfferCommand(
        user_id=USER_ID,
        language=OfferLanguage.UZ_CYRL,
        displayed_offer_text_id=TEXT_ID,
        user_agent_source="Browser/1.0",
    )

    assert tuple(field.name for field in fields(command)) == (
        "user_id",
        "language",
        "displayed_offer_text_id",
        "user_agent_source",
    )
    assert command.user_id == USER_ID
    assert command.language is OfferLanguage.UZ_CYRL
    assert command.displayed_offer_text_id == TEXT_ID
    for forbidden in (
        "content_hash",
        "version_number",
        "offer_version_id",
        "purpose",
        "status",
        "actor_user_id",
        "accepted_at",
        "session_id",
        "csrf",
    ):
        assert not hasattr(command, forbidden)


def test_command_accepts_raw_user_agent_source_for_later_normalization() -> None:
    raw_source = "Browser\x00  Agent " + ("x" * 600)

    command = AcceptCurrentRegistrationOfferCommand(
        user_id=USER_ID,
        language=OfferLanguage.RU,
        displayed_offer_text_id=TEXT_ID,
        user_agent_source=raw_source,
    )

    assert command.user_agent_source == raw_source
    assert raw_source not in repr(command)
    assert "user_agent_source=<redacted>" in repr(command)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"user_id": "ATTACKER_USER_VALUE"},
            "Authenticated user identity is invalid",
        ),
        (
            {"language": "ATTACKER_LANGUAGE"},
            "Selected offer language is invalid",
        ),
        (
            {"displayed_offer_text_id": "ATTACKER_TEXT_VALUE"},
            "Displayed offer text identity is invalid",
        ),
        ({"user_agent_source": 424242}, "User-Agent source is invalid"),
    ],
)
def test_command_rejects_untyped_input_without_echoing_it(
    overrides: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "user_id": USER_ID,
        "language": OfferLanguage.UZ_LATN,
        "displayed_offer_text_id": TEXT_ID,
        "user_agent_source": None,
    }
    values.update(overrides)

    with pytest.raises(ValueError) as error:
        AcceptCurrentRegistrationOfferCommand(**values)

    assert str(error.value) == message
    assert all(str(value) not in str(error.value) for value in overrides.values())


def test_acceptance_command_is_immutable_and_repr_hides_user_agent() -> None:
    command = AcceptCurrentRegistrationOfferCommand(
        user_id=USER_ID,
        language=OfferLanguage.UZ_LATN,
        displayed_offer_text_id=TEXT_ID,
        user_agent_source="SECRET RAW USER AGENT",
    )

    with pytest.raises(FrozenInstanceError):
        command.language = OfferLanguage.RU
    assert "SECRET" not in repr(command)
