from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.offers.authorization import (
    PlatformAdminActor,
    assert_platform_admin_actor,
)
from app.offers.contracts import OfferVersion, StoredOfferText
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.repository import SqlAlchemyOfferVersionRepository


@dataclass(frozen=True, slots=True)
class OfferTextMetadata:
    id: UUID
    language: OfferLanguage
    content_hash: str


@dataclass(frozen=True, slots=True)
class OfferVersionListItem:
    id: UUID
    purpose: OfferPurpose
    version_number: int
    status: OfferStatus
    texts: tuple[OfferTextMetadata, ...]
    missing_languages: tuple[OfferLanguage, ...]
    created_by_user_id: UUID
    created_at: datetime
    legal_review_authority: str | None
    legal_reviewed_at: datetime | None
    legal_review_reference: str | None
    approved_by_user_id: UUID | None
    approved_at: datetime | None
    current_by_user_id: UUID | None
    current_at: datetime | None

    @property
    def complete(self) -> bool:
        return not self.missing_languages


@dataclass(frozen=True, slots=True)
class OfferTextDetail:
    id: UUID
    language: OfferLanguage
    content_hash: str
    title: str = field(repr=False)
    body: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class OfferVersionDetail:
    summary: OfferVersionListItem
    texts: tuple[OfferTextDetail, ...] = field(repr=False)


def list_offer_versions_for_admin(
    session: Session,
    *,
    actor: PlatformAdminActor,
    purpose: OfferPurpose | None = None,
) -> tuple[OfferVersionListItem, ...]:
    assert_platform_admin_actor(session, actor)
    repository = SqlAlchemyOfferVersionRepository(session)
    return tuple(
        _to_list_item(
            version,
            repository.list_texts(version_id=version.id),
        )
        for version in repository.list_versions(purpose=purpose)
    )


def get_offer_version_detail_for_admin(
    session: Session,
    *,
    actor: PlatformAdminActor,
    offer_version_id: UUID,
) -> OfferVersionDetail | None:
    assert_platform_admin_actor(session, actor)
    repository = SqlAlchemyOfferVersionRepository(session)
    version = repository.get_version(version_id=offer_version_id)
    if version is None:
        return None
    texts = repository.list_texts(version_id=version.id)
    return OfferVersionDetail(
        summary=_to_list_item(version, texts),
        texts=tuple(
            OfferTextDetail(
                id=text.id,
                language=text.variant.language,
                content_hash=text.variant.content_hash,
                title=text.variant.title,
                body=text.variant.body,
            )
            for text in texts
        ),
    )


def _to_list_item(
    version: OfferVersion,
    texts: tuple[StoredOfferText, ...],
) -> OfferVersionListItem:
    text_metadata = tuple(
        OfferTextMetadata(
            id=text.id,
            language=text.variant.language,
            content_hash=text.variant.content_hash,
        )
        for text in texts
    )
    present_languages = {text.language for text in text_metadata}
    missing_languages = tuple(
        language for language in OfferLanguage if language not in present_languages
    )
    review = version.legal_review
    return OfferVersionListItem(
        id=version.id,
        purpose=version.purpose,
        version_number=version.version_number,
        status=version.status,
        texts=text_metadata,
        missing_languages=missing_languages,
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at,
        legal_review_authority=(None if review is None else review.authority),
        legal_reviewed_at=(None if review is None else review.reviewed_at),
        legal_review_reference=(None if review is None else review.reference),
        approved_by_user_id=version.approved_by_user_id,
        approved_at=version.approved_at,
        current_by_user_id=version.current_by_user_id,
        current_at=version.current_at,
    )
