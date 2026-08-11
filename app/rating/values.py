"""Redacted identifiers and hashes for the M16 rating boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

__all__ = (
    "DisclosureViewId",
    "RatingEventId",
    "RiskBandDisclosureRequestHash",
)

_SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)


@dataclass(frozen=True, slots=True, repr=False)
class RatingEventId:
    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("Rating event identity is invalid")

    def as_uuid(self) -> UUID:
        return self.value

    def __repr__(self) -> str:
        return "RatingEventId(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class DisclosureViewId:
    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("Disclosure view identity is invalid")

    def as_uuid(self) -> UUID:
        return self.value

    def as_path_segment(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:
        return "DisclosureViewId(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class RiskBandDisclosureRequestHash:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or _SHA256_HEX_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError(
                "Risk-band disclosure request hash must be lowercase SHA-256 hex"
            )

    def __repr__(self) -> str:
        return "RiskBandDisclosureRequestHash(<redacted>)"
