import inspect
from datetime import UTC, datetime
from uuid import uuid4

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
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.service import (
    create_offer_draft_version,
    upsert_offer_draft_text,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 19, 0, tzinfo=UTC)


def _admin(session: Session) -> User:
    user = User(
        phone="+998900000939",
        password_hash=None,
        is_active=True,
        is_platform_admin=True,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def test_draft_text_insert_and_update_are_canonical_hashed_and_safely_audited(
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

        inserted = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=draft.id,
            language=OfferLanguage.UZ_LATN,
            title="Birinchi\r\nsarlavha",
            body="Birinchi\rmatn",
            now=NOW,
        )
        updated = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=draft.id,
            language=OfferLanguage.UZ_LATN,
            title="Yangilangan sarlavha",
            body="Yangilangan yuridik matn",
            now=NOW,
        )

        assert inserted.succeeded is True
        assert updated.succeeded is True
        assert inserted.text is not None
        assert updated.text is not None
        assert updated.text.id == inserted.text.id

    expected = canonicalize_offer_text(
        title="Yangilangan sarlavha",
        body="Yangilangan yuridik matn",
    )
    with Session(m2_test_database) as session:
        persisted = session.scalar(select(OfferTextModel))
        assert persisted is not None
        assert persisted.title == expected.title
        assert persisted.body == expected.body
        assert persisted.content_hash == compute_offer_content_hash(expected)
        text_audits = tuple(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.event_type == AuditEventType.OFFER_TEXT_UPDATED.value
                )
            )
        )
        assert len(text_audits) == 2
        assert all(
            set(audit.payload)
            == {"purpose", "version_number", "language", "content_hash"}
            for audit in text_audits
        )
        assert all("title" not in audit.payload for audit in text_audits)
        assert all("body" not in audit.payload for audit in text_audits)
        assert "Yangilangan yuridik matn" not in repr(text_audits)


@pytest.mark.parametrize("status", [OfferStatus.APPROVED, OfferStatus.CURRENT])
def test_approved_or_current_text_cannot_be_mutated(
    m2_test_database: Engine,
    status: OfferStatus,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _admin(session)
        actor = require_platform_admin_actor(admin)
        version = OfferVersionModel(
            purpose=OfferPurpose.REGISTRATION.value,
            version_number=1,
            status=status.value,
            created_by_user_id=admin.id,
            created_at=NOW,
            legal_review_authority="Nasiya Legal",
            legal_reviewed_at=NOW,
            legal_review_reference="LEGAL-2026-939",
            approved_by_user_id=admin.id,
            approved_at=NOW,
            current_by_user_id=admin.id if status is OfferStatus.CURRENT else None,
            current_at=NOW if status is OfferStatus.CURRENT else None,
        )
        session.add(version)
        session.flush()

        result = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=version.id,
            language=OfferLanguage.RU,
            title="Mutatsiya qilinmasin",
            body="MAXFIY YANGI YURIDIK MATN",
            now=NOW,
        )

        assert result.succeeded is False
        assert result.error is ErrorCode.OFFER_NOT_DRAFT
        assert result.text is None
        assert session.scalar(select(func.count()).select_from(OfferTextModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_missing_version_is_safe_not_draft_denial(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))

        result = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=uuid4(),
            language=OfferLanguage.UZ_CYRL,
            title="Сарлавҳа",
            body="Юридик матн",
            now=NOW,
        )

        assert result.error is ErrorCode.OFFER_NOT_DRAFT
        assert get_error_http_status(result.error) == 409
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("", "body"),
        ("title", ""),
        ("   ", "body"),
        ("title", "\t"),
    ],
)
def test_empty_text_is_rejected_before_persistence_or_audit(
    m2_test_database: Engine,
    title: str,
    body: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        draft = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))

        with pytest.raises(ValueError, match="must not be empty"):
            upsert_offer_draft_text(
                session,
                actor=actor,
                offer_version_id=draft.id,
                language=OfferLanguage.UZ_LATN,
                title=title,
                body=body,
                now=NOW,
            )

        assert session.scalar(select(func.count()).select_from(OfferTextModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == (
            audit_count
        )


def test_text_service_has_no_transaction_owner_or_content_logging() -> None:
    source = inspect.getsource(offer_service.upsert_offer_draft_text)

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "logging" not in source
    assert "logger" not in source
