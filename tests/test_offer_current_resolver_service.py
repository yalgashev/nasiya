import inspect
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.service as offer_service
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode, get_error_http_status
from app.auth.models import User
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.service import resolve_current_offer

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 22, 0, tzinfo=UTC)


def _user(session: Session) -> User:
    user = User(
        phone="+998900000942",
        password_hash=None,
        is_active=True,
        is_platform_admin=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _current(
    session: Session,
    *,
    actor_id,
    purpose: OfferPurpose,
    languages: tuple[OfferLanguage, ...] = tuple(OfferLanguage),
) -> OfferVersionModel:
    version = OfferVersionModel(
        purpose=purpose.value,
        version_number=1,
        status=OfferStatus.CURRENT.value,
        created_by_user_id=actor_id,
        created_at=NOW,
        legal_review_authority="Nasiya External Legal",
        legal_reviewed_at=NOW,
        legal_review_reference=f"LEGAL-{purpose.value}",
        approved_by_user_id=actor_id,
        approved_at=NOW,
        current_by_user_id=actor_id,
        current_at=NOW,
    )
    session.add(version)
    session.flush()
    for language in languages:
        canonical = canonicalize_offer_text(
            title=f"{purpose.value} {language.value} title",
            body=f"{purpose.value} {language.value} exact legal body",
        )
        session.add(
            OfferTextModel(
                offer_version_id=version.id,
                language=language.value,
                title=canonical.title,
                body=canonical.body,
                content_hash=compute_offer_content_hash(canonical),
                created_at=NOW,
                updated_at=NOW,
            )
        )
    session.flush()
    return version


@pytest.mark.parametrize(
    ("purpose", "language"),
    [(purpose, language) for purpose in OfferPurpose for language in OfferLanguage],
)
def test_resolver_returns_exact_current_purpose_language_variant(
    m2_test_database: Engine,
    purpose: OfferPurpose,
    language: OfferLanguage,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = _user(session)
        versions = {
            candidate_purpose: _current(
                session,
                actor_id=actor.id,
                purpose=candidate_purpose,
            )
            for candidate_purpose in OfferPurpose
        }

        result = resolve_current_offer(
            session,
            purpose=purpose,
            language=language,
        )

        assert result.succeeded is True
        assert result.error is None
        assert result.offer is not None
        assert result.offer.version.id == versions[purpose].id
        assert result.offer.version.purpose is purpose
        assert result.offer.version.status is OfferStatus.CURRENT
        assert result.offer.text.variant.language is language
        assert result.offer.text.variant.offer_version_id == versions[purpose].id
        assert purpose.value in result.offer.text.variant.body
        assert language.value in result.offer.text.variant.body
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_no_current_or_missing_requested_language_is_offer_unavailable(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        no_current = resolve_current_offer(
            session,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_LATN,
        )
        assert no_current.error is ErrorCode.OFFER_UNAVAILABLE
        assert get_error_http_status(no_current.error) == 409

        actor = _user(session)
        _current(
            session,
            actor_id=actor.id,
            purpose=OfferPurpose.REGISTRATION,
            languages=(OfferLanguage.UZ_LATN,),
        )
        missing_language = resolve_current_offer(
            session,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.RU,
        )

        assert missing_language.succeeded is False
        assert missing_language.error is ErrorCode.OFFER_UNAVAILABLE
        assert missing_language.offer is None
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_resolver_result_and_error_paths_do_not_repr_or_log_legal_body(
    m2_test_database: Engine,
) -> None:
    canary = "SECRET CURRENT LEGAL BODY"
    with Session(m2_test_database) as session, session.begin():
        actor = _user(session)
        version = _current(
            session,
            actor_id=actor.id,
            purpose=OfferPurpose.REGISTRATION,
            languages=(OfferLanguage.UZ_LATN,),
        )
        text = session.scalar(
            select(OfferTextModel).where(OfferTextModel.offer_version_id == version.id)
        )
        text.body = canary
        canonical = canonicalize_offer_text(title=text.title, body=text.body)
        text.content_hash = compute_offer_content_hash(canonical)
        session.flush()

        result = resolve_current_offer(
            session,
            purpose=OfferPurpose.REGISTRATION,
            language=OfferLanguage.UZ_LATN,
        )

        assert result.offer is not None
        assert result.offer.text.variant.body == canary
        assert canary not in repr(result)
        assert canary not in repr(result.offer)
        assert "logging" not in inspect.getsource(offer_service.resolve_current_offer)
        assert "logger" not in inspect.getsource(offer_service.resolve_current_offer)
