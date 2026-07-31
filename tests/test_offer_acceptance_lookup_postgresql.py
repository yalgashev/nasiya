from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.offers.authorization import require_platform_admin_actor
from app.offers.commands import AcceptCurrentRegistrationOfferCommand
from app.offers.contracts import HasAcceptedCurrentRegistrationOffer
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.repository import (
    SqlAlchemyHasAcceptedCurrentRegistrationOffer,
)
from app.offers.service import (
    accept_current_registration_offer,
    approve_offer_version,
    create_offer_draft_version,
    make_offer_version_current,
    resolve_current_offer,
    upsert_offer_draft_text,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)


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
        saved = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=draft.id,
            language=language,
            title=f"{reference} {language.value} title",
            body=f"{reference} {language.value} body",
            now=NOW,
        )
        assert saved.succeeded
    approved = approve_offer_version(
        session,
        actor=actor,
        offer_version_id=draft.id,
        legal_review_authority="External Legal",
        legal_reviewed_at=NOW - timedelta(hours=1),
        legal_review_reference=reference,
        now=NOW,
    )
    assert approved.version is not None
    return approved.version


def test_lookup_accepts_any_legal_language_independent_of_later_ui_locale(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000969",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000970")
        actor = require_platform_admin_actor(admin)
        version = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-969",
        )
        current = make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=version.id,
            expected_current_version_id=None,
            now=NOW,
        )
        assert current.succeeded
        displayed = resolve_current_offer(
            session,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.RU,
        )
        assert displayed.offer is not None
        lookup = SqlAlchemyHasAcceptedCurrentRegistrationOffer(session)
        assert isinstance(lookup, HasAcceptedCurrentRegistrationOffer)
        assert lookup(user_id=account.id) is False

        accepted = accept_current_registration_offer(
            session,
            command=AcceptCurrentRegistrationOfferCommand(
                user_id=account.id,
                language=OfferLanguage.RU,
                displayed_offer_text_id=displayed.offer.text.id,
            ),
            now=NOW,
        )

        assert accepted.succeeded
        assert lookup(user_id=account.id) is True
        for later_ui_locale in ("uz", "ru"):
            assert later_ui_locale not in {language.value for language in OfferLanguage}
            assert lookup(user_id=account.id) is True


def test_old_version_acceptance_does_not_satisfy_new_current_lookup(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000971",
            is_platform_admin=True,
        )
        account = _user(session, phone="+998900000972")
        actor = require_platform_admin_actor(admin)
        first = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-971",
        )
        assert make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=first.id,
            expected_current_version_id=None,
            now=NOW,
        ).succeeded
        displayed = resolve_current_offer(
            session,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_CYRL,
        )
        assert displayed.offer is not None
        assert accept_current_registration_offer(
            session,
            command=AcceptCurrentRegistrationOfferCommand(
                user_id=account.id,
                language=OfferLanguage.UZ_CYRL,
                displayed_offer_text_id=displayed.offer.text.id,
            ),
            now=NOW,
        ).succeeded

        second = _approved(
            session,
            actor=actor,
            reference="LEGAL-2026-972",
        )
        assert make_offer_version_current(
            session,
            actor=actor,
            offer_version_id=second.id,
            expected_current_version_id=first.id,
            now=NOW,
        ).succeeded

        lookup = SqlAlchemyHasAcceptedCurrentRegistrationOffer(session)
        assert lookup(user_id=account.id) is False
