from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.debt.values import DebtId
from app.offers.commands import AcceptCurrentDebtOfferCommand
from app.offers.content import canonicalize_offer_text, compute_offer_content_hash
from app.offers.contracts import (
    DebtOfferAcceptanceOutcome,
    DebtOfferAcceptanceResult,
    DebtOfferAcceptanceSnapshot,
    DebtOfferAcceptanceStaleError,
    LegalReviewEvidence,
    OfferTextVariant,
    OfferVersion,
    ResolvedCurrentOffer,
    StoredDebtOfferAcceptance,
    StoredOfferText,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
VERSION_ID = UUID("22222222-2222-4222-8222-222222222222")
TEXT_ID = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


def _current_debt_offer() -> ResolvedCurrentOffer:
    canonical = canonicalize_offer_text(
        title="Qarz ofertasi", body="Maxfiy bo‘lmagan matn"
    )
    version = OfferVersion(
        id=VERSION_ID,
        purpose=OfferPurpose.DEBT_ACCEPTANCE,
        version_number=3,
        status=OfferStatus.CURRENT,
        created_by_user_id=USER_ID,
        created_at=NOW - timedelta(days=2),
        legal_review=LegalReviewEvidence(
            authority="Legal",
            reviewed_at=NOW - timedelta(days=1, hours=1),
            reference="LEGAL-1",
        ),
        approved_by_user_id=USER_ID,
        approved_at=NOW - timedelta(days=1),
        current_by_user_id=USER_ID,
        current_at=NOW,
    )
    variant = OfferTextVariant(
        offer_version_id=VERSION_ID,
        language=OfferLanguage.UZ_LATN,
        title=canonical.title,
        body=canonical.body,
        content_hash=compute_offer_content_hash(canonical),
    )
    return ResolvedCurrentOffer(
        version=version, text=StoredOfferText(id=TEXT_ID, variant=variant)
    )


def test_debt_command_has_trusted_debt_id_and_redacts_identifiers_and_user_agent() -> (
    None
):
    command = AcceptCurrentDebtOfferCommand(
        user_id=USER_ID,
        debt_id=DebtId(uuid4()),
        language=OfferLanguage.UZ_LATN,
        displayed_offer_text_id=TEXT_ID,
        user_agent_source="RAW SECRET USER AGENT",
    )

    assert tuple(field.name for field in fields(command)) == (
        "user_id",
        "debt_id",
        "language",
        "displayed_offer_text_id",
        "user_agent_source",
    )
    assert "RAW SECRET" not in repr(command)
    assert str(command.debt_id.as_uuid()) not in repr(command)
    with pytest.raises(FrozenInstanceError):
        command.language = OfferLanguage.RU


def test_debt_acceptance_snapshots_exact_current_evidence_without_offer_body() -> None:
    resolved = _current_debt_offer()
    debt_id = DebtId(uuid4())
    snapshot = DebtOfferAcceptanceSnapshot.from_current_offer(
        user_id=USER_ID,
        debt_id=debt_id,
        resolved_offer=resolved,
        language=OfferLanguage.UZ_LATN,
        displayed_offer_text_id=TEXT_ID,
        accepted_at=NOW,
        user_agent="Browser 1.0",
    )

    assert snapshot.debt_id == debt_id
    assert snapshot.purpose is OfferPurpose.DEBT_ACCEPTANCE
    assert snapshot.offer_version_id == VERSION_ID
    assert snapshot.offer_text_id == TEXT_ID
    assert snapshot.language is OfferLanguage.UZ_LATN
    assert snapshot.version_number == 3
    assert snapshot.content_hash == resolved.text.variant.content_hash
    assert "Maxfiy bo‘lmagan matn" not in repr(snapshot)
    assert "Browser 1.0" not in repr(snapshot)
    assert str(debt_id.as_uuid()) not in repr(snapshot)


def test_debt_acceptance_requires_debt_purpose_and_has_stale_replay_results() -> None:
    resolved = _current_debt_offer()
    debt_id = DebtId(uuid4())
    with pytest.raises(DebtOfferAcceptanceStaleError, match="Debt offer changed"):
        DebtOfferAcceptanceSnapshot.from_current_offer(
            user_id=USER_ID,
            debt_id=debt_id,
            resolved_offer=resolved,
            language=OfferLanguage.UZ_LATN,
            displayed_offer_text_id=uuid4(),
            accepted_at=NOW,
            user_agent=None,
        )
    with pytest.raises(ValueError, match="purpose must be DEBT_ACCEPTANCE"):
        DebtOfferAcceptanceSnapshot(
            user_id=USER_ID,
            debt_id=debt_id,
            offer_version_id=VERSION_ID,
            offer_text_id=TEXT_ID,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_LATN,
            version_number=3,
            content_hash="a" * 64,
            accepted_at=NOW,
        )

    snapshot = DebtOfferAcceptanceSnapshot.from_current_offer(
        user_id=USER_ID,
        debt_id=debt_id,
        resolved_offer=resolved,
        language=OfferLanguage.UZ_LATN,
        displayed_offer_text_id=TEXT_ID,
        accepted_at=NOW,
        user_agent=None,
    )
    stored = StoredDebtOfferAcceptance(id=uuid4(), acceptance=snapshot)
    assert (
        DebtOfferAcceptanceResult.accepted(stored).outcome
        is DebtOfferAcceptanceOutcome.ACCEPTED
    )
    assert DebtOfferAcceptanceResult.replay(stored).is_replay
    assert DebtOfferAcceptanceResult.stale().outcome is DebtOfferAcceptanceOutcome.STALE
