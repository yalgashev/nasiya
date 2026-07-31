import inspect
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.read_models as offer_read_models
from app.auth.models import User
from app.offers.authorization import (
    PlatformAdminAuthorizationError,
    require_platform_admin_actor,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.read_models import (
    OfferTextDetail,
    OfferTextMetadata,
    OfferVersionDetail,
    OfferVersionListItem,
    get_offer_version_detail_for_admin,
    list_offer_versions_for_admin,
)
from app.offers.service import (
    approve_offer_version,
    create_offer_draft_version,
    upsert_offer_draft_text,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 23, 0, tzinfo=UTC)
CANARY_TITLE = "SECRET AUTHORIZED DETAIL TITLE"
CANARY_BODY = "SECRET AUTHORIZED DETAIL LEGAL BODY"


def _admin(session: Session) -> User:
    user = User(
        phone="+998900000943",
        password_hash=None,
        is_active=True,
        is_platform_admin=True,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _seed_versions(session: Session):
    actor = require_platform_admin_actor(_admin(session))
    partial = create_offer_draft_version(
        session,
        actor=actor,
        purpose=OfferPurpose.REGISTRATION,
        now=NOW,
    )
    partial_text = upsert_offer_draft_text(
        session,
        actor=actor,
        offer_version_id=partial.id,
        language=OfferLanguage.UZ_LATN,
        title=CANARY_TITLE,
        body=CANARY_BODY,
        now=NOW,
    )
    assert partial_text.succeeded

    approved = create_offer_draft_version(
        session,
        actor=actor,
        purpose=OfferPurpose.DEBT_ACCEPTANCE,
        now=NOW,
    )
    for language in OfferLanguage:
        result = upsert_offer_draft_text(
            session,
            actor=actor,
            offer_version_id=approved.id,
            language=language,
            title=f"{language.value} approved title",
            body=f"{language.value} approved body",
            now=NOW,
        )
        assert result.succeeded
    approval = approve_offer_version(
        session,
        actor=actor,
        offer_version_id=approved.id,
        legal_review_authority="Nasiya External Legal",
        legal_reviewed_at=NOW - timedelta(hours=1),
        legal_review_reference="LEGAL-2026-943",
        now=NOW,
    )
    assert approval.succeeded
    return actor, partial, approved


def test_admin_list_is_metadata_only_and_reports_language_completeness(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, partial, approved = _seed_versions(session)

        items = list_offer_versions_for_admin(session, actor=actor)

        assert all(isinstance(item, OfferVersionListItem) for item in items)
        assert [item.id for item in items] == [approved.id, partial.id]
        by_id = {item.id: item for item in items}
        partial_item = by_id[partial.id]
        assert partial_item.status is OfferStatus.DRAFT
        assert partial_item.complete is False
        assert partial_item.missing_languages == (
            OfferLanguage.UZ_CYRL,
            OfferLanguage.RU,
        )
        assert all(
            isinstance(text, OfferTextMetadata) for item in items for text in item.texts
        )
        assert not hasattr(partial_item, "title")
        assert not hasattr(partial_item, "body")
        assert not hasattr(partial_item.texts[0], "title")
        assert not hasattr(partial_item.texts[0], "body")
        assert CANARY_TITLE not in repr(items)
        assert CANARY_BODY not in repr(items)

        approved_item = by_id[approved.id]
        assert approved_item.complete is True
        assert approved_item.missing_languages == ()
        assert approved_item.legal_review_authority == "Nasiya External Legal"
        assert approved_item.legal_review_reference == "LEGAL-2026-943"
        assert approved_item.approved_by_user_id == actor.user_id
        assert approved_item.approved_at == NOW


def test_authorized_detail_returns_full_text_but_never_reprs_it(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, partial, _approved = _seed_versions(session)

        detail = get_offer_version_detail_for_admin(
            session,
            actor=actor,
            offer_version_id=partial.id,
        )

        assert isinstance(detail, OfferVersionDetail)
        assert detail.summary.id == partial.id
        assert len(detail.texts) == 1
        assert isinstance(detail.texts[0], OfferTextDetail)
        assert detail.texts[0].title == CANARY_TITLE
        assert detail.texts[0].body == CANARY_BODY
        assert CANARY_TITLE not in repr(detail)
        assert CANARY_BODY not in repr(detail)
        assert CANARY_TITLE not in repr(detail.texts[0])
        assert CANARY_BODY not in repr(detail.texts[0])


def test_detail_rechecks_admin_and_denies_stale_actor_before_body_load(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, partial, _approved = _seed_versions(session)
        partial_id = partial.id

    with Session(m2_test_database) as session, session.begin():
        admin = session.get(User, actor.user_id)
        assert admin is not None
        admin.is_platform_admin = False

    with Session(m2_test_database) as session, session.begin():
        with pytest.raises(
            PlatformAdminAuthorizationError,
            match="authorization failed",
        ):
            get_offer_version_detail_for_admin(
                session,
                actor=actor,
                offer_version_id=partial_id,
            )
        with pytest.raises(PlatformAdminAuthorizationError):
            list_offer_versions_for_admin(session, actor=actor)


def test_read_model_module_does_not_expose_orm_or_external_document_fields() -> None:
    source = inspect.getsource(offer_read_models)

    assert "app.offers.models" not in source
    assert "OfferVersionModel" not in source
    assert "OfferTextModel" not in source
    assert "document_url" not in source
    assert "object_key" not in source
    assert "external_url" not in source
