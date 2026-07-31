from dataclasses import dataclass, field
from uuid import UUID

from app.offers.enums import OfferLanguage


@dataclass(frozen=True, slots=True, repr=False)
class AcceptCurrentRegistrationOfferCommand:
    user_id: UUID
    language: OfferLanguage
    displayed_offer_text_id: UUID
    user_agent_source: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise ValueError("Authenticated user identity is invalid")
        if not isinstance(self.language, OfferLanguage):
            raise ValueError("Selected offer language is invalid")
        if not isinstance(self.displayed_offer_text_id, UUID):
            raise ValueError("Displayed offer text identity is invalid")
        if self.user_agent_source is not None and not isinstance(
            self.user_agent_source,
            str,
        ):
            raise ValueError("User-Agent source is invalid")

    def __repr__(self) -> str:
        return (
            "AcceptCurrentRegistrationOfferCommand("
            f"user_id={self.user_id!r}, "
            f"language={self.language.value!r}, "
            f"displayed_offer_text_id={self.displayed_offer_text_id!r}, "
            "user_agent_source=<redacted>)"
        )
