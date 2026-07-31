from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.service as offer_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.offers.authorization import require_platform_admin_actor
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.service import (
    MakeOfferVersionCurrentOutcome,
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    upsert_offer_draft_text,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 21, 0, tzinfo=UTC)
SWITCHED_AT = NOW + timedelta(minutes=1)
_FUTURE_TIMEOUT_SECONDS = 20


def _admin(session: Session) -> User:
    user = User(
        phone="+998900000941",
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
        upsert = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=draft.id,
            language=language,
            title=f"{reference} {language.value} title",
            body=f"{reference} {language.value} synthetic body",
            now=NOW,
        )
        assert upsert.succeeded
    approval = approve_offer_version(
        session,
        actor=actor,
        offer_version_id=draft.id,
        legal_review_authority="Nasiya External Legal",
        legal_reviewed_at=NOW - timedelta(hours=1),
        legal_review_reference=reference,
        now=NOW,
    )
    assert approval.version is not None
    return approval.version


def _make_current(session: Session, *, actor, target_id, expected_id):
    return make_offer_version_current(
        session,
        actor=actor,
        offer_version_id=target_id,
        expected_current_version_id=expected_id,
        now=SWITCHED_AT,
    )


def test_current_replacement_demotes_old_and_correlates_two_audits(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        first = _approved(session, actor=actor, reference="LEGAL-2026-941")
        second = _approved(session, actor=actor, reference="LEGAL-2026-942")
        first_current = _make_current(
            session,
            actor=actor,
            target_id=first.id,
            expected_id=None,
        )
        assert first_current.outcome is MakeOfferVersionCurrentOutcome.SWITCHED
        switch_audit_count = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.event_type.in_(
                    (
                        AuditEventType.OFFER_VERSION_MADE_CURRENT.value,
                        AuditEventType.OFFER_VERSION_DEMOTED.value,
                    )
                )
            )
        )

        replacement = _make_current(
            session,
            actor=actor,
            target_id=second.id,
            expected_id=first.id,
        )

        assert replacement.succeeded is True
        assert replacement.outcome is MakeOfferVersionCurrentOutcome.SWITCHED
        assert replacement.previous_current_version_id == first.id
        assert replacement.version is not None
        assert replacement.version.status is OfferStatus.CURRENT
        assert replacement.version.current_by_user_id == actor.user_id
        assert replacement.version.current_at == SWITCHED_AT
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type.in_(
                        (
                            AuditEventType.OFFER_VERSION_MADE_CURRENT.value,
                            AuditEventType.OFFER_VERSION_DEMOTED.value,
                        )
                    )
                )
            )
            == switch_audit_count + 2
        )

    with Session(m2_test_database) as session:
        old = session.get(OfferVersionModel, first.id)
        current = session.get(OfferVersionModel, second.id)
        assert old is not None
        assert current is not None
        assert old.status == OfferStatus.APPROVED.value
        assert old.legal_review_reference == "LEGAL-2026-941"
        assert old.current_at == SWITCHED_AT
        assert current.status == OfferStatus.CURRENT.value
        assert current.legal_review_reference == "LEGAL-2026-942"
        correlated = tuple(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.occurred_at == SWITCHED_AT,
                    AuditLog.event_type.in_(
                        (
                            AuditEventType.OFFER_VERSION_MADE_CURRENT.value,
                            AuditEventType.OFFER_VERSION_DEMOTED.value,
                        )
                    ),
                )
            )
        )
        assert len(correlated) == 3
        demotion = next(
            audit
            for audit in correlated
            if audit.event_type == AuditEventType.OFFER_VERSION_DEMOTED.value
        )
        promotion = next(
            audit
            for audit in correlated
            if audit.object_id == second.id
            and audit.event_type == AuditEventType.OFFER_VERSION_MADE_CURRENT.value
        )
        assert demotion.object_id == first.id
        assert demotion.payload["replacement_version_id"] == str(second.id)
        assert promotion.payload["previous_current_version_id"] == str(first.id)


def test_already_current_is_audit_free_noop_even_if_expected_is_stale(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        approved = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-943",
        )
        first = _make_current(
            session,
            actor=actor,
            target_id=approved.id,
            expected_id=None,
        )
        assert first.version is not None
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))

        replay = _make_current(
            session,
            actor=actor,
            target_id=approved.id,
            expected_id=UUID("11111111-1111-4111-8111-111111111111"),
        )

        assert replay.outcome is MakeOfferVersionCurrentOutcome.ALREADY_CURRENT
        assert replay.version == first.version
        assert session.scalar(select(func.count()).select_from(AuditLog)) == (
            audit_count
        )


