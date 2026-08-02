from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import Session as AuthSession
from app.auth.sessions import rotate_session
from app.customer_activation.contracts import (
    ActivationCsrfSecret,
    ActivationSafeDeviceMetadata,
    ActivationSessionRotation,
    ActivationSessionSecrets,
    CurrentRegistrationAcceptanceSelection,
    PreparedCustomerActivation,
    RegistrationPrerequisiteError,
)
from app.customer_activation.ports import (
    CurrentSessionRotationPort,
    CustomerDocumentReadinessPort,
    CustomerIdentityReadinessPort,
    RegistrationOfferReadinessPort,
)
from app.customer_document.contracts import CustomerDocumentStatus
from app.customer_document.models import CustomerDocument
from app.customer_identity.contracts import IdentityRevision
from app.customer_identity.crypto import CustomerIdentityCryptoConfig
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.service import CustomerIdentityCompletenessService
from app.offers.enums import OfferPurpose, OfferStatus
from app.offers.repository import (
    SqlAlchemyOfferAcceptanceRepository,
    SqlAlchemyOfferVersionRepository,
)
from app.settings import Settings
from app.storage.models import ObjectFile, ObjectFileStatus

_ALLOWED_DOCUMENT_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_SESSION_TOKEN_CONSTRAINTS = frozenset(
    {"sessions_token_hash_key", "ix_sessions_token_hash"}
)


class CurrentSessionRotationConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Current session rotation conflicted")


class SqlAlchemyRegistrationOfferReadiness(RegistrationOfferReadinessPort):
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_earliest_exact_current_acceptance(
        self,
        *,
        actor_user_id: UUID,
    ) -> UUID | None:
        selection = self.select_earliest_exact_current_acceptance(
            actor_user_id=actor_user_id,
        )
        return selection.acceptance_id_for_snapshot() if selection.succeeded else None

    def select_earliest_exact_current_acceptance(
        self,
        *,
        actor_user_id: UUID,
    ) -> CurrentRegistrationAcceptanceSelection:
        versions = SqlAlchemyOfferVersionRepository(
            self._session
        ).lock_versions_for_purpose(purpose=OfferPurpose.REGISTRATION)
        current = tuple(
            version for version in versions if version.status is OfferStatus.CURRENT
        )
        if len(current) != 1:
            return CurrentRegistrationAcceptanceSelection(
                error=RegistrationPrerequisiteError.OFFER_UNAVAILABLE,
            )
        acceptance = SqlAlchemyOfferAcceptanceRepository(
            self._session
        ).lock_earliest_exact_current_registration_acceptance(
            user_id=actor_user_id,
            current_version=current[0],
        )
        if acceptance is None:
            return CurrentRegistrationAcceptanceSelection(
                error=(RegistrationPrerequisiteError.REGISTRATION_OFFER_NOT_ACCEPTED),
            )
        return CurrentRegistrationAcceptanceSelection(
            _acceptance_id=acceptance.id,
        )


class SqlAlchemyCustomerIdentityReadiness(CustomerIdentityReadinessPort):
    def __init__(
        self,
        session: Session,
        *,
        crypto_config: CustomerIdentityCryptoConfig,
    ) -> None:
        self._service = CustomerIdentityCompletenessService(
            repository=SqlAlchemyCustomerIdentityRepository(session),
            crypto_config=crypto_config,
        )

    def lock_complete_identity_revision(
        self,
        *,
        customer_id: UUID,
    ) -> IdentityRevision | None:
        return self._service.resolve_revision(
            customer_id=customer_id,
            lock_for_update=True,
        )


class SqlAlchemyCustomerDocumentReadiness(CustomerDocumentReadinessPort):
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_current_available_document(
        self,
        *,
        customer_id: UUID,
    ) -> UUID | None:
        candidates = tuple(
            self._session.execute(
                select(CustomerDocument.id, CustomerDocument.object_file_id)
                .where(
                    CustomerDocument.customer_id == customer_id,
                    CustomerDocument.status == CustomerDocumentStatus.CURRENT.value,
                )
                .order_by(CustomerDocument.id)
            )
        )
        if len(candidates) != 1:
            return None
        document_id, object_file_id = candidates[0]
        object_file = self._session.get(
            ObjectFile,
            object_file_id,
            with_for_update=True,
        )
        if (
            object_file is None
            or object_file.status != ObjectFileStatus.AVAILABLE.value
            or object_file.content_type not in _ALLOWED_DOCUMENT_CONTENT_TYPES
        ):
            return None
        document = self._session.scalar(
            select(CustomerDocument)
            .where(CustomerDocument.id == document_id)
            .with_for_update()
        )
        if (
            document is None
            or document.customer_id != customer_id
            or document.object_file_id != object_file.id
            or document.status != CustomerDocumentStatus.CURRENT.value
        ):
            return None
        return document.id


class SqlAlchemyCurrentSessionRotation(CurrentSessionRotationPort):
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def replace_current_authenticated_session(
        self,
        *,
        actor_user_id: UUID,
        current_session_id: UUID,
        now: datetime,
    ) -> PreparedCustomerActivation | None:
        current_time = _as_utc(now)
        current = self._session.scalar(
            select(AuthSession)
            .where(
                AuthSession.id == current_session_id,
                AuthSession.user_id == actor_user_id,
            )
            .with_for_update()
        )
        if (
            current is None
            or current.revoked_at is not None
            or _as_utc(current.expires_at) <= current_time
        ):
            return None
        previous_expires_at = current.expires_at
        previous_active_shop_id = current.active_shop_id
        safe_device_metadata = ActivationSafeDeviceMetadata(
            user_agent=current.user_agent
        )
        try:
            with self._session.begin_nested():
                created = rotate_session(
                    self._session,
                    current_session=current,
                    user_id=actor_user_id,
                    user_agent=safe_device_metadata.user_agent,
                    now=current_time,
                    settings=self._settings,
                )
                created.session.expires_at = previous_expires_at
                created.session.active_shop_id = previous_active_shop_id
                self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) in _SESSION_TOKEN_CONSTRAINTS:
                raise CurrentSessionRotationConflict() from None
            raise
        rotation = ActivationSessionRotation(
            previous_session_id=current_session_id,
            replacement_session_id=created.session.id,
            user_id=actor_user_id,
            active_shop_id=previous_active_shop_id,
            safe_device_metadata=safe_device_metadata,
            _replacement_secrets=ActivationSessionSecrets(
                token=created.raw_token,
                csrf_secret=ActivationCsrfSecret(created.session.csrf_secret),
            ),
        )
        return PreparedCustomerActivation(_rotation=rotation)


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Activation repository time must be timezone-aware")
    return value.astimezone(UTC)


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
