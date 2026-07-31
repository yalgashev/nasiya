from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.service as offer_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.user_agent import MAX_USER_AGENT_LENGTH
from app.offers.authorization import require_platform_admin_actor
from app.offers.commands import AcceptCurrentRegistrationOfferCommand
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferAcceptance as OfferAcceptanceModel
from app.offers.service import (
    AcceptCurrentRegistrationOfferOutcome,
    accept_current_registration_offer,
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    resolve_current_offer,
    upsert_offer_draft_text,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
_BARRIER_TIMEOUT_SECONDS = 10
_FUTURE_TIMEOUT_SECONDS = 20


def _user(
    session: Session,
    *,
    phone: str,
    is_platform_admin: bool = False,
) -> User:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
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
        result = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=draft.id,
            language=language,
            title=f"{reference} {language.value} title",
            body=f"{reference} {language.value} exact legal body",
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


def _make_current(session: Session, *, actor, version, expected_id=None) -> None:
    result = make_offer_version_current(
        session,
        actor=actor,
        offer_version_id=version.id,
        expected_current_version_id=expected_id,
        now=NOW,
    )
    assert result.succeeded


def _resolved(session: Session, language: OfferLanguage):
    result = resolve_current_offer(
        session,
        purpose=OfferPurpose.REGISTRATION,
        language=language,
    )
    assert result.offer is not None
    return result.offer


def test_acceptance_persists_exact_server_current_snapshot_and_safe_audit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000955",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000956")
        actor = require_platform_admin_actor(admin)
        version = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-955",
        )
        _make_current(session, actor=actor, version=version)
        displayed = _resolved(session, OfferLanguage.RU)

        result = accept_current_registration_offer(
            session,
            command=AcceptCurrentRegistrationOfferCommand(
                user_id=account.id,
                language=OfferLanguage.RU,
                displayed_offer_text_id=displayed.text.id,
                user_agent_source="Raw Browser/1.0",
            ),
            now=NOW,
        )

        assert result.succeeded is True
        assert result.outcome is AcceptCurrentRegistrationOfferOutcome.CREATED
        assert result.acceptance is not None
        evidence = result.acceptance.acceptance
        assert evidence.user_id == account.id
        assert evidence.offer_version_id == displayed.version.id
        assert evidence.offer_text_id == displayed.text.id
        assert evidence.purpose is OfferPurpose.REGISTRATION
        assert evidence.language is OfferLanguage.RU
        assert evidence.version_number == displayed.version.version_number
        assert evidence.content_hash == displayed.text.variant.content_hash
        assert evidence.accepted_at == NOW
        assert evidence.user_agent == "Raw Browser/1.0"

    with Session(m2_test_database) as session:
        persisted = session.scalar(select(OfferAcceptanceModel))
        assert persisted is not None
        assert persisted.offer_version_id == displayed.version.id
        assert persisted.offer_text_id == displayed.text.id
        assert persisted.language == OfferLanguage.RU.value
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.event_type == AuditEventType.OFFER_REGISTRATION_ACCEPTED.value
            )
        )
        assert audit is not None
        assert audit.object_id == persisted.id
        assert audit.payload == {
            "purpose": OfferPurpose.REGISTRATION.value,
            "offer_version_id": str(displayed.version.id),
            "offer_text_id": str(displayed.text.id),
            "version_number": displayed.version.version_number,
            "language": OfferLanguage.RU.value,
            "content_hash": displayed.text.variant.content_hash,
        }
        assert "exact legal body" not in repr(audit)
        assert "Raw Browser" not in repr(audit)
        assert "+998900000956" not in repr(audit)


def test_later_current_switch_keeps_old_acceptance_immutable(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000957",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000958")
        actor = require_platform_admin_actor(admin)
        first = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-957",
        )
        _make_current(session, actor=actor, version=first)
        displayed = _resolved(session, OfferLanguage.UZ_CYRL)
        accepted = accept_current_registration_offer(
            session,
            command=AcceptCurrentRegistrationOfferCommand(
                user_id=account.id,
                language=OfferLanguage.UZ_CYRL,
                displayed_offer_text_id=displayed.text.id,
            ),
            now=NOW,
        )
        assert accepted.acceptance is not None
        acceptance_id = accepted.acceptance.id
        snapshot = accepted.acceptance.acceptance

        second = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-958",
        )
        _make_current(
            session,
            actor=actor,
            version=second,
            expected_id=first.id,
        )
        session.flush()

        persisted = session.get(OfferAcceptanceModel, acceptance_id)
        assert persisted is not None
        assert persisted.offer_version_id == snapshot.offer_version_id
        assert persisted.offer_text_id == snapshot.offer_text_id
        assert persisted.language == snapshot.language.value
        assert persisted.version_number == snapshot.version_number
        assert persisted.content_hash == snapshot.content_hash
        assert persisted.accepted_at == snapshot.accepted_at


