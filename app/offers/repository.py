from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.offers.contracts import (
    DebtOfferAcceptanceSnapshot,
    LegalReviewEvidence,
    OfferTextVariant,
    OfferVersion,
    RegistrationOfferAcceptance,
    ResolvedCurrentOffer,
    StoredDebtOfferAcceptance,
    StoredOfferText,
    StoredRegistrationOfferAcceptance,
    next_offer_version_number,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance as OfferAcceptanceModel
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel

_VERSION_NUMBER_CONSTRAINT = "uq_offer_versions_purpose_version_number"
_TEXT_LANGUAGE_CONSTRAINT = "uq_offer_texts_offer_version_id_language"
_ACCEPTANCE_REPLAY_CONSTRAINT = "uq_offer_acceptances_user_id_offer_text_id_purpose"
_DEBT_ACCEPTANCE_CONSTRAINT = "uq_offer_acceptances_debt_id"


class OfferVersionAllocationConflict(RuntimeError):
    pass


class OfferTextInsertConflict(RuntimeError):
    pass


class OfferAcceptanceInsertConflict(RuntimeError):
    pass


class SqlAlchemyOfferVersionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_draft(
        self,
        *,
        purpose: OfferPurpose,
        created_by_user_id: UUID,
        created_at: datetime,
    ) -> OfferVersion:
        rows = self._lock_version_rows_for_purpose(purpose)
        current_max = max(
            (row.version_number for row in rows),
            default=None,
        )
        model = OfferVersionModel(
            purpose=purpose.value,
            version_number=next_offer_version_number(current_max),
            status=OfferStatus.DRAFT.value,
            created_by_user_id=created_by_user_id,
            created_at=_as_utc(created_at),
        )
        try:
            with self._session.begin_nested():
                self._session.add(model)
                self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == _VERSION_NUMBER_CONSTRAINT:
                raise OfferVersionAllocationConflict(
                    "Offer version allocation conflicted"
                ) from None
            raise
        return _to_domain_version(model)

    def list_versions(
        self,
        *,
        purpose: OfferPurpose | None = None,
    ) -> tuple[OfferVersion, ...]:
        statement = select(OfferVersionModel)
        if purpose is not None:
            statement = statement.where(OfferVersionModel.purpose == purpose.value)
        statement = statement.order_by(
            OfferVersionModel.purpose,
            OfferVersionModel.version_number,
            OfferVersionModel.id,
        )
        return tuple(
            _to_domain_version(model) for model in self._session.scalars(statement)
        )

    def get_version(self, *, version_id: UUID) -> OfferVersion | None:
        model = self._session.get(OfferVersionModel, version_id)
        return None if model is None else _to_domain_version(model)

    def lock_version(self, *, version_id: UUID) -> OfferVersion | None:
        statement = (
            select(OfferVersionModel)
            .where(OfferVersionModel.id == version_id)
            .with_for_update()
        )
        model = self._session.scalar(statement)
        return None if model is None else _to_domain_version(model)

    def lock_versions_for_purpose(
        self,
        *,
        purpose: OfferPurpose,
    ) -> tuple[OfferVersion, ...]:
        return tuple(
            _to_domain_version(model)
            for model in self._lock_version_rows_for_purpose(purpose)
        )

    def list_texts(
        self,
        *,
        version_id: UUID,
    ) -> tuple[StoredOfferText, ...]:
        statement = (
            select(OfferTextModel)
            .where(OfferTextModel.offer_version_id == version_id)
            .order_by(OfferTextModel.language, OfferTextModel.id)
        )
        return tuple(
            _to_stored_text(model) for model in self._session.scalars(statement)
        )

    def get_text(
        self,
        *,
        version_id: UUID,
        language: OfferLanguage,
    ) -> StoredOfferText | None:
        statement = select(OfferTextModel).where(
            OfferTextModel.offer_version_id == version_id,
            OfferTextModel.language == language.value,
        )
        model = self._session.scalar(statement)
        return None if model is None else _to_stored_text(model)

    def save_draft_text(
        self,
        *,
        variant: OfferTextVariant,
        now: datetime,
    ) -> StoredOfferText:
        statement = select(OfferTextModel).where(
            OfferTextModel.offer_version_id == variant.offer_version_id,
            OfferTextModel.language == variant.language.value,
        )
        model = self._session.scalar(statement)
        current_time = _as_utc(now)
        if model is None:
            model = OfferTextModel(
                offer_version_id=variant.offer_version_id,
                language=variant.language.value,
                title=variant.title,
                body=variant.body,
                content_hash=variant.content_hash,
                created_at=current_time,
                updated_at=current_time,
            )
            try:
                with self._session.begin_nested():
                    self._session.add(model)
                    self._session.flush()
            except IntegrityError as exc:
                if _constraint_name(exc) == _TEXT_LANGUAGE_CONSTRAINT:
                    raise OfferTextInsertConflict(
                        "Offer text language insert conflicted"
                    ) from None
                raise
        else:
            model.title = variant.title
            model.body = variant.body
            model.content_hash = variant.content_hash
            model.updated_at = current_time
            self._session.flush()
        return _to_stored_text(model)

    def save_lifecycle_state(self, *, version: OfferVersion) -> OfferVersion:
        statement = (
            select(OfferVersionModel)
            .where(OfferVersionModel.id == version.id)
            .with_for_update()
        )
        model = self._session.scalar(statement)
        if model is None:
            raise ValueError("Offer version does not exist")
        model.status = version.status.value
        if version.legal_review is None:
            model.legal_review_authority = None
            model.legal_reviewed_at = None
            model.legal_review_reference = None
        else:
            model.legal_review_authority = version.legal_review.authority
            model.legal_reviewed_at = version.legal_review.reviewed_at
            model.legal_review_reference = version.legal_review.reference
        model.approved_by_user_id = version.approved_by_user_id
        model.approved_at = version.approved_at
        model.current_by_user_id = version.current_by_user_id
        model.current_at = version.current_at
        self._session.flush()
        return _to_domain_version(model)

    def _lock_version_rows_for_purpose(
        self,
        purpose: OfferPurpose,
    ) -> tuple[OfferVersionModel, ...]:
        statement = (
            select(OfferVersionModel)
            .where(OfferVersionModel.purpose == purpose.value)
            .order_by(OfferVersionModel.id)
            .with_for_update()
        )
        return tuple(self._session.scalars(statement))


class SqlAlchemyCurrentOfferResolver:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_current(
        self,
        *,
        purpose: OfferPurpose,
        language: OfferLanguage,
    ) -> ResolvedCurrentOffer | None:
        return self._resolve(
            purpose=purpose,
            language=language,
            lock_for_acceptance=False,
        )

    def resolve_current_for_acceptance(
        self,
        *,
        language: OfferLanguage,
    ) -> ResolvedCurrentOffer | None:
        return self._resolve(
            purpose=OfferPurpose.REGISTRATION,
            language=language,
            lock_for_acceptance=True,
        )

    def lock_current_version_with_all_texts(
        self,
        *,
        purpose: OfferPurpose,
    ) -> tuple[OfferVersion, tuple[StoredOfferText, ...]] | None:
        """Lock the exact current version and read its complete language set."""

        statement = (
            select(OfferVersionModel)
            .where(
                OfferVersionModel.purpose == purpose.value,
                OfferVersionModel.status == OfferStatus.CURRENT.value,
            )
            .with_for_update(read=True, of=OfferVersionModel)
        )
        model = self._session.scalar(statement)
        if model is None:
            return None
        text_statement = (
            select(OfferTextModel)
            .where(OfferTextModel.offer_version_id == model.id)
            .order_by(OfferTextModel.language, OfferTextModel.id)
        )
        texts = tuple(
            _to_stored_text(text) for text in self._session.scalars(text_statement)
        )
        return _to_domain_version(model), texts

    def _resolve(
        self,
        *,
        purpose: OfferPurpose,
        language: OfferLanguage,
        lock_for_acceptance: bool,
    ) -> ResolvedCurrentOffer | None:
        statement = (
            select(OfferVersionModel, OfferTextModel)
            .join(
                OfferTextModel,
                OfferTextModel.offer_version_id == OfferVersionModel.id,
            )
            .where(
                OfferVersionModel.purpose == purpose.value,
                OfferVersionModel.status == OfferStatus.CURRENT.value,
                OfferTextModel.language == language.value,
            )
        )
        if lock_for_acceptance:
            statement = statement.with_for_update(
                read=True,
                of=OfferVersionModel,
            )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        version_model, text_model = row
        return ResolvedCurrentOffer(
            version=_to_domain_version(version_model),
            text=_to_stored_text(text_model),
        )


class SqlAlchemyOfferAcceptanceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_acceptance(
        self,
        *,
        user_id: UUID,
        offer_text_id: UUID,
        purpose: OfferPurpose,
    ) -> StoredRegistrationOfferAcceptance | None:
        statement = select(OfferAcceptanceModel).where(
            OfferAcceptanceModel.user_id == user_id,
            OfferAcceptanceModel.offer_text_id == offer_text_id,
            OfferAcceptanceModel.purpose == purpose.value,
            OfferAcceptanceModel.debt_id.is_(None),
        )
        model = self._session.scalar(statement)
        return None if model is None else _to_stored_acceptance(model)

    def lock_earliest_exact_current_registration_acceptance(
        self,
        *,
        user_id: UUID,
        current_version: OfferVersion,
    ) -> StoredRegistrationOfferAcceptance | None:
        if (
            not isinstance(current_version, OfferVersion)
            or current_version.purpose is not OfferPurpose.REGISTRATION
            or current_version.status is not OfferStatus.CURRENT
        ):
            raise ValueError("Current registration offer is invalid")
        statement = (
            select(OfferAcceptanceModel)
            .join(
                OfferTextModel,
                OfferTextModel.id == OfferAcceptanceModel.offer_text_id,
            )
            .where(
                OfferAcceptanceModel.user_id == user_id,
                OfferAcceptanceModel.offer_version_id == current_version.id,
                OfferAcceptanceModel.purpose == OfferPurpose.REGISTRATION.value,
                OfferAcceptanceModel.debt_id.is_(None),
                OfferAcceptanceModel.version_number == current_version.version_number,
                OfferAcceptanceModel.language == OfferTextModel.language,
                OfferAcceptanceModel.content_hash == OfferTextModel.content_hash,
                OfferTextModel.offer_version_id == current_version.id,
            )
            .order_by(
                OfferAcceptanceModel.accepted_at,
                OfferAcceptanceModel.id,
            )
            .with_for_update(of=OfferAcceptanceModel)
        )
        models = tuple(self._session.scalars(statement))
        return None if not models else _to_stored_acceptance(models[0])

    def create_acceptance(
        self,
        *,
        acceptance: RegistrationOfferAcceptance,
    ) -> StoredRegistrationOfferAcceptance:
        model = OfferAcceptanceModel(
            user_id=acceptance.user_id,
            offer_version_id=acceptance.offer_version_id,
            offer_text_id=acceptance.offer_text_id,
            purpose=acceptance.purpose.value,
            language=acceptance.language.value,
            version_number=acceptance.version_number,
            content_hash=acceptance.content_hash,
            accepted_at=acceptance.accepted_at,
            user_agent=acceptance.user_agent,
        )
        try:
            with self._session.begin_nested():
                self._session.add(model)
                self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == _ACCEPTANCE_REPLAY_CONSTRAINT:
                raise OfferAcceptanceInsertConflict(
                    "Offer acceptance insert conflicted"
                ) from None
            raise
        return _to_stored_acceptance(model)

    def create_debt_acceptance(
        self, *, acceptance: DebtOfferAcceptanceSnapshot
    ) -> StoredDebtOfferAcceptance:
        model = OfferAcceptanceModel(
            user_id=acceptance.user_id,
            offer_version_id=acceptance.offer_version_id,
            offer_text_id=acceptance.offer_text_id,
            purpose=acceptance.purpose.value,
            language=acceptance.language.value,
            version_number=acceptance.version_number,
            content_hash=acceptance.content_hash,
            accepted_at=acceptance.accepted_at,
            user_agent=acceptance.user_agent,
            debt_id=acceptance.debt_id.as_uuid(),
        )
        try:
            with self._session.begin_nested():
                self._session.add(model)
                self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == _DEBT_ACCEPTANCE_CONSTRAINT:
                raise OfferAcceptanceInsertConflict(
                    "Debt offer acceptance insert conflicted"
                ) from None
            raise
        return StoredDebtOfferAcceptance(id=model.id, acceptance=acceptance)


class SqlAlchemyHasAcceptedCurrentRegistrationOffer:
    def __init__(self, session: Session) -> None:
        self._session = session

    def __call__(self, *, user_id: UUID) -> bool:
        statement = (
            select(OfferAcceptanceModel.id)
            .join(
                OfferTextModel,
                OfferTextModel.id == OfferAcceptanceModel.offer_text_id,
            )
            .join(
                OfferVersionModel,
                OfferVersionModel.id == OfferAcceptanceModel.offer_version_id,
            )
            .where(
                OfferAcceptanceModel.user_id == user_id,
                OfferAcceptanceModel.purpose == OfferPurpose.REGISTRATION.value,
                OfferAcceptanceModel.language == OfferTextModel.language,
                OfferAcceptanceModel.version_number == OfferVersionModel.version_number,
                OfferAcceptanceModel.content_hash == OfferTextModel.content_hash,
                OfferTextModel.offer_version_id == OfferVersionModel.id,
                OfferVersionModel.purpose == OfferPurpose.REGISTRATION.value,
                OfferVersionModel.status == OfferStatus.CURRENT.value,
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None


def _to_domain_version(model: OfferVersionModel) -> OfferVersion:
    review = None
    if model.legal_review_authority is not None:
        if model.legal_reviewed_at is None or model.legal_review_reference is None:
            raise ValueError("Persisted legal review evidence is incomplete")
        review = LegalReviewEvidence(
            authority=model.legal_review_authority,
            reviewed_at=model.legal_reviewed_at,
            reference=model.legal_review_reference,
        )
    return OfferVersion(
        id=model.id,
        purpose=OfferPurpose(model.purpose),
        version_number=model.version_number,
        status=OfferStatus(model.status),
        created_by_user_id=model.created_by_user_id,
        created_at=model.created_at,
        legal_review=review,
        approved_by_user_id=model.approved_by_user_id,
        approved_at=model.approved_at,
        current_by_user_id=model.current_by_user_id,
        current_at=model.current_at,
    )


def _to_stored_text(model: OfferTextModel) -> StoredOfferText:
    return StoredOfferText(
        id=model.id,
        variant=OfferTextVariant(
            offer_version_id=model.offer_version_id,
            language=OfferLanguage(model.language),
            title=model.title,
            body=model.body,
            content_hash=model.content_hash,
        ),
    )


def _to_stored_acceptance(
    model: OfferAcceptanceModel,
) -> StoredRegistrationOfferAcceptance:
    return StoredRegistrationOfferAcceptance(
        id=model.id,
        acceptance=RegistrationOfferAcceptance(
            user_id=model.user_id,
            offer_version_id=model.offer_version_id,
            offer_text_id=model.offer_text_id,
            purpose=OfferPurpose(model.purpose),
            language=OfferLanguage(model.language),
            version_number=model.version_number,
            content_hash=model.content_hash,
            accepted_at=model.accepted_at,
            user_agent=model.user_agent,
        ),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Repository timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
