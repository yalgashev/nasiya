from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.offers.authorization import require_platform_admin_actor
from app.offers.commands import AcceptCurrentRegistrationOfferCommand
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferAcceptance as OfferAcceptanceModel
from app.offers.models import OfferText as OfferTextModel
from app.offers.service import (
    accept_current_registration_offer,
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    resolve_current_offer,
    upsert_offer_draft_text,
    validate_current_registration_offer,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)


def _user(
    session: Session,
    *,
    phone: str,
    is_active: bool = True,
    is_platform_admin: bool = False,
) -> User:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=is_active,
        is_platform_admin=is_platform_admin,
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
        text = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=draft.id,
            language=language,
            title=f"{reference} {language.value} title",
            body=f"{reference} {language.value} body",
            now=NOW,
        )
        assert text.succeeded
    approved = approve_offer_version(
        session,
        actor=actor,
        offer_version_id=draft.id,
        legal_review_authority="Nasiya External Legal",
        legal_reviewed_at=NOW - timedelta(hours=1),
        legal_review_reference=reference,
        now=NOW,
    )
    assert approved.version is not None
    return approved.version


def _current_offer(session: Session, *, actor, approved, expected_id=None):
    result = make_offer_version_current(
        session,
        actor=actor,
        offer_version_id=approved.id,
        expected_current_version_id=expected_id,
        now=NOW,
    )
    assert result.succeeded
    resolved = resolve_current_offer(
        session,
        purpose=OfferPurpose.REGISTRATION,
        language=OfferLanguage.UZ_LATN,
    )
    assert resolved.offer is not None
    return resolved.offer


def _acceptance_audit_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.event_type == AuditEventType.OFFER_REGISTRATION_ACCEPTED.value
            )
        )
        or 0
    )


def test_exact_current_displayed_text_validates_without_writing_evidence(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000945",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000946")
        actor = require_platform_admin_actor(admin)
        current = _current_offer(
            session,
            actor=actor,
            approved=_approved(
                session,
                actor=actor,
                reference="LEGAL-2026-945",
            ),
        )
        command = AcceptCurrentRegistrationOfferCommand(
            user_id=account.id,
            language=OfferLanguage.UZ_LATN,
            displayed_offer_text_id=current.text.id,
            user_agent_source="Browser/1.0",
        )

        result = validate_current_registration_offer(session, command=command)

        assert result.succeeded is True
        assert result.offer == current
        assert result.offer.version.purpose is OfferPurpose.REGISTRATION
        assert result.offer.text.id == command.displayed_offer_text_id
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 0
        )
        assert _acceptance_audit_count(session) == 0


def test_no_current_or_missing_language_is_unavailable_without_write(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        account = _user(session, phone="+998900000947")
        current_text_id = account.id
        command = AcceptCurrentRegistrationOfferCommand(
            user_id=account.id,
            language=OfferLanguage.RU,
            displayed_offer_text_id=current_text_id,
        )

        result = validate_current_registration_offer(session, command=command)
        service_result = accept_current_registration_offer(
            session,
            command=command,
            now=NOW,
        )

        assert result.error is ErrorCode.OFFER_UNAVAILABLE
        assert service_result.error is ErrorCode.OFFER_UNAVAILABLE
        assert command.displayed_offer_text_id == current_text_id
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 0
        )
        assert _acceptance_audit_count(session) == 0


def test_wrong_language_displayed_text_is_offer_changed(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000948",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000949")
        actor = require_platform_admin_actor(admin)
        approved = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-948",
        )
        _current_offer(session, actor=actor, approved=approved)
        uz = resolve_current_offer(
            session,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_LATN,
        )
        assert uz.offer is not None

        command = AcceptCurrentRegistrationOfferCommand(
            user_id=account.id,
            language=OfferLanguage.RU,
            displayed_offer_text_id=uz.offer.text.id,
        )
        result = validate_current_registration_offer(session, command=command)
        service_result = accept_current_registration_offer(
            session,
            command=command,
            now=NOW,
        )

        assert result.error is ErrorCode.OFFER_CHANGED
        assert service_result.error is ErrorCode.OFFER_CHANGED
        assert _acceptance_audit_count(session) == 0


def test_get_post_current_switch_race_rejects_stale_displayed_text(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000950",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000951")
        actor = require_platform_admin_actor(admin)
        first = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-950",
        )
        first_current = _current_offer(session, actor=actor, approved=first)
        stale_text_id = first_current.text.id
        second = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-951",
        )
        _current_offer(
            session,
            actor=actor,
            approved=second,
            expected_id=first.id,
        )

        command = AcceptCurrentRegistrationOfferCommand(
            user_id=account.id,
            language=OfferLanguage.UZ_LATN,
            displayed_offer_text_id=stale_text_id,
        )
        stale = validate_current_registration_offer(session, command=command)
        service_result = accept_current_registration_offer(
            session,
            command=command,
            now=NOW,
        )

        assert stale.error is ErrorCode.OFFER_CHANGED
        assert service_result.error is ErrorCode.OFFER_CHANGED
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 0
        )
        assert _acceptance_audit_count(session) == 0


def test_hash_tampering_fails_closed_as_offer_changed(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000952",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000953")
        actor = require_platform_admin_actor(admin)
        current = _current_offer(
            session,
            actor=actor,
            approved=_approved(
                session,
                actor=actor,
                reference="LEGAL-2026-952",
            ),
        )
        persisted_text = session.get(OfferTextModel, current.text.id)
        assert persisted_text is not None
        persisted_text.content_hash = "0" * 64
        session.flush()

        command = AcceptCurrentRegistrationOfferCommand(
            user_id=account.id,
            language=OfferLanguage.UZ_LATN,
            displayed_offer_text_id=current.text.id,
        )
        result = validate_current_registration_offer(session, command=command)
        service_result = accept_current_registration_offer(
            session,
            command=command,
            now=NOW,
        )

        assert result.error is ErrorCode.OFFER_CHANGED
        assert service_result.error is ErrorCode.OFFER_CHANGED
        assert _acceptance_audit_count(session) == 0


def test_missing_or_inactive_authenticated_user_is_unauthorized(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        inactive = _user(
            session,
            phone="+998900000954",
            is_active=False,
        )
        result = validate_current_registration_offer(
            session,
            command=AcceptCurrentRegistrationOfferCommand(
                user_id=inactive.id,
                language=OfferLanguage.UZ_LATN,
                displayed_offer_text_id=inactive.id,
            ),
        )

        assert result.error is ErrorCode.UNAUTHORIZED
        assert _acceptance_audit_count(session) == 0