def test_acceptance_and_audit_follow_caller_outer_rollback(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000959",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000960")
        actor = require_platform_admin_actor(admin)
        version = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-959",
        )
        _make_current(session, actor=actor, version=version)
        displayed = _resolved(session, OfferLanguage.UZ_LATN)
        account_id = account.id
        text_id = displayed.text.id

    with pytest.raises(RuntimeError, match="force caller rollback"):
        with Session(m2_test_database) as session, session.begin():
            result = accept_current_registration_offer(
                session,
                command=AcceptCurrentRegistrationOfferCommand(
                    user_id=account_id,
                    language=OfferLanguage.UZ_LATN,
                    displayed_offer_text_id=text_id,
                ),
                now=NOW,
            )
            assert result.succeeded
            raise RuntimeError("force caller rollback")

    with Session(m2_test_database) as session:
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.OFFER_REGISTRATION_ACCEPTED.value
                )
            )
            == 0
        )


def test_sequential_acceptance_replay_returns_original_without_new_rows(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000961",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000962")
        actor = require_platform_admin_actor(admin)
        version = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-961",
        )
        _make_current(session, actor=actor, version=version)
        displayed = _resolved(session, OfferLanguage.UZ_LATN)
        command = AcceptCurrentRegistrationOfferCommand(
            user_id=account.id,
            language=OfferLanguage.UZ_LATN,
            displayed_offer_text_id=displayed.text.id,
        )

        created = accept_current_registration_offer(
            session,
            command=command,
            now=NOW,
        )
        replayed = accept_current_registration_offer(
            session,
            command=command,
            now=NOW + timedelta(minutes=5),
        )

        assert created.outcome is AcceptCurrentRegistrationOfferOutcome.CREATED
        assert replayed.outcome is AcceptCurrentRegistrationOfferOutcome.REPLAYED
        assert created.acceptance is not None
        assert replayed.acceptance == created.acceptance
        assert replayed.acceptance.acceptance.accepted_at == NOW
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.OFFER_REGISTRATION_ACCEPTED.value
                )
            )
            == 1
        )


def test_concurrent_acceptance_replay_returns_one_acceptance_and_one_audit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000963",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000964")
        actor = require_platform_admin_actor(admin)
        version = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-963",
        )
        _make_current(session, actor=actor, version=version)
        displayed = _resolved(session, OfferLanguage.RU)
        user_id = account.id
        text_id = displayed.text.id

    barrier = Barrier(2)

    def accept() -> tuple[AcceptCurrentRegistrationOfferOutcome, object]:
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            result = accept_current_registration_offer(
                session,
                command=AcceptCurrentRegistrationOfferCommand(
                    user_id=user_id,
                    language=OfferLanguage.RU,
                    displayed_offer_text_id=text_id,
                ),
                now=NOW,
            )
            assert result.succeeded
            assert result.outcome is not None
            assert result.acceptance is not None
            return result.outcome, result.acceptance.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(accept) for _index in range(2)]
        outcomes = [
            future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures
        ]

    assert sorted(outcome.value for outcome, _row_id in outcomes) == [
        AcceptCurrentRegistrationOfferOutcome.CREATED.value,
        AcceptCurrentRegistrationOfferOutcome.REPLAYED.value,
    ]
    assert len({row_id for _outcome, row_id in outcomes}) == 1
    with Session(m2_test_database) as session:
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.OFFER_REGISTRATION_ACCEPTED.value
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    ("phone_suffix", "raw_user_agent", "expected"),
    [
        ("965", "", None),
        ("966", "A" * MAX_USER_AGENT_LENGTH, "A" * MAX_USER_AGENT_LENGTH),
        (
            "967",
            "B" * (MAX_USER_AGENT_LENGTH + 1),
            "B" * MAX_USER_AGENT_LENGTH,
        ),
        ("968", " Browser\x00\u200b\n\t Test ", "Browser Test"),
    ],
)
def test_acceptance_persists_only_normalized_bounded_user_agent(
    m2_test_database: Engine,
    phone_suffix: str,
    raw_user_agent: str,
    expected: str | None,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone=f"+998900000{phone_suffix}",
            is_platform_admin=True,
        )
        account = _user(session, phone=f"+998910000{phone_suffix}")
        actor = require_platform_admin_actor(admin)
        version = _approved(
            session,
            actor=actor,
            reference=f"LEGAL-2026-{phone_suffix}",
        )
        _make_current(session, actor=actor, version=version)
        displayed = _resolved(session, OfferLanguage.UZ_CYRL)

        result = accept_current_registration_offer(
            session,
            command=AcceptCurrentRegistrationOfferCommand(
                user_id=account.id,
                language=OfferLanguage.UZ_CYRL,
                displayed_offer_text_id=displayed.text.id,
                user_agent_source=raw_user_agent,
            ),
            now=NOW,
        )

        assert result.acceptance is not None
        assert result.acceptance.acceptance.user_agent == expected
        persisted = session.get(
            OfferAcceptanceModel,
            result.acceptance.id,
        )
        assert persisted is not None
        assert persisted.user_agent == expected


