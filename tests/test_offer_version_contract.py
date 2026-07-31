from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.offers.contracts import (
    LegalReviewEvidence,
    OfferVersion,
    next_offer_version_number,
)
from app.offers.enums import OfferPurpose, OfferStatus

OFFER_ID = UUID("11111111-1111-4111-8111-111111111111")
CREATOR_ID = UUID("22222222-2222-4222-8222-222222222222")
APPROVER_ID = UUID("33333333-3333-4333-8333-333333333333")
CURRENT_ACTOR_ID = UUID("44444444-4444-4444-8444-444444444444")
CREATED_AT = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
APPROVED_AT = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
CURRENT_AT = datetime(2026, 7, 31, 11, 0, tzinfo=UTC)


def _review() -> LegalReviewEvidence:
    return LegalReviewEvidence(
        authority="Nasiya Legal",
        reviewed_at=REVIEWED_AT,
        reference="LEGAL-2026-001",
    )


def _approved_version(**overrides: object) -> OfferVersion:
    values: dict[str, object] = {
        "id": OFFER_ID,
        "purpose": OfferPurpose.REGISTRATION,
        "version_number": 1,
        "status": OfferStatus.APPROVED,
        "created_by_user_id": CREATOR_ID,
        "created_at": CREATED_AT,
        "legal_review": _review(),
        "approved_by_user_id": APPROVER_ID,
        "approved_at": APPROVED_AT,
    }
    values.update(overrides)
    return OfferVersion(**values)


def test_draft_offer_has_stable_identity_and_no_approval_or_current_data() -> None:
    version = OfferVersion(
        id=OFFER_ID,
        purpose=OfferPurpose.REGISTRATION,
        version_number=1,
        status=OfferStatus.DRAFT,
        created_by_user_id=CREATOR_ID,
        created_at=CREATED_AT,
    )

    assert version.id == OFFER_ID
    assert version.version_number == 1
    assert version.legal_review is None
    assert version.approved_at is None
    assert version.current_at is None


@pytest.mark.parametrize("version_number", [0, -1, True, 1.5])
def test_offer_version_number_must_be_positive_integer(
    version_number: object,
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        OfferVersion(
            id=OFFER_ID,
            purpose=OfferPurpose.REGISTRATION,
            version_number=version_number,
            status=OfferStatus.DRAFT,
            created_by_user_id=CREATOR_ID,
            created_at=CREATED_AT,
        )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("id", "not-a-uuid", "id must be a UUID"),
        (
            "created_by_user_id",
            "not-a-uuid",
            "created_by_user_id must be a UUID",
        ),
        ("purpose", "REGISTRATION", "Offer purpose is invalid"),
        ("status", "DRAFT", "Offer status is invalid"),
    ],
)
def test_offer_identity_fields_require_typed_values(
    field_name: str,
    value: object,
    message: str,
) -> None:
    fields: dict[str, object] = {
        "id": OFFER_ID,
        "purpose": OfferPurpose.REGISTRATION,
        "version_number": 1,
        "status": OfferStatus.DRAFT,
        "created_by_user_id": CREATOR_ID,
        "created_at": CREATED_AT,
    }
    fields[field_name] = value

    with pytest.raises(ValueError, match=message):
        OfferVersion(**fields)


def test_next_offer_version_number_is_positive_and_monotonic() -> None:
    assert next_offer_version_number(None) == 1
    assert next_offer_version_number(1) == 2
    assert next_offer_version_number(41) == 42

    with pytest.raises(ValueError, match="must be positive"):
        next_offer_version_number(0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"legal_review": _review()},
        {"approved_by_user_id": APPROVER_ID},
        {"approved_at": APPROVED_AT},
        {
            "legal_review": _review(),
            "approved_by_user_id": APPROVER_ID,
        },
    ],
)
def test_approval_metadata_is_all_or_none(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="approval metadata must be complete"):
        OfferVersion(
            id=OFFER_ID,
            purpose=OfferPurpose.REGISTRATION,
            version_number=1,
            status=OfferStatus.APPROVED,
            created_by_user_id=CREATOR_ID,
            created_at=CREATED_AT,
            **overrides,
        )


def test_draft_rejects_approval_evidence() -> None:
    with pytest.raises(ValueError, match="Draft offer must not have"):
        _approved_version(status=OfferStatus.DRAFT)


