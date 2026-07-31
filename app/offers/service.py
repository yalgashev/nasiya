from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
)
from app.audit.repository import append_audit_event
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.offers.authorization import (
    PlatformAdminActor,
    assert_platform_admin_actor,
)
from app.offers.commands import AcceptCurrentRegistrationOfferCommand
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.contracts import (
    LegalReviewEvidence,
    OfferTextVariant,
    OfferVersion,
    RegistrationOfferAcceptance,
    ResolvedCurrentOffer,
    StoredOfferText,
    StoredRegistrationOfferAcceptance,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.lifecycle import (
    require_atomic_current_replacement_demotion,
    require_offer_status_transition,
)
from app.offers.policy import OfferVersionCompletenessPolicy
from app.offers.repository import (
    OfferAcceptanceInsertConflict,
    SqlAlchemyCurrentOfferResolver,
    SqlAlchemyOfferAcceptanceRepository,
    SqlAlchemyOfferVersionRepository,
)
from app.offers.user_agent import normalize_offer_acceptance_user_agent


@dataclass(frozen=True, slots=True)
class UpsertOfferDraftTextResult:
    text: StoredOfferText | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.text is not None and self.error is None


@dataclass(frozen=True, slots=True)
class ApproveOfferVersionResult:
    version: OfferVersion | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.version is not None and self.error is None


class MakeOfferVersionCurrentOutcome(StrEnum):
    SWITCHED = "SWITCHED"
    ALREADY_CURRENT = "ALREADY_CURRENT"


@dataclass(frozen=True, slots=True)
class MakeOfferVersionCurrentResult:
    version: OfferVersion | None = None
    previous_current_version_id: UUID | None = None
    outcome: MakeOfferVersionCurrentOutcome | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.version is not None and self.outcome is not None and self.error is None
        )


@dataclass(frozen=True, slots=True)
class ResolveCurrentOfferResult:
    offer: ResolvedCurrentOffer | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.offer is not None and self.error is None


@dataclass(frozen=True, slots=True)
class ValidateCurrentRegistrationOfferResult:
    offer: ResolvedCurrentOffer | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return self.offer is not None and self.error is None


class AcceptCurrentRegistrationOfferOutcome(StrEnum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class AcceptCurrentRegistrationOfferResult:
    acceptance: StoredRegistrationOfferAcceptance | None = None
    outcome: AcceptCurrentRegistrationOfferOutcome | None = None
    error: ErrorCode | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.acceptance is not None
            and self.outcome is not None
            and self.error is None
        )


def create_offer_draft_version(
    session: Session,
    *,
    actor: PlatformAdminActor,
    purpose: OfferPurpose,
    now: datetime,
) -> OfferVersion:
    current_time = _as_utc(now)
    assert_platform_admin_actor(session, actor)
    version = SqlAlchemyOfferVersionRepository(session).create_draft(
        purpose=purpose,
        created_by_user_id=actor.user_id,
        created_at=current_time,
    )
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_CREATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor.user_id,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=version.id,
            occurred_at=current_time,
            candidate_metadata={
                "purpose": version.purpose,
                "version_number": version.version_number,
                "status": version.status,
            },
        ),
    )
    return version


def upsert_offer_draft_text(
    session: Session,
    *,
    actor: PlatformAdminActor,
    offer_version_id: UUID,
    language: OfferLanguage,
    title: str,
    body: str,
    now: datetime,
) -> UpsertOfferDraftTextResult:
    current_time = _as_utc(now)
    assert_platform_admin_actor(session, actor)
    repository = SqlAlchemyOfferVersionRepository(session)
    version = repository.lock_version(version_id=offer_version_id)
    if version is None or version.status is not OfferStatus.DRAFT:
        return UpsertOfferDraftTextResult(error=ErrorCode.OFFER_NOT_DRAFT)

    canonical = canonicalize_offer_text(title=title, body=body)
    variant = OfferTextVariant(
        offer_version_id=version.id,
        language=language,
        title=canonical.title,
        body=canonical.body,
        content_hash=compute_offer_content_hash(canonical),
    )
    stored = repository.save_draft_text(variant=variant, now=current_time)
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.OFFER_TEXT_UPDATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor.user_id,
            object_type=AuditObjectType.OFFER_TEXT,
            object_id=stored.id,
            occurred_at=current_time,
            candidate_metadata={
                "purpose": version.purpose,
                "version_number": version.version_number,
                "language": variant.language,
                "content_hash": variant.content_hash,
            },
        ),
    )
    return UpsertOfferDraftTextResult(text=stored)


