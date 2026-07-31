from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.offers.enums import OfferLanguage
from app.offers.models import OfferText


def _checks() -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in OfferText.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_offer_texts_columns_store_exact_legal_content_evidence() -> None:
    table = OfferText.__table__

    assert table.name == "offer_texts"
    assert tuple(table.columns.keys()) == (
        "id",
        "offer_version_id",
        "language",
        "title",
        "body",
        "content_hash",
        "created_at",
        "updated_at",
    )
    assert all(not column.nullable for column in table.columns)
    assert isinstance(table.c.id.type, PostgresUUID)
    assert isinstance(table.c.offer_version_id.type, PostgresUUID)
    assert isinstance(table.c.title.type, Text)
    assert isinstance(table.c.body.type, Text)
    assert table.c.language.type.length == 16
    assert table.c.content_hash.type.length == 64
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True


def test_offer_text_version_foreign_key_is_named_and_restrictive() -> None:
    foreign_key = next(iter(OfferText.__table__.c.offer_version_id.foreign_keys))

    assert (
        foreign_key.constraint.name
        == "fk_offer_texts_offer_version_id_offer_versions_id"
    )
    assert foreign_key.target_fullname == "offer_versions.id"
    assert foreign_key.ondelete == "RESTRICT"


def test_offer_text_unique_language_constraint_is_exact() -> None:
    unique = next(
        constraint
        for constraint in OfferText.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    )

    assert unique.name == "uq_offer_texts_offer_version_id_language"
    assert tuple(column.name for column in unique.columns) == (
        "offer_version_id",
        "language",
    )


def test_offer_text_checks_are_named_and_cover_language_hash_content_time() -> None:
    checks = _checks()

    assert set(checks) == {
        "ck_offer_texts_language_allowed",
        "ck_offer_texts_content_canonical",
        "ck_offer_texts_content_hash_sha256_hex",
        "ck_offer_texts_timestamp_order",
    }
    language_sql = str(checks["ck_offer_texts_language_allowed"].sqltext)
    assert all(language.value in language_sql for language in OfferLanguage)
    assert "[0-9a-f]{64}" in str(
        checks["ck_offer_texts_content_hash_sha256_hex"].sqltext
    )
    canonical_sql = str(checks["ck_offer_texts_content_canonical"].sqltext)
    assert "chr(13)" in canonical_sql
    assert "btrim(title)" in canonical_sql
    assert "btrim(body)" in canonical_sql


def test_offer_text_repr_redacts_full_title_and_body() -> None:
    model = OfferText(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        offer_version_id=UUID("22222222-2222-4222-8222-222222222222"),
        language=OfferLanguage.RU.value,
        title="SECRET LEGAL TITLE",
        body="SECRET LEGAL BODY",
        content_hash="a" * 64,
        created_at=datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
    )

    rendered = repr(model)

    assert "SECRET" not in rendered
    assert "title=<redacted>" in rendered
    assert "body=<redacted>" in rendered