def test_stale_expected_current_rejects_without_mutation_or_audit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        first = _approved(session, actor=actor, reference="LEGAL-2026-944")
        second = _approved(session, actor=actor, reference="LEGAL-2026-945")
        _make_current(
            session,
            actor=actor,
            target_id=first.id,
            expected_id=None,
        )
        audit_count = session.scalar(select(func.count()).select_from(AuditLog))

        stale = _make_current(
            session,
            actor=actor,
            target_id=second.id,
            expected_id=None,
        )

        assert stale.error is ErrorCode.OFFER_CHANGED
        assert session.get(OfferVersionModel, first.id).status == (
            OfferStatus.CURRENT.value
        )
        assert session.get(OfferVersionModel, second.id).status == (
            OfferStatus.APPROVED.value
        )
        assert session.scalar(select(func.count()).select_from(AuditLog)) == (
            audit_count
        )


def test_draft_and_incomplete_approved_candidates_are_denied(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _admin(session)
        actor = require_platform_admin_actor(admin)
        draft = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        draft_result = _make_current(
            session,
            actor=actor,
            target_id=draft.id,
            expected_id=None,
        )
        assert draft_result.error is ErrorCode.OFFER_NOT_APPROVED

        incomplete = OfferVersionModel(
            purpose=OfferPurpose.DEBT_ACCEPTANCE.value,
            version_number=1,
            status=OfferStatus.APPROVED.value,
            created_by_user_id=admin.id,
            created_at=NOW,
            legal_review_authority="Nasiya External Legal",
            legal_reviewed_at=NOW,
            legal_review_reference="LEGAL-2026-946",
            approved_by_user_id=admin.id,
            approved_at=NOW,
        )
        session.add(incomplete)
        session.flush()
        incomplete_result = _make_current(
            session,
            actor=actor,
            target_id=incomplete.id,
            expected_id=None,
        )
        assert incomplete_result.error is ErrorCode.OFFER_INCOMPLETE
        assert incomplete.status == OfferStatus.APPROVED.value
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.object_id == incomplete.id,
                    AuditLog.event_type
                    == AuditEventType.OFFER_VERSION_MADE_CURRENT.value,
                )
            )
            == 0
        )


def test_parallel_stale_switches_have_one_winner_and_one_current(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        original = _approved(session, actor=actor, reference="LEGAL-2026-947")
        candidates = (
            _approved(session, actor=actor, reference="LEGAL-2026-948"),
            _approved(session, actor=actor, reference="LEGAL-2026-949"),
        )
        _make_current(
            session,
            actor=actor,
            target_id=original.id,
            expected_id=None,
        )
        original_id = original.id
        candidate_ids = tuple(candidate.id for candidate in candidates)

    barrier = Barrier(2)

    def switch(target_id: UUID):
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=10)
            return _make_current(
                session,
                actor=actor,
                target_id=target_id,
                expected_id=original_id,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(switch, target_id) for target_id in candidate_ids]
        results = [future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures]

    assert sum(result.succeeded for result in results) == 1
    assert [result.error for result in results].count(ErrorCode.OFFER_CHANGED) == 1
    with Session(m2_test_database) as session:
        current_ids = tuple(
            session.scalars(
                select(OfferVersionModel.id).where(
                    OfferVersionModel.purpose == OfferPurpose.REGISTRATION.value,
                    OfferVersionModel.status == OfferStatus.CURRENT.value,
                )
            )
        )
        assert len(current_ids) == 1
        assert current_ids[0] in candidate_ids
        assert session.get(OfferVersionModel, original_id).status == (
            OfferStatus.APPROVED.value
        )


def test_second_audit_failure_rolls_back_both_transitions_and_first_audit(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))
        first = _approved(session, actor=actor, reference="LEGAL-2026-950")
        second = _approved(session, actor=actor, reference="LEGAL-2026-951")
        _make_current(
            session,
            actor=actor,
            target_id=first.id,
            expected_id=None,
        )
        first_id = first.id
        second_id = second.id

    original_append = offer_service.append_audit_event

    def fail_promotion_audit(session, event) -> None:
        if event.event_type is AuditEventType.OFFER_VERSION_MADE_CURRENT:
            raise RuntimeError("promotion audit unavailable")
        original_append(session, event)

    monkeypatch.setattr(
        offer_service,
        "append_audit_event",
        fail_promotion_audit,
    )
    with pytest.raises(RuntimeError, match="promotion audit unavailable"):
        with Session(m2_test_database) as session, session.begin():
            _make_current(
                session,
                actor=actor,
                target_id=second_id,
                expected_id=first_id,
            )

    with Session(m2_test_database) as session:
        assert session.get(OfferVersionModel, first_id).status == (
            OfferStatus.CURRENT.value
        )
        assert session.get(OfferVersionModel, second_id).status == (
            OfferStatus.APPROVED.value
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type == AuditEventType.OFFER_VERSION_DEMOTED.value
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OfferTextModel)
                .where(OfferTextModel.offer_version_id.in_((first_id, second_id)))
            )
            == 6
        )
