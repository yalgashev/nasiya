from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import tests.test_m11_registration_verify_postgresql as verify_tests
import tests.test_offer_acceptance_postgresql as acceptance_tests
from app.customer_activation.contracts import CustomerActivationActor
from app.customer_activation.service import select_current_registration_acceptance
from app.offers.commands import AcceptCurrentRegistrationOfferCommand
from app.offers.enums import OfferLanguage
from app.offers.models import OfferAcceptance
from app.offers.service import (
    AcceptCurrentRegistrationOfferOutcome,
    accept_current_registration_offer,
)

pytestmark = pytest.mark.integration


def test_offer_switch_and_issue_serialize_on_current_version_set(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    verify_tests.test_activation_locking_offer_first_then_switch_preserves_activation(
        monkeypatch,
        m2_test_database,
    )


def test_switch_first_invalidates_old_offer_snapshot(
    m2_test_database: Engine,
) -> None:
    verify_tests.test_offer_switch_committing_first_invalidates_old_snapshot_without_activation(
        m2_test_database
    )


def test_multiple_language_acceptance_race_keeps_deterministic_snapshot(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = acceptance_tests._user(
            session,
            phone="+998900001486",
            is_platform_admin=True,
        )
        account = acceptance_tests._user(session, phone="+998900001487")
        actor = acceptance_tests.require_platform_admin_actor(admin)
        version = acceptance_tests._approved(
            session,
            actor=actor,
            reference="M11-SYNTHETIC-LANGUAGE-RACE",
        )
        acceptance_tests._make_current(session, actor=actor, version=version)
        variants = tuple(
            (
                language,
                acceptance_tests._resolved(session, language).text.id,
            )
            for language in (OfferLanguage.UZ_LATN, OfferLanguage.RU)
        )
        user_id = account.id

    start = Barrier(len(variants))

    def accept(language: OfferLanguage, text_id: UUID) -> UUID:
        with Session(m2_test_database) as session, session.begin():
            start.wait(timeout=10)
            result = accept_current_registration_offer(
                session,
                command=AcceptCurrentRegistrationOfferCommand(
                    user_id=user_id,
                    language=language,
                    displayed_offer_text_id=text_id,
                ),
                now=acceptance_tests.NOW,
            )
            assert result.outcome is AcceptCurrentRegistrationOfferOutcome.CREATED
            assert result.acceptance is not None
            return result.acceptance.id

    with ThreadPoolExecutor(max_workers=len(variants)) as executor:
        futures = [executor.submit(accept, *variant) for variant in variants]
        accepted_ids = tuple(future.result(timeout=20) for future in futures)

    with Session(m2_test_database) as session, session.begin():
        rows = tuple(
            session.scalars(
                select(OfferAcceptance)
                .where(OfferAcceptance.user_id == user_id)
                .order_by(OfferAcceptance.accepted_at, OfferAcceptance.id)
            )
        )
        selection = select_current_registration_acceptance(
            session,
            actor=CustomerActivationActor(user_id),
        )
        expected_acceptance_id = rows[0].id

    assert len(set(accepted_ids)) == 2
    assert len(rows) == 2
    assert selection.acceptance_id_for_snapshot() == expected_acceptance_id