def approve_offer_version(
    session: Session,
    *,
    actor: PlatformAdminActor,
    offer_version_id: UUID,
    legal_review_authority: str | None,
    legal_reviewed_at: datetime | None,
    legal_review_reference: str | None,
    now: datetime,
) -> ApproveOfferVersionResult:
    current_time = _as_utc(now)
    assert_platform_admin_actor(session, actor)
    repository = SqlAlchemyOfferVersionRepository(session)
    version = repository.lock_version(version_id=offer_version_id)
    if version is None or version.status is not OfferStatus.DRAFT:
        return ApproveOfferVersionResult(error=ErrorCode.OFFER_NOT_DRAFT)

    texts = repository.list_texts(version_id=version.id)
    completeness = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=version.id,
        variants=(text.variant for text in texts),
    )
    if not completeness.complete:
        return ApproveOfferVersionResult(error=ErrorCode.OFFER_INCOMPLETE)

    evidence = _legal_review_evidence(
        authority=legal_review_authority,
        reviewed_at=legal_reviewed_at,
        reference=legal_review_reference,
        now=current_time,
    )
    if evidence is None:
        return ApproveOfferVersionResult(error=ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED)

    approved = replace(
        version,
        status=require_offer_status_transition(
            version.status,
            OfferStatus.APPROVED,
        ),
        legal_review=evidence,
        approved_by_user_id=actor.user_id,
        approved_at=current_time,
    )
    persisted = repository.save_lifecycle_state(version=approved)
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_APPROVED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor.user_id,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=persisted.id,
            occurred_at=current_time,
            candidate_metadata={
                "purpose": persisted.purpose,
                "version_number": persisted.version_number,
                "from_status": OfferStatus.DRAFT,
                "to_status": persisted.status,
                "legal_review_authority": evidence.authority,
                "legal_review_reference": evidence.reference,
                "legal_reviewed_at": evidence.reviewed_at,
            },
        ),
    )
    return ApproveOfferVersionResult(version=persisted)


