from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.service as offer_service
from app.audit.contracts import AuditEvent, AuditEventType
from app.audit.models import AuditLog
from app.audit.repository import append_audit_event
from app.auth.models import User
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    upsert_offer_draft_text,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
CANARIES = {
    "title": "SECRET LEGAL TITLE CANARY",
    "body": "SECRET LEGAL BODY CANARY",
    "user_agent": "SECRET RAW USER AGENT",
    "secret": "SECRET INTERNAL VALUE",
    "session_id": "SECRET SESSION ID",
    "cookie": "SECRET COOKIE",
    "csrf": "SECRET CSRF",
    "url": "https://secret.invalid/legal-document",
}
EXPECTED_PAYLOAD_KEYS = {
    AuditEventType.OFFER_VERSION_CREATED.value: {
        "purpose",
        "version_number",
        "status",
    },
    AuditEventType.OFFER_TEXT_UPDATED.value: {
        "purpose",
        "version_number",
        "language",
        "content_hash",
    },
    AuditEventType.OFFER_VERSION_APPROVED.value: {
        "purpose",
        "version_number",
        "from_status",
        "to_status",
        "legal_review_authority",
        "legal_review_reference",
        "legal_reviewed_at",
    },
    AuditEventType.OFFER_VERSION_MADE_CURRENT.value: {
        "purpose",
        "version_number",
        "from_status",
        "to_status",
        "previous_current_version_id",
    },
    AuditEventType.OFFER_VERSION_DEMOTED.value: {
        "purpose",
        "version_number",
        "from_status",
        "to_status",
        "replacement_version_id",
    },
}


def _admin(session: Session) -> User:
    user = User(
        phone="+998900000944",
        password_hash=None,
        is_active=True,
        is_platform_admin=True,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _approved(session: Session, *, actor, reference: str):
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
            title=f"{CANARIES['title']} {reference} {language.value}",
            body=f"{CANARIES['body']} {reference} {language.value}",
            now=NOW,
        )
        assert result.succeeded
    result = approve_offer_version(
        session,
        actor=actor,
        offer_version_id=draft.id,
        legal_review_authority="Nasiya External Legal",
        legal_reviewed_at=NOW - timedelta(hours=1),
        legal_review_reference=reference,
        now=NOW,
    )
    assert result.version is not None
    return result.version


def test_lifecycle_events_use_central_redaction_and_exact_safe_payloads(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def append_contaminated(session: Session, event: AuditEvent) -> None:
        append_audit_event(
            session,
            replace(
                event,
                candidate_metadata={
                    **event.candidate_metadata,
                    **CANARIES,
                },
            ),
        )

    monkeypatch.setattr(
        offer_service,
        "append_audit_event",
        append_contaminated,
    )
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        first = _approved(session, actor=actor, reference="LEGAL-2026-944")
        second = _approved(session, actor=actor, reference="LEGAL-2026-945")
        initial = make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=first.id,
            expected_current_version_id=None,
            now=NOW,
        )
        assert initial.succeeded
        replacement = make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=second.id,
            expected_current_version_id=first.id,
            now=NOW,
        )
        assert replacement.succeeded

    with Session(m2_test_database) as session:
        audits = tuple(session.scalars(select(AuditLog)))
        event_counts = {
            event_type: sum(audit.event_type == event_type for audit in audits)
            for event_type in EXPECTED_PAYLOAD_KEYS
        }
        assert event_counts == {
            AuditEventType.OFFER_VERSION_CREATED.value: 2,
            AuditEventType.OFFER_TEXT_UPDATED.value: 6,
            AuditEventType.OFFER_VERSION_APPROVED.value: 2,
            AuditEventType.OFFER_VERSION_MADE_CURRENT.value: 2,
            AuditEventType.OFFER_VERSION_DEMOTED.value: 1,
        }
        for audit in audits:
            assert set(audit.payload) == EXPECTED_PAYLOAD_KEYS[audit.event_type]
            serialized = repr(audit.payload)
            assert all(canary not in serialized for canary in CANARIES.values())
        rendered = repr(audits)
        assert all(canary not in rendered for canary in CANARIES.values())


def test_created_event_failure_rolls_back_draft(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))

    def fail_created(_session: Session, event: AuditEvent) -> None:
        assert event.event_type is AuditEventType.OFFER_VERSION_CREATED
        raise RuntimeError("created audit unavailable")

    monkeypatch.setattr(offer_service, "append_audit_event", fail_created)
    with pytest.raises(RuntimeError, match="created audit unavailable"):
        with Session(m2_test_database) as session, session.begin():
            create_offer_draft_version(
                session,
                actor=actor,
                purpose=OfferPurpose.REGISTRATION,
                now=NOW,
            )

    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_text_updated_event_failure_rolls_back_legal_content(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        draft = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        draft_id = draft.id

    def fail_text(_session: Session, event: AuditEvent) -> None:
        assert event.event_type is AuditEventType.OFFER_TEXT_UPDATED
        raise RuntimeError("text audit unavailable")

    monkeypatch.setattr(offer_service, "append_audit_event", fail_text)
    with pytest.raises(RuntimeError, match="text audit unavailable"):
        with Session(m2_test_database) as session, session.begin():
            upsert_offer_draft_text(
                session,
                actor=actor,
                offer_version_id=draft_id,
                language=OfferLanguage.UZ_LATN,
                title=CANARIES["title"],
                body=CANARIES["body"],
                now=NOW,
            )

    with Session(m2_test_database) as session:
        assert session.get(OfferVersionModel, draft_id) is not None
        assert session.scalar(select(func.count()).select_from(OfferTextModel)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == AuditEventType.OFFER_TEXT_UPDATED.value)
            )
            == 0
        )
