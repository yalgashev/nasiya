from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.offers.enums import OfferPurpose, OfferStatus
from app.offers.models import OfferVersion


def _check_constraints() -> dict[str, CheckConstraint]:
    return {
        constraint.name: constraint
        for constraint in OfferVersion.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_offer_versions_table_and_columns_are_exact() -> None:
    table = OfferVersion.__table__

    assert table.name == "offer_versions"
    assert tuple(table.columns.keys()) == (
        "id",
        "purpose",
        "version_number",
        "status",
        "created_by_user_id",
        "created_at",
        "legal_review_authority",
        "legal_reviewed_at",
        "legal_review_reference",
        "approved_by_user_id",
        "approved_at",
        "current_by_user_id",
        "current_at",
    )
    assert isinstance(table.c.id.type, PostgresUUID)
    assert table.c.id.type.as_uuid is True
    assert table.c.purpose.type.length == 32
    assert table.c.status.type.length == 16
    assert table.c.created_at.type.timezone is True
    assert table.c.legal_reviewed_at.type.timezone is True
    assert table.c.approved_at.type.timezone is True
    assert table.c.current_at.type.timezone is True


def test_offer_version_nullability_matches_lifecycle() -> None:
    table = OfferVersion.__table__
    required = {
        "id",
        "purpose",
        "version_number",
        "status",
        "created_by_user_id",
        "created_at",
    }

    assert {column.name for column in table.columns if not column.nullable} == required


def test_offer_version_foreign_keys_are_named_restrictive_user_links() -> None:
    table = OfferVersion.__table__
    expected = {
        "created_by_user_id": (
            "fk_offer_versions_created_by_user_id_users_id",
            "users.id",
        ),
        "approved_by_user_id": (
            "fk_offer_versions_approved_by_user_id_users_id",
            "users.id",
        ),
        "current_by_user_id": (
            "fk_offer_versions_current_by_user_id_users_id",
            "users.id",
        ),
    }

    for column_name, (constraint_name, target) in expected.items():
        foreign_key = next(iter(table.c[column_name].foreign_keys))
        assert foreign_key.constraint.name == constraint_name
        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == "RESTRICT"


def test_offer_version_unique_and_check_constraints_are_named_and_exact() -> None:
    table = OfferVersion.__table__
    unique = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert unique.name == "uq_offer_versions_purpose_version_number"
    assert tuple(column.name for column in unique.columns) == (
        "purpose",
        "version_number",
    )

    checks = _check_constraints()
    assert set(checks) == {
        "ck_offer_versions_purpose_allowed",
        "ck_offer_versions_version_number_positive",
        "ck_offer_versions_status_allowed",
        "ck_offer_versions_approval_evidence_matches_status",
        "ck_offer_versions_current_metadata_matches_status",
        "ck_offer_versions_legal_review_authority_valid",
        "ck_offer_versions_legal_review_reference_valid",
        "ck_offer_versions_timestamp_order",
    }
    purpose_sql = str(checks["ck_offer_versions_purpose_allowed"].sqltext)
    assert OfferPurpose.REGISTRATION.value in purpose_sql
    assert OfferPurpose.DEBT_ACCEPTANCE.value in purpose_sql
    status_sql = str(checks["ck_offer_versions_status_allowed"].sqltext)
    assert all(status.value in status_sql for status in OfferStatus)


def test_offer_version_current_index_is_partial_unique_per_purpose() -> None:
    index = next(
        index for index in OfferVersion.__table__.indexes if isinstance(index, Index)
    )

    assert index.name == "uq_offer_versions_current_purpose"
    assert index.unique is True
    assert tuple(column.name for column in index.columns) == ("purpose",)
    assert str(index.dialect_options["postgresql"]["where"]) == ("status = 'CURRENT'")


def test_offer_version_repr_omits_review_evidence_and_actor_ids() -> None:
    evidence_authority = "SECRET REVIEW AUTHORITY"
    evidence_reference = "SECRET-REFERENCE"
    model = OfferVersion(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        purpose=OfferPurpose.REGISTRATION.value,
        version_number=1,
        status=OfferStatus.APPROVED.value,
        created_by_user_id=UUID("22222222-2222-4222-8222-222222222222"),
        created_at=datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
        legal_review_authority=evidence_authority,
        legal_reviewed_at=datetime(2026, 7, 31, 9, 0, tzinfo=UTC),
        legal_review_reference=evidence_reference,
        approved_by_user_id=UUID("33333333-3333-4333-8333-333333333333"),
        approved_at=datetime(2026, 7, 31, 10, 0, tzinfo=UTC),
    )

    rendered = repr(model)

    assert evidence_authority not in rendered
    assert evidence_reference not in rendered
    assert "22222222" not in rendered
    assert "33333333" not in rendered
    assert "legal_review=<redacted>" in rendered
