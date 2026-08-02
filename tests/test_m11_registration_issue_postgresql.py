from datetime import datetime, timedelta
from inspect import signature

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer_activation.contracts import RegistrationReadinessSnapshot
from app.otp.contracts import OtpChallengeEventAction, OtpChallengeStatus, OtpPurpose
from app.otp.crypto import OtpBrowserBindingDigest
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import (
    OtpChallengeInsertConflict,
    activate_challenge,
    consume_registration_challenge,
    create_pending_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
    invalidate_registration_challenge_for_state_change,
    lock_outstanding_challenge_set_by_user,
    lock_registration_candidate_set_by_browser,
    record_registration_failed_attempt,
    supersede_and_cancel_same_purpose_challenges,
)
from tests.m11_seed import NOW, REGISTRATION_DIGEST, seed_registration_snapshot

pytestmark = pytest.mark.integration
LOGIN_DIGEST = OtpBrowserBindingDigest("b" * 64)


def create_registration_with_dispatch(
    session: Session,
    *,
    snapshot: RegistrationReadinessSnapshot,
    now: datetime,
) -> tuple[OtpChallenge, OtpDispatch]:
    challenge = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=now,
    )
    dispatch = create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=now,
    )
    return challenge, dispatch


def test_registration_create_lock_and_same_purpose_supersede_are_isolated(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001321",
        )
        registration, registration_dispatch = create_registration_with_dispatch(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        login = create_pending_challenge(
            session,
            browser_binding_digest=LOGIN_DIGEST,
            now=NOW,
            purpose=OtpPurpose.LOGIN,
            user_id=snapshot.user_id,
            telegram_link_id=snapshot.telegram_link_id,
            telegram_linked_at=snapshot.telegram_linked_at,
        )
        login_dispatch = create_pending_dispatch(
            session,
            challenge_id=login.id,
            locale="uz-Latn",
            now=NOW,
        )

        locked = lock_outstanding_challenge_set_by_user(
            session,
            user_id=snapshot.user_id,
            purpose=OtpPurpose.REGISTRATION,
        )
        candidate = lock_registration_candidate_set_by_browser(
            session,
            browser_binding_digest=REGISTRATION_DIGEST,
        )

        assert locked.challenges == candidate.challenges == (registration,)
        assert locked.dispatches == candidate.dispatches == (registration_dispatch,)
        assert (
            supersede_and_cancel_same_purpose_challenges(
                session,
                locked=locked,
                purpose=OtpPurpose.REGISTRATION,
                now=NOW + timedelta(seconds=1),
            )
            == 1
        )
        assert registration.status == OtpChallengeStatus.SUPERSEDED.value
        assert registration_dispatch.status == "CANCELLED"
        assert login.status == OtpChallengeStatus.PENDING_DISPATCH.value
        assert login_dispatch.status == "PENDING"
        event_actions = [
            event.action for event in session.scalars(select(OtpChallengeEvent))
        ]
        assert event_actions == [OtpChallengeEventAction.SUPERSEDED.value]


def test_registration_state_failed_attempt_burn_and_consume_events_are_exact(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001322",
        )
        stale, stale_dispatch = create_registration_with_dispatch(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        invalidate_registration_challenge_for_state_change(
            session,
            challenge=stale,
            dispatch=stale_dispatch,
            now=NOW + timedelta(seconds=1),
        )

        burned = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW + timedelta(seconds=2),
        )
        activate_challenge(
            session,
            challenge=burned,
            code_mac="e" * 64,
            activated_at=NOW + timedelta(seconds=3),
            expires_at=NOW + timedelta(minutes=3),
        )
        record_registration_failed_attempt(
            session,
            challenge=burned,
            now=NOW + timedelta(seconds=4),
            max_attempts=1,
        )

        consumed = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW + timedelta(seconds=5),
        )
        activate_challenge(
            session,
            challenge=consumed,
            code_mac="f" * 64,
            activated_at=NOW + timedelta(seconds=6),
            expires_at=NOW + timedelta(minutes=3),
        )
        consume_registration_challenge(
            session,
            challenge=consumed,
            now=NOW + timedelta(seconds=7),
        )

        assert stale.status == OtpChallengeStatus.INVALIDATED.value
        assert stale_dispatch.status == "CANCELLED"
        assert burned.status == OtpChallengeStatus.BURNED.value
        assert burned.failed_attempts == 1
        assert consumed.status == OtpChallengeStatus.CONSUMED.value
        assert [
            event.action
            for event in session.scalars(
                select(OtpChallengeEvent).order_by(OtpChallengeEvent.occurred_at)
            )
        ] == [
            OtpChallengeEventAction.INVALIDATED_BY_REGISTRATION_STATE_CHANGE.value,
            OtpChallengeEventAction.VERIFY_FAILED.value,
            OtpChallengeEventAction.BURNED.value,
            OtpChallengeEventAction.CONSUMED.value,
        ]


def test_registration_context_validation_and_expected_conflict_keep_session_usable(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001323",
        )
        with pytest.raises(ValueError, match="registration context"):
            create_pending_challenge(
                session,
                browser_binding_digest=LOGIN_DIGEST,
                now=NOW,
                purpose=OtpPurpose.LOGIN,
                customer_id=snapshot.customer_id,
            )
        with pytest.raises(ValueError, match="real identity snapshot"):
            create_pending_challenge(
                session,
                browser_binding_digest=REGISTRATION_DIGEST,
                now=NOW,
                purpose=OtpPurpose.REGISTRATION,
            )

        create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        conflicting_snapshot = RegistrationReadinessSnapshot(
            user_id=snapshot.user_id,
            customer_id=snapshot.customer_id,
            telegram_link_id=snapshot.telegram_link_id,
            telegram_linked_at=snapshot.telegram_linked_at,
            registration_offer_acceptance_id=(
                snapshot.registration_offer_acceptance_id
            ),
            customer_identity_revision=snapshot.customer_identity_revision,
            customer_document_id=snapshot.customer_document_id,
            browser_binding_digest=OtpBrowserBindingDigest("9" * 64),
        )
        with pytest.raises(OtpChallengeInsertConflict):
            create_pending_registration_challenge(
                session,
                snapshot=conflicting_snapshot,
                now=NOW + timedelta(seconds=1),
            )

        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(OtpChallenge)) == 1
        assert (
            "challenge_id"
            not in signature(lock_registration_candidate_set_by_browser).parameters
        )
        assert (
            "purpose"
            not in signature(lock_registration_candidate_set_by_browser).parameters
        )


def test_runtime_lock_trace_is_dispatch_then_challenge(
    m2_test_database: Engine,
) -> None:
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(m2_test_database, "before_cursor_execute", capture_statement)
    try:
        with Session(m2_test_database) as session, session.begin():
            snapshot = seed_registration_snapshot(
                session,
                phone="+998900001324",
            )
            create_registration_with_dispatch(
                session,
                snapshot=snapshot,
                now=NOW,
            )
            session.flush()
            statements.clear()

            lock_registration_candidate_set_by_browser(
                session,
                browser_binding_digest=REGISTRATION_DIGEST,
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_statement)

    row_locks = [statement for statement in statements if "FOR UPDATE" in statement]
    dispatch_position = next(
        index
        for index, statement in enumerate(row_locks)
        if "otp_dispatches" in statement
    )
    challenge_position = next(
        index
        for index, statement in enumerate(row_locks)
        if "otp_challenges" in statement
    )
    assert dispatch_position < challenge_position