def make_offer_version_current(
    session: Session,
    *,
    actor: PlatformAdminActor,
    offer_version_id: UUID,
    expected_current_version_id: UUID | None,
    now: datetime,
) -> MakeOfferVersionCurrentResult:
    if expected_current_version_id is not None and not isinstance(
        expected_current_version_id, UUID
    ):
        raise ValueError("Expected current offer identity is invalid")
    current_time = _as_utc(now)
    assert_platform_admin_actor(session, actor)
    repository = SqlAlchemyOfferVersionRepository(session)
    candidate = repository.get_version(version_id=offer_version_id)
    if candidate is None:
        return MakeOfferVersionCurrentResult(error=ErrorCode.OFFER_NOT_APPROVED)

    locked_versions = repository.lock_versions_for_purpose(purpose=candidate.purpose)
    target = next(
        (version for version in locked_versions if version.id == candidate.id),
        None,
    )
    if target is None:
        return MakeOfferVersionCurrentResult(error=ErrorCode.OFFER_NOT_APPROVED)
    if target.status is OfferStatus.CURRENT:
        return MakeOfferVersionCurrentResult(
            version=target,
            previous_current_version_id=target.id,
            outcome=MakeOfferVersionCurrentOutcome.ALREADY_CURRENT,
        )

    current = next(
        (
            version
            for version in locked_versions
            if version.status is OfferStatus.CURRENT
        ),
        None,
    )
    actual_current_id = None if current is None else current.id
    if actual_current_id != expected_current_version_id:
        return MakeOfferVersionCurrentResult(error=ErrorCode.OFFER_CHANGED)
    if target.status is not OfferStatus.APPROVED:
        return MakeOfferVersionCurrentResult(error=ErrorCode.OFFER_NOT_APPROVED)

    texts = repository.list_texts(version_id=target.id)
    completeness = OfferVersionCompletenessPolicy().evaluate(
        offer_version_id=target.id,
        variants=(text.variant for text in texts),
    )
    if not completeness.complete:
        return MakeOfferVersionCurrentResult(error=ErrorCode.OFFER_INCOMPLETE)

    if current is not None:
        demoted = replace(
            current,
            status=require_atomic_current_replacement_demotion(
                current.status,
                OfferStatus.APPROVED,
            ),
        )
        repository.save_lifecycle_state(version=demoted)
        append_audit_event(
            session,
            AuditEvent(
                event_type=AuditEventType.OFFER_VERSION_DEMOTED,
                actor_kind=AuditActorKind.USER,
                actor_user_id=actor.user_id,
                object_type=AuditObjectType.OFFER_VERSION,
                object_id=current.id,
                occurred_at=current_time,
                candidate_metadata={
                    "purpose": current.purpose,
                    "version_number": current.version_number,
                    "from_status": OfferStatus.CURRENT,
                    "to_status": OfferStatus.APPROVED,
                    "replacement_version_id": target.id,
                },
            ),
        )

    promoted = replace(
        target,
        status=require_offer_status_transition(
            target.status,
            OfferStatus.CURRENT,
        ),
        current_by_user_id=actor.user_id,
        current_at=current_time,
    )
    persisted = repository.save_lifecycle_state(version=promoted)
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_MADE_CURRENT,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor.user_id,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=persisted.id,
            occurred_at=current_time,
            candidate_metadata={
                "purpose": persisted.purpose,
                "version_number": persisted.version_number,
                "from_status": OfferStatus.APPROVED,
                "to_status": OfferStatus.CURRENT,
                "previous_current_version_id": actual_current_id,
            },
        ),
    )
    return MakeOfferVersionCurrentResult(
        version=persisted,
        previous_current_version_id=actual_current_id,
        outcome=MakeOfferVersionCurrentOutcome.SWITCHED,
    )


def resolve_current_offer(
    session: Session,
    *,
    purpose: OfferPurpose,
    language: OfferLanguage,
) -> ResolveCurrentOfferResult:
    resolved = SqlAlchemyCurrentOfferResolver(session).resolve_current(
        purpose=purpose,
        language=language,
    )
    if resolved is None:
        return ResolveCurrentOfferResult(error=ErrorCode.OFFER_UNAVAILABLE)
    return ResolveCurrentOfferResult(offer=resolved)


def validate_current_registration_offer(
    session: Session,
    *,
    command: AcceptCurrentRegistrationOfferCommand,
) -> ValidateCurrentRegistrationOfferResult:
    active_user_statement = (
        select(User.id)
        .where(
            User.id == command.user_id,
            User.is_active.is_(True),
        )
        .with_for_update(read=True)
    )
    if session.scalar(active_user_statement) is None:
        return ValidateCurrentRegistrationOfferResult(error=ErrorCode.UNAUTHORIZED)

    try:
        resolved = SqlAlchemyCurrentOfferResolver(
            session
        ).resolve_current_for_acceptance(language=command.language)
    except ValueError:
        return ValidateCurrentRegistrationOfferResult(error=ErrorCode.OFFER_CHANGED)
    if resolved is None:
        return ValidateCurrentRegistrationOfferResult(error=ErrorCode.OFFER_UNAVAILABLE)
    if (
        resolved.version.purpose is not OfferPurpose.REGISTRATION
        or resolved.version.status is not OfferStatus.CURRENT
        or resolved.text.variant.offer_version_id != resolved.version.id
        or resolved.text.variant.language is not command.language
        or resolved.text.id != command.displayed_offer_text_id
    ):
        return ValidateCurrentRegistrationOfferResult(error=ErrorCode.OFFER_CHANGED)
    return ValidateCurrentRegistrationOfferResult(offer=resolved)