def test_audit_append_failure_is_rolled_back_with_acceptance_by_caller(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000973",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000974")
        actor = require_platform_admin_actor(admin)
        version = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-973",
        )
        _make_current(session, actor=actor, version=version)
        displayed = _resolved(session, OfferLanguage.UZ_LATN)
        user_id = account.id
        text_id = displayed.text.id

    def fail_audit(*_args, **_kwargs) -> None:
        raise RuntimeError("safe audit failure")

    monkeypatch.setattr(offer_service, "append_audit_event", fail_audit)

    with pytest.raises(RuntimeError, match="safe audit failure"):
        with Session(m2_test_database) as session, session.begin():
            accept_current_registration_offer(
                session,
                command=AcceptCurrentRegistrationOfferCommand(
                    user_id=user_id,
                    language=OfferLanguage.UZ_LATN,
                    displayed_offer_text_id=text_id,
                ),
                now=NOW,
            )

    with Session(m2_test_database) as session:
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.OFFER_REGISTRATION_ACCEPTED.value
                )
            )
            == 0
        )


def test_debt_current_cannot_be_accepted_through_registration_boundary(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000975",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000976")
        actor = require_platform_admin_actor(admin)
        draft = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.DEBT_ACCEPTANCE,
            now=NOW,
        )
        displayed_text_id = None
        for language in OfferLanguage:
            text = upsert_offer_draft_text(
                session,
                actor=actor,
                offer_version_id=draft.id,
                language=language,
                title=f"Debt {language.value} title",
                body=f"Debt {language.value} body",
                now=NOW,
            )
            assert text.text is not None
            if language is OfferLanguage.UZ_LATN:
                displayed_text_id = text.text.id
        approved = approve_offer_version(
            session,
            actor=actor,
            offer_version_id=draft.id,
            legal_review_authority="External Legal",
            legal_reviewed_at=NOW - timedelta(hours=1),
            legal_review_reference="LEGAL-DEBT-975",
            now=NOW,
        )
        assert approved.version is not None
        _make_current(session, actor=actor, version=approved.version)
        assert displayed_text_id is not None

        result = accept_current_registration_offer(
            session,
            command=AcceptCurrentRegistrationOfferCommand(
                user_id=account.id,
                language=OfferLanguage.UZ_LATN,
                displayed_offer_text_id=displayed_text_id,
            ),
            now=NOW,
        )

        assert result.error is ErrorCode.OFFER_UNAVAILABLE
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 0
        )


def test_acceptance_and_current_switch_race_never_crosses_offer_snapshot(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000977",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000978")
        actor = require_platform_admin_actor(admin)
        first = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-977",
        )
        _make_current(session, actor=actor, version=first)
        displayed = _resolved(session, OfferLanguage.RU)
        second = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-978",
        )
        user_id = account.id
        old_text_id = displayed.text.id
        old_hash = displayed.text.variant.content_hash

    barrier = Barrier(2)

    def accept():
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            return accept_current_registration_offer(
                session,
                command=AcceptCurrentRegistrationOfferCommand(
                    user_id=user_id,
                    language=OfferLanguage.RU,
                    displayed_offer_text_id=old_text_id,
                ),
                now=NOW,
            )

    def switch():
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            return make_offer_version_current(
                session,
                actor=actor,
                offer_version_id=second.id,
                expected_current_version_id=first.id,
                now=NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        acceptance_future = executor.submit(accept)
        switch_future = executor.submit(switch)
        acceptance_result = acceptance_future.result(timeout=_FUTURE_TIMEOUT_SECONDS)
        switch_result = switch_future.result(timeout=_FUTURE_TIMEOUT_SECONDS)

    assert switch_result.succeeded
    with Session(m2_test_database) as session:
        current = _resolved(session, OfferLanguage.RU)
        assert current.version.id == second.id
        rows = tuple(session.scalars(select(OfferAcceptanceModel)))
        if acceptance_result.succeeded:
            assert acceptance_result.acceptance is not None
            assert acceptance_result.acceptance.acceptance.offer_version_id == first.id
            assert acceptance_result.acceptance.acceptance.offer_text_id == old_text_id
            assert acceptance_result.acceptance.acceptance.content_hash == old_hash
            assert len(rows) == 1
            assert rows[0].offer_version_id == first.id
            assert rows[0].offer_text_id == old_text_id
        else:
            assert acceptance_result.error is ErrorCode.OFFER_CHANGED
            assert rows == ()
