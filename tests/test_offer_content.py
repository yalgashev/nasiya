from hashlib import sha256

import pytest

from app.offers.content import (
    CanonicalOfferText,
    canonicalize_offer_text,
    compute_offer_content_hash,
)


def _expected_hash(title: str, body: str) -> str:
    title_bytes = title.encode("utf-8")
    body_bytes = body.encode("utf-8")
    payload = (
        b"NASIYA-OFFER-TEXT-V1\x00"
        + len(title_bytes).to_bytes(8, byteorder="big", signed=False)
        + title_bytes
        + len(body_bytes).to_bytes(8, byteorder="big", signed=False)
        + body_bytes
    )
    return sha256(payload).hexdigest()


def test_canonicalization_changes_only_line_endings() -> None:
    content = canonicalize_offer_text(
        title="  Sarlavha\r\nikkinchi \r",
        body="\tMatn\r\nqator\rso\u02bbz  ",
    )

    assert content.title == "  Sarlavha\nikkinchi \n"
    assert content.body == "\tMatn\nqator\nso\u02bbz  "


def test_hash_uses_exact_prefix_lengths_utf8_and_lowercase_sha256() -> None:
    content = canonicalize_offer_text(
        title="Taklif \u21161",
        body="\u041c\u0430\u0442\u043d \U0001f512",
    )

    content_hash = compute_offer_content_hash(content)

    assert content_hash == _expected_hash(content.title, content.body)
    assert len(content_hash) == 64
    assert content_hash == content_hash.lower()


def test_line_ending_variants_have_the_same_hash() -> None:
    crlf = canonicalize_offer_text(title="A\r\nB", body="C\rD")
    lf = canonicalize_offer_text(title="A\nB", body="C\nD")

    assert crlf == lf
    assert compute_offer_content_hash(crlf) == compute_offer_content_hash(lf)


def test_length_prefix_prevents_title_body_separator_collision() -> None:
    first = canonicalize_offer_text(title="a", body="bc")
    second = canonicalize_offer_text(title="ab", body="c")

    assert compute_offer_content_hash(first) != compute_offer_content_hash(second)


def test_unicode_is_not_normalized() -> None:
    composed = canonicalize_offer_text(title="\u00e9", body="matn")
    decomposed = canonicalize_offer_text(title="e\u0301", body="matn")

    assert composed.title != decomposed.title
    assert compute_offer_content_hash(composed) != compute_offer_content_hash(
        decomposed
    )


@pytest.mark.parametrize(
    ("title", "body", "message"),
    [
        ("", "body", "Offer title must not be empty"),
        (" \t\n", "body", "Offer title must not be empty"),
        ("title", "", "Offer body must not be empty"),
        ("title", "\r\n\t", "Offer body must not be empty"),
    ],
)
def test_empty_or_whitespace_only_content_is_rejected(
    title: str,
    body: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        canonicalize_offer_text(title=title, body=body)


def test_canonical_content_repr_never_contains_legal_text() -> None:
    content = CanonicalOfferText(
        title="SECRET LEGAL TITLE",
        body="SECRET LEGAL BODY",
    )

    rendered = repr(content)

    assert rendered == "CanonicalOfferText()"
    assert "SECRET" not in rendered
