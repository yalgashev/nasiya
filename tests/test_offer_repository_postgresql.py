import inspect
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    AuditWriter,
)
from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.models import User
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.contracts import (
    CurrentOfferResolver,
    LegalReviewEvidence,
    OfferAcceptanceRepository,
    OfferTextVariant,
    OfferVersionRepository,
    RegistrationOfferAcceptance,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance as OfferAcceptanceModel
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.repository import (
    SqlAlchemyCurrentOfferResolver,
    SqlAlchemyOfferAcceptanceRepository,
    SqlAlchemyOfferVersionRepository,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 15, 0, tzinfo=UTC)


def _user(session: Session, *, phone: str) -> User:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _variant(
    *,
    version_id,
    language: OfferLanguage = OfferLanguage.UZ_LATN,
    title: str = "Taklif",
    body: str = "Taklif matni",
) -> OfferTextVariant:
    canonical = canonicalize_offer_text(title=title, body=body)
    return OfferTextVariant(
        offer_version_id=version_id,
        language=language,
        title=canonical.title,
        body=canonical.body,
        content_hash=compute_offer_content_hash(canonical),
    )


def test_repository_adapters_persist_resolve_accept_and_audit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user = _user(session, phone="+998900000901")
        versions = SqlAlchemyOfferVersionRepository(session)
        resolver = SqlAlchemyCurrentOfferResolver(session)
        acceptances = SqlAlchemyOfferAcceptanceRepository(session)
        audit = SqlAlchemyAuditWriter(session)

        assert isinstance(versions, OfferVersionRepository)
        assert isinstance(resolver, CurrentOfferResolver)
        assert isinstance(acceptances, OfferAcceptanceRepository)
        assert isinstance(audit, AuditWriter)

        first = versions.create_draft(
            purpose=OfferPurpose.REGISTRATION,
            created_by_user_id=user.id,
            created_at=NOW,
        )
        second = versions.create_draft(
            purpose=OfferPurpose.REGISTRATION,
            created_by_user_id=user.id,
            created_at=NOW,
        )
        assert (first.version_number, second.version_number) == (1, 2)
        assert versions.get_version(version_id=first.id) == first
        assert versions.lock_version(version_id=first.id) == first
        assert versions.list_versions(purpose=OfferPurpose.REGISTRATION) == (
            first,
            second,
        )
        locked_versions = versions.lock_versions_for_purpose(
            purpose=OfferPurpose.REGISTRATION
        )
        assert {version.id for version in locked_versions} == {
            first.id,
            second.id,
        }
        assert tuple(version.id for version in locked_versions) == tuple(
            sorted((first.id, second.id))
        )

        stored_text = versions.save_draft_text(
            variant=_variant(version_id=first.id),
            now=NOW,
        )
        updated_text = versions.save_draft_text(
            variant=_variant(
                version_id=first.id,
                title="Yangilangan taklif",
                body="Yangilangan matn",
            ),
            now=NOW,
        )
        assert updated_text.id == stored_text.id
        assert (
            versions.get_text(
                version_id=first.id,
                language=OfferLanguage.UZ_LATN,
            )
            == updated_text
        )
        assert versions.list_texts(version_id=first.id) == (updated_text,)

        review = LegalReviewEvidence(
            authority="Nasiya Legal",
            reviewed_at=NOW,
            reference="LEGAL-2026-901",
        )
        approved = versions.save_lifecycle_state(
            version=replace(
                first,
                status=OfferStatus.APPROVED,
                legal_review=review,
                approved_by_user_id=user.id,
                approved_at=NOW,
            )
        )
        current = versions.save_lifecycle_state(
            version=replace(
                approved,
                status=OfferStatus.CURRENT,
                current_by_user_id=user.id,
                current_at=NOW,
            )
        )
        resolved = resolver.resolve_current(
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_LATN,
        )
        assert resolved is not None
        assert resolved.version == current
        assert resolved.text == updated_text
        locked_resolved = resolver.resolve_current_for_acceptance(
            language=OfferLanguage.UZ_LATN
        )
        assert locked_resolved == resolved

        acceptance = RegistrationOfferAcceptance(
            user_id=user.id,
            offer_version_id=current.id,
            offer_text_id=updated_text.id,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_LATN,
            version_number=current.version_number,
            content_hash=updated_text.variant.content_hash,
            accepted_at=NOW,
            user_agent="Repository Test Browser",
        )
        stored_acceptance = acceptances.create_acceptance(acceptance=acceptance)
        assert (
            acceptances.get_acceptance(
                user_id=user.id,
                offer_text_id=updated_text.id,
                purpose=OfferPurpose.REGISTRATION,
            )
            == stored_acceptance
        )

        audit.append(
            event=AuditEvent(
                event_type=AuditEventType.OFFER_REGISTRATION_ACCEPTED,
                actor_kind=AuditActorKind.USER,
                actor_user_id=user.id,
                object_type=AuditObjectType.OFFER_ACCEPTANCE,
                object_id=stored_acceptance.id,
                occurred_at=NOW,
                candidate_metadata={
                    "purpose": OfferPurpose.REGISTRATION,
                    "offer_version_id": current.id,
                    "offer_text_id": updated_text.id,
                    "version_number": current.version_number,
                    "language": OfferLanguage.UZ_LATN,
                    "content_hash": updated_text.variant.content_hash,
                    "body": "SECRET LEGAL BODY",
                },
            )
        )

    with Session(m2_test_database) as session:
        row = session.scalar(select(AuditLog))
        assert row is not None
        assert set(row.payload) == {
            "purpose",
            "offer_version_id",
            "offer_text_id",
            "version_number",
            "language",
            "content_hash",
        }
        assert "SECRET" not in repr(row.payload)
        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 2
        assert session.scalar(select(func.count()).select_from(OfferTextModel)) == 1
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 1
        )


def test_repository_and_audit_writes_follow_outer_rollback(
    m2_test_database: Engine,
) -> None:
    with pytest.raises(RuntimeError, match="force outer rollback"):
        with Session(m2_test_database) as session, session.begin():
            user = _user(session, phone="+998900000902")
            versions = SqlAlchemyOfferVersionRepository(session)
            draft = versions.create_draft(
                purpose=OfferPurpose.REGISTRATION,
                created_by_user_id=user.id,
                created_at=NOW,
            )
            SqlAlchemyAuditWriter(session).append(
                event=AuditEvent(
                    event_type=AuditEventType.OFFER_VERSION_CREATED,
                    actor_kind=AuditActorKind.USER,
                    actor_user_id=user.id,
                    object_type=AuditObjectType.OFFER_VERSION,
                    object_id=draft.id,
                    occurred_at=NOW,
                    candidate_metadata={
                        "purpose": OfferPurpose.REGISTRATION,
                        "version_number": draft.version_number,
                        "status": OfferStatus.DRAFT,
                    },
                )
            )
            raise RuntimeError("force outer rollback")

    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.phone == "+998900000902")
            )
            == 0
        )


def test_repository_sources_never_own_commit_full_rollback_or_close() -> None:
    source = "\n".join(
        (
            inspect.getsource(SqlAlchemyOfferVersionRepository),
            inspect.getsource(SqlAlchemyCurrentOfferResolver),
            inspect.getsource(SqlAlchemyOfferAcceptanceRepository),
            inspect.getsource(SqlAlchemyAuditWriter),
        )
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "begin_nested" in source