def test_approved_and_current_require_complete_approval_evidence() -> None:
    for status in (OfferStatus.APPROVED, OfferStatus.CURRENT):
        with pytest.raises(ValueError, match="requires approval evidence"):
            OfferVersion(
                id=OFFER_ID,
                purpose=OfferPurpose.REGISTRATION,
                version_number=1,
                status=status,
                created_by_user_id=CREATOR_ID,
                created_at=CREATED_AT,
            )


def test_current_requires_actor_and_timestamp() -> None:
    with pytest.raises(ValueError, match="requires current metadata"):
        _approved_version(status=OfferStatus.CURRENT)

    current = _approved_version(
        status=OfferStatus.CURRENT,
        current_by_user_id=CURRENT_ACTOR_ID,
        current_at=CURRENT_AT,
    )
    assert current.current_by_user_id == CURRENT_ACTOR_ID
    assert current.current_at == CURRENT_AT


def test_demoted_approved_version_may_retain_historical_current_metadata() -> None:
    version = _approved_version(
        current_by_user_id=CURRENT_ACTOR_ID,
        current_at=CURRENT_AT,
    )

    assert version.status is OfferStatus.APPROVED
    assert version.current_by_user_id == CURRENT_ACTOR_ID


def test_legal_review_contract_trims_authority_and_normalizes_utc() -> None:
    plus_five = timezone(timedelta(hours=5))
    evidence = LegalReviewEvidence(
        authority="  Nasiya Legal  ",
        reviewed_at=datetime(2026, 7, 31, 14, 0, tzinfo=plus_five),
        reference="LEGAL 2026.001",
    )

    assert evidence.authority == "Nasiya Legal"
    assert evidence.reviewed_at == REVIEWED_AT
    assert evidence.reviewed_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("authority", "reference", "message"),
    [
        ("", "LEGAL-1", "authority must be"),
        ("A" * 201, "LEGAL-1", "authority must be"),
        ("Legal\nTeam", "LEGAL-1", "control character"),
        ("Legal", "", "reference is invalid"),
        ("Legal", "-LEGAL", "reference is invalid"),
        ("Legal", "LEGAL/1", "reference is invalid"),
        ("Legal", "L" * 201, "reference is invalid"),
    ],
)
def test_legal_review_bounds_are_exact(
    authority: str,
    reference: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        LegalReviewEvidence(
            authority=authority,
            reviewed_at=REVIEWED_AT,
            reference=reference,
        )


def test_all_aggregate_timestamps_must_be_aware_and_ordered() -> None:
    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        OfferVersion(
            id=OFFER_ID,
            purpose=OfferPurpose.REGISTRATION,
            version_number=1,
            status=OfferStatus.DRAFT,
            created_by_user_id=CREATOR_ID,
            created_at=CREATED_AT.replace(tzinfo=None),
        )

    with pytest.raises(ValueError, match="reviewed_at must be timezone-aware"):
        LegalReviewEvidence(
            authority="Legal",
            reviewed_at=REVIEWED_AT.replace(tzinfo=None),
            reference="LEGAL-1",
        )

    with pytest.raises(ValueError, match="must not be after approval"):
        _approved_version(
            legal_review=LegalReviewEvidence(
                authority="Legal",
                reviewed_at=APPROVED_AT + timedelta(seconds=1),
                reference="LEGAL-1",
            )
        )

    with pytest.raises(ValueError, match="must not predate approval"):
        _approved_version(
            status=OfferStatus.CURRENT,
            current_by_user_id=CURRENT_ACTOR_ID,
            current_at=APPROVED_AT - timedelta(seconds=1),
        )


def test_aggregate_and_approval_evidence_are_immutable() -> None:
    version = _approved_version()

    with pytest.raises(FrozenInstanceError):
        version.status = OfferStatus.CURRENT
    with pytest.raises(FrozenInstanceError):
        version.legal_review.reference = "MUTATED"


def test_legal_review_repr_omits_authority_and_reference() -> None:
    evidence = _review()

    assert repr(evidence) == "LegalReviewEvidence()"
    assert "Nasiya Legal" not in repr(evidence)
    assert "LEGAL-2026-001" not in repr(evidence)
