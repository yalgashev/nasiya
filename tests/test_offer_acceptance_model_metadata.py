from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferAcceptance


def _checks() -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in OfferAcceptance.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_offer_acceptance_columns_are_complete_immutable_snapshot_shape() -> None:
    table = OfferAcceptance.__table__

    assert table.name == "offer_acceptances"
    assert tuple(table.columns.keys()) == (
        "id",
        "user_id",
        "offer_version_id",
        "offer_text_id",
        "purpose",
        "language",
        "version_number",
        "content_hash",
        "accepted_at",
        "user_agent",
    )
    assert {column.name for column in table.columns if not column.nullable} == {
        "id",
        "user_id",
        "offer_version_id",
        "offer_text_id",
        "purpose",
        "language",
        "version_number",
        "content_hash",
        "accepted_at",
    }
    assert all(
        isinstance(table.c[column_name].type, PostgresUUID)
        for column_name in ("id", "user_id", "offer_version_id", "offer_text_id")
    )
    assert table.c.accepted_at.type.timezone is True
    assert table.c.user_agent.type.length == 512


def test_acceptance_foreign_keys_are_named_and_restrictive() -> None:
    expected = {
        "user_id": (
            "fk_offer_acceptances_user_id_users_id",
            "users.id",
        ),
        "offer_version_id": (
            "fk_offer_acceptances_offer_version_id_offer_versions_id",
            "offer_versions.id",
        ),
        "offer_text_id": (
            "fk_offer_acceptances_offer_text_id_offer_texts_id",
            "offer_texts.id",
        ),
    }

    for column_name, (constraint_name, target) in expected.items():
        foreign_key = next(iter(OfferAcceptance.__table__.c[column_name].foreign_keys))
        assert foreign_key.constraint.name == constraint_name
        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == "RESTRICT"


def test_acceptance_replay_unique_constraint_is_exact() -> None:
    unique = next(
        constraint
        for constraint in OfferAcceptance.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    )

    assert unique.name == "uq_offer_acceptances_user_id_offer_text_id_purpose"
    assert tuple(column.name for column in unique.columns) == (
        "user_id",
        "offer_text_id",
        "purpose",
    )


def test_acceptance_snapshot_checks_are_named_and_closed() -> None:
    checks = _checks()

    assert set(checks) == {
        "ck_offer_acceptances_purpose_allowed",
        "ck_offer_acceptances_language_allowed",
        "ck_offer_acceptances_version_number_positive",
        "ck_offer_acceptances_content_hash_sha256_hex",
        "ck_offer_acceptances_user_agent_normalized",
    }
    purpose_sql = str(checks["ck_offer_acceptances_purpose_allowed"].sqltext)
    assert all(purpose.value in purpose_sql for purpose in OfferPurpose)
    language_sql = str(checks["ck_offer_acceptances_language_allowed"].sqltext)
    assert all(language.value in language_sql for language in OfferLanguage)
    assert "[0-9a-f]{64}" in str(
        checks["ck_offer_acceptances_content_hash_sha256_hex"].sqltext
    )


def test_acceptance_repr_redacts_user_and_user_agent() -> None:
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    raw_user_agent = "SECRET RAW USER AGENT"
    model = OfferAcceptance(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        user_id=user_id,
        offer_version_id=UUID("33333333-3333-4333-8333-333333333333"),
        offer_text_id=UUID("44444444-4444-4444-8444-444444444444"),
        purpose=OfferPurpose.REGISTRATION.value,
        language=OfferLanguage.UZ_LATN.value,
        version_number=1,
        content_hash="a" * 64,
        accepted_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        user_agent=raw_user_agent,
    )

    rendered = repr(model)

    assert str(user_id) not in rendered
    assert raw_user_agent not in rendered
    assert "user_id=<redacted>" in rendered
    assert "user_agent=<redacted>" in rendered