def accept_current_registration_offer(
    session: Session,
    *,
    command: AcceptCurrentRegistrationOfferCommand,
    now: datetime,
) -> AcceptCurrentRegistrationOfferResult:
    current_time = _as_utc(now)
    validation = validate_current_registration_offer(
        session,
        command=command,
    )
    if not validation.succeeded:
        return AcceptCurrentRegistrationOfferResult(error=validation.error)
    resolved = validation.offer
    if resolved is None:
        return AcceptCurrentRegistrationOfferResult(error=ErrorCode.OFFER_UNAVAILABLE)

    evidence = RegistrationOfferAcceptance(
        user_id=command.user_id,
        offer_version_id=resolved.version.id,
        offer_text_id=resolved.text.id,
        purpose=resolved.version.purpose,
        language=resolved.text.variant.language,
        version_number=resolved.version.version_number,
        content_hash=resolved.text.variant.content_hash,
        accepted_at=current_time,
        user_agent=normalize_offer_acceptance_user_agent(command.user_agent_source),
    )
    acceptance_repository = SqlAlchemyOfferAcceptanceRepository(session)
    existing = acceptance_repository.get_acceptance(
        user_id=evidence.user_id,
        offer_text_id=evidence.offer_text_id,
        purpose=evidence.purpose,
    )
    if existing is not None:
        return AcceptCurrentRegistrationOfferResult(
            acceptance=existing,
            outcome=AcceptCurrentRegistrationOfferOutcome.REPLAYED,
        )
    try:
        stored = acceptance_repository.create_acceptance(acceptance=evidence)
    except OfferAcceptanceInsertConflict:
        replay = acceptance_repository.get_acceptance(
            user_id=evidence.user_id,
            offer_text_id=evidence.offer_text_id,
            purpose=evidence.purpose,
        )
        if replay is None:
            raise RuntimeError(
                "Offer acceptance conflict did not resolve to an existing row"
            ) from None
        return AcceptCurrentRegistrationOfferResult(
            acceptance=replay,
            outcome=AcceptCurrentRegistrationOfferOutcome.REPLAYED,
        )
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.OFFER_REGISTRATION_ACCEPTED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=command.user_id,
            object_type=AuditObjectType.OFFER_ACCEPTANCE,
            object_id=stored.id,
            occurred_at=current_time,
            candidate_metadata={
                "purpose": evidence.purpose,
                "offer_version_id": evidence.offer_version_id,
                "offer_text_id": evidence.offer_text_id,
                "version_number": evidence.version_number,
                "language": evidence.language,
                "content_hash": evidence.content_hash,
            },
        ),
    )
    return AcceptCurrentRegistrationOfferResult(
        acceptance=stored,
        outcome=AcceptCurrentRegistrationOfferOutcome.CREATED,
    )


def _legal_review_evidence(
    *,
    authority: str | None,
    reviewed_at: datetime | None,
    reference: str | None,
    now: datetime,
) -> LegalReviewEvidence | None:
    if (
        not isinstance(authority, str)
        or not isinstance(reviewed_at, datetime)
        or not isinstance(reference, str)
    ):
        return None
    try:
        evidence = LegalReviewEvidence(
            authority=authority,
            reviewed_at=reviewed_at,
            reference=reference,
        )
    except ValueError:
        return None
    if evidence.reviewed_at > now:
        return None
    return evidence


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Offer service time must be timezone-aware")
    return value.astimezone(UTC)
