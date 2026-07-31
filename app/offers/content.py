from dataclasses import dataclass, field
from hashlib import sha256
from typing import Final

_OFFER_TEXT_HASH_PREFIX: Final = b"NASIYA-OFFER-TEXT-V1\x00"


@dataclass(frozen=True, slots=True)
class CanonicalOfferText:
    title: str = field(repr=False)
    body: str = field(repr=False)


def canonicalize_offer_text(
    *,
    title: str,
    body: str,
) -> CanonicalOfferText:
    canonical_title = _canonicalize_line_endings(title)
    canonical_body = _canonicalize_line_endings(body)
    if not canonical_title or canonical_title.isspace():
        raise ValueError("Offer title must not be empty")
    if not canonical_body or canonical_body.isspace():
        raise ValueError("Offer body must not be empty")
    return CanonicalOfferText(title=canonical_title, body=canonical_body)


def compute_offer_content_hash(content: CanonicalOfferText) -> str:
    title_bytes = content.title.encode("utf-8")
    body_bytes = content.body.encode("utf-8")
    payload = (
        _OFFER_TEXT_HASH_PREFIX
        + len(title_bytes).to_bytes(8, byteorder="big", signed=False)
        + title_bytes
        + len(body_bytes).to_bytes(8, byteorder="big", signed=False)
        + body_bytes
    )
    return sha256(payload).hexdigest()


def _canonicalize_line_endings(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")
