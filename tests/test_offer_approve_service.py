from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.service as offer_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode, get_error_http_status
from app.auth.models import User
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    upsert_offer_draft_text,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 20, 0, tzinfo=UTC)
REVIEWED_AT = NOW - timedelta(hours=1)


def _admin(session: Session) -> User:
    user = User(
        phone="+998900000940",
        password_hash=None,
        is_active=True,
        is_platform_admin=True,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _complete_draft(session: Session):
    actor = require_platform_admin_actor(_admin(session))
    draft = create_offer_draft_version(
        session,
        actor=actor,
        purpose=OfferPurpose.REGISTRATION,
        now=NOW,
    )
    for language in OfferLanguage:
        result = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=draft.id,
            language=language,
            title=f"{language.value} synthetic title",
            body=f"{language.value} synthetic legal body",
            now=NOW,
        )
        assert result.succeeded
    return actor, draft


def _approve(session: Session, *, actor, version_id):
    return approve_offer_version(
        session,
        actor=actor,
        offer_version_id=version_id,
        legal_review_authority="Nasiya External Legal",
        legal_reviewed_at=REVIEWED_AT,
        legal_review_reference="LEGAL-2026-940",
        now=NOW,
    )


def test_complete_draft_requires_external_evidence_and_becomes_approved(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, draft = _complete_draft(session)

        result = _approve(session, actor=actor, version_id=draft.id)

        assert result.succeeded is True
        assert result.error is None
        assert result.version is not None
        assert result.version.status is OfferStatus.APPROVED
        assert result.version.approved_by_user_id == actor.user_id
        assert result.version.approved_at == NOW
        assert result.version.legal_review is not None
        assert result.version.legal_review.authority == "Nasiya External Legal"
        assert result.version.legal_review.reviewed_at == REVIEWED_AT
        assert result.version.legal_review.reference == "LEGAL-2026-940"

    with Session(m2_test_database) as session:
        persisted = session.get(OfferVersionModel, draft.id)
        assert persisted is not None
        assert persisted.status == OfferStatus.APPROVED.value
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.event_type == AuditEventType.OFFER_VERSION_APPROVED.value
            )
        )
        assert audit is not None
        assert audit.object_id == draft.id
        assert audit.payload == {
            "purpose": OfferPurpose.REGISTRATION.value,
            "version_number": 1,
            "from_status": OfferStatus.DRAFT.value,
            "to_status": OfferStatus.APPROVED.value,
            "legal_review_authority": "Nasiya External Legal",
            "legal_review_reference": "LEGAL-2026-940",
            "legal_reviewed_at": REVIEWED_AT.isoformat(),
        }
        assert "synthetic legal body" not in repr(audit)


def test_incomplete_draft_cannot_be_approved(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        draft = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        for language in (OfferLanguage.UZ_LATN, OfferLanguage.RU):
            upsert_offer_draft_text(
                session,
                actor=actor,
                offer_version_id=draft.id,
                language=language,
                title=f"{language.value} title",
                body=f"{language.value} body",
                now=NOW,
            )
        approval_audits_before = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == AuditEventType.OFFER_VERSION_APPROVED.value)
        )

        result = _approve(session, actor=actor, version_id=draft.id)

        assert result.succeeded is False
        assert result.error is ErrorCode.OFFER_INCOMPLETE
        assert get_error_http_status(result.error) == 422
        persisted = session.get(OfferVersionModel, draft.id)
        assert persisted.status == OfferStatus.DRAFT.value
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type == AuditEventType.OFFER_VERSION_APPROVED.value
                )
            )
            == approval_audits_before
        )


@pytest.mark.parametrize(
    (
        "authority",
        "reviewed_at",
        "reference",
    ),
    [
        (None, REVIEWED_AT, "LEGAL-1"),
        ("", REVIEWED_AT, "LEGAL-1"),
        ("Legal\nReviewer", REVIEWED_AT, "LEGAL-1"),
        ("L" * 201, REVIEWED_AT, "LEGAL-1"),
        ("Legal", None, "LEGAL-1"),
        ("Legal", NOW.replace(tzinfo=None), "LEGAL-1"),
        ("Legal", NOW + timedelta(seconds=1), "LEGAL-1"),
        ("Legal", REVIEWED_AT, None),
        ("Legal", REVIEWED_AT, ""),
        ("Legal", REVIEWED_AT, "https://evidence.invalid/1"),
        ("Legal", REVIEWED_AT, "R" * 201),
    ],
)
def test_missing_invalid_or_future_evidence_is_required_error(
    m2_test_database: Engine,
    authority: str | None,
    reviewed_at: datetime | None,
    reference: str | None,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, draft = _complete_draft(session)

        result = approve_offer_version(
            session,
            actor=actor,
            offer_version_id=draft.id,
            legal_review_authority=authority,
            legal_reviewed_at=reviewed_at,
            legal_review_reference=reference,
            now=NOW,
        )

        assert result.error is ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED
        assert get_error_http_status(result.error) == 422
        persisted = session.get(OfferVersionModel, draft.id)
        assert persisted.status == OfferStatus.DRAFT.value
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type == AuditEventType.OFFER_VERSION_APPROVED.value
                )
            )
            == 0
        )


def test_approved_version_cannot_be_approved_or_have_evidence_replaced(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, draft = _complete_draft(session)
        first = _approve(session, actor=actor, version_id=draft.id)
        assert first.version is not None
        original_evidence = first.version.legal_review

        replay = approve_offer_version(
            session,
            actor=actor,
            offer_version_id=draft.id,
            legal_review_authority="Replacement Authority",
            legal_reviewed_at=REVIEWED_AT,
            legal_review_reference="REPLACEMENT-1",
            now=NOW,
        )

        assert replay.error is ErrorCode.OFFER_NOT_DRAFT
        persisted = session.get(OfferVersionModel, draft.id)
        assert persisted.legal_review_authority == original_evidence.authority
        assert persisted.legal_review_reference == original_evidence.reference
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type == AuditEventType.OFFER_VERSION_APPROVED.value
                )
            )
            == 1
        )


def test_approval_audit_failure_rolls_back_transition_only(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, draft = _complete_draft(session)
        draft_id = draft.id

    original_append = offer_service.append_audit_event

    def fail_approval_audit(session, event) -> None:
        if event.event_type is AuditEventType.OFFER_VERSION_APPROVED:
            raise RuntimeError("approval audit unavailable")
        original_append(session, event)

    monkeypatch.setattr(offer_service, "append_audit_event", fail_approval_audit)
    with pytest.raises(RuntimeError, match="approval audit unavailable"):
        with Session(m2_test_database) as session, session.begin():
            _approve(session, actor=actor, version_id=draft_id)

    with Session(m2_test_database) as session:
        persisted = session.get(OfferVersionModel, draft_id)
        assert persisted is not None
        assert persisted.status == OfferStatus.DRAFT.value
        assert persisted.legal_review_authority is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(OfferTextModel)
                .where(OfferTextModel.offer_version_id == draft_id)
            )
            == 3
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type == AuditEventType.OFFER_VERSION_APPROVED.value
                )
            )
            == 0
        )
