from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.debt.values import DebtId
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.enums import (
    OfferLanguage,
    OfferPurpose,
    OfferStatus,
    require_m9_runtime_acceptance_purpose,
)

_LEGAL_REVIEW_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,199}")
_CONTENT_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True, repr=False)
class LegalReviewEvidence:
    authority: str
    reviewed_at: datetime
    reference: str

    def __post_init__(self) -> None:
        authority = self.authority.strip()
        if not 1 <= len(authority) <= 200:
            raise ValueError("Legal review authority must be 1 to 200 characters")
        if any(unicodedata.category(char) == "Cc" for char in authority):
            raise ValueError("Legal review authority contains a control character")
        if _LEGAL_REVIEW_REFERENCE_PATTERN.fullmatch(self.reference) is None:
            raise ValueError("Legal review reference is invalid")
        object.__setattr__(self, "authority", authority)
        object.__setattr__(
            self,
            "reviewed_at",
            _as_utc(self.reviewed_at, field_name="reviewed_at"),
        )

    def __repr__(self) -> str:
        return "LegalReviewEvidence()"


@dataclass(frozen=True, slots=True)
class OfferVersion:
    id: UUID
    purpose: OfferPurpose
    version_number: int
    status: OfferStatus
    created_by_user_id: UUID
    created_at: datetime
    legal_review: LegalReviewEvidence | None = None
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    current_by_user_id: UUID | None = None
    current_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, field_name="id")
        _require_uuid(self.created_by_user_id, field_name="created_by_user_id")
        if not isinstance(self.purpose, OfferPurpose):
            raise ValueError("Offer purpose is invalid")
        if not isinstance(self.status, OfferStatus):
            raise ValueError("Offer status is invalid")
        if (
            not isinstance(self.version_number, int)
            or isinstance(self.version_number, bool)
            or self.version_number < 1
        ):
            raise ValueError("Offer version number must be positive")

        created_at = _as_utc(self.created_at, field_name="created_at")
        object.__setattr__(self, "created_at", created_at)

        approval_values = (
            self.legal_review,
            self.approved_by_user_id,
            self.approved_at,
        )
        approval_is_complete = all(value is not None for value in approval_values)
        approval_is_empty = all(value is None for value in approval_values)
        if not approval_is_complete and not approval_is_empty:
            raise ValueError("Offer approval metadata must be complete")
        if self.status is OfferStatus.DRAFT and not approval_is_empty:
            raise ValueError("Draft offer must not have approval evidence")
        if self.status is not OfferStatus.DRAFT and not approval_is_complete:
            raise ValueError("Approved offer requires approval evidence")

        current_values = (self.current_by_user_id, self.current_at)
        current_is_complete = all(value is not None for value in current_values)
        current_is_empty = all(value is None for value in current_values)
        if not current_is_complete and not current_is_empty:
            raise ValueError("Offer current metadata must be complete")
        if self.status is OfferStatus.DRAFT and not current_is_empty:
            raise ValueError("Draft offer must not have current metadata")
        if self.status is OfferStatus.CURRENT and not current_is_complete:
            raise ValueError("Current offer requires current metadata")

        if approval_is_complete:
            _require_uuid(
                self.approved_by_user_id,
                field_name="approved_by_user_id",
            )
            approved_at = _as_utc(self.approved_at, field_name="approved_at")
            if self.legal_review.reviewed_at > approved_at:
                raise ValueError("Legal review must not be after approval")
            if approved_at < created_at:
                raise ValueError("Offer approval must not predate creation")
            object.__setattr__(self, "approved_at", approved_at)

        if current_is_complete:
            _require_uuid(
                self.current_by_user_id,
                field_name="current_by_user_id",
            )
            current_at = _as_utc(self.current_at, field_name="current_at")
            if self.approved_at is None or current_at < self.approved_at:
                raise ValueError("Offer current time must not predate approval")
            object.__setattr__(self, "current_at", current_at)


@dataclass(frozen=True, slots=True)
class OfferTextVariant:
    offer_version_id: UUID
    language: OfferLanguage
    title: str = field(repr=False)
    body: str = field(repr=False)
    content_hash: str

    def __post_init__(self) -> None:
        _require_uuid(
            self.offer_version_id,
            field_name="offer_version_id",
        )
        if not isinstance(self.language, OfferLanguage):
            raise ValueError("Offer language is invalid")

        canonical = canonicalize_offer_text(title=self.title, body=self.body)
        if canonical.title != self.title or canonical.body != self.body:
            raise ValueError("Offer text must already be canonical")
        expected_hash = compute_offer_content_hash(canonical)
        if self.content_hash != expected_hash:
            raise ValueError("Offer content hash does not match canonical text")


@dataclass(frozen=True, slots=True, repr=False)
class RegistrationOfferAcceptance:
    user_id: UUID
    offer_version_id: UUID
    offer_text_id: UUID
    purpose: OfferPurpose
    language: OfferLanguage
    version_number: int
    content_hash: str
    accepted_at: datetime
    user_agent: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.user_id, field_name="user_id")
        _require_uuid(
            self.offer_version_id,
            field_name="offer_version_id",
        )
        _require_uuid(self.offer_text_id, field_name="offer_text_id")
        if not isinstance(self.purpose, OfferPurpose):
            raise ValueError("Offer purpose is invalid")
        require_m9_runtime_acceptance_purpose(self.purpose)
        if not isinstance(self.language, OfferLanguage):
            raise ValueError("Offer language is invalid")
        if (
            not isinstance(self.version_number, int)
            or isinstance(self.version_number, bool)
            or self.version_number < 1
        ):
            raise ValueError("Offer version number must be positive")
        if _CONTENT_HASH_PATTERN.fullmatch(self.content_hash) is None:
            raise ValueError("Offer content hash is invalid")
        object.__setattr__(
            self,
            "accepted_at",
            _as_utc(self.accepted_at, field_name="accepted_at"),
        )
        _require_normalized_user_agent(self.user_agent)

    def __repr__(self) -> str:
        return (
            "RegistrationOfferAcceptance("
            f"user_id={self.user_id!r}, "
            f"offer_version_id={self.offer_version_id!r}, "
            f"offer_text_id={self.offer_text_id!r}, "
            f"purpose={self.purpose.value!r}, "
            f"language={self.language.value!r}, "
            f"version_number={self.version_number!r}, "
            f"content_hash={self.content_hash!r}, "
            f"accepted_at={self.accepted_at!r}, "
            "user_agent=<redacted>)"
        )


class DebtOfferAcceptanceOutcome(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    STALE = "stale"


@dataclass(frozen=True, slots=True, repr=False)
class DebtOfferAcceptanceSnapshot:
    """Immutable debt-only evidence; it deliberately retains no offer body."""

    user_id: UUID = field(repr=False)
    debt_id: DebtId = field(repr=False)
    offer_version_id: UUID = field(repr=False)
    offer_text_id: UUID = field(repr=False)
    purpose: OfferPurpose
    language: OfferLanguage
    version_number: int
    content_hash: str
    accepted_at: datetime
    user_agent: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.user_id, field_name="user_id")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Debt acceptance debt identity is invalid")
        _require_uuid(self.offer_version_id, field_name="offer_version_id")
        _require_uuid(self.offer_text_id, field_name="offer_text_id")
        if self.purpose is not OfferPurpose.DEBT_ACCEPTANCE:
            raise ValueError("Debt acceptance purpose must be DEBT_ACCEPTANCE")
        if not isinstance(self.language, OfferLanguage):
            raise ValueError("Debt acceptance language is invalid")
        if (
            not isinstance(self.version_number, int)
            or isinstance(self.version_number, bool)
            or self.version_number < 1
        ):
            raise ValueError("Debt acceptance version number must be positive")
        if _CONTENT_HASH_PATTERN.fullmatch(self.content_hash) is None:
            raise ValueError("Debt acceptance content hash is invalid")
        object.__setattr__(
            self,
            "accepted_at",
            _as_utc(self.accepted_at, field_name="accepted_at"),
        )
        _require_normalized_user_agent(self.user_agent)

    @classmethod
    def from_current_offer(
        cls,
        *,
        user_id: UUID,
        debt_id: DebtId,
        resolved_offer: ResolvedCurrentOffer,
        language: OfferLanguage,
        displayed_offer_text_id: UUID,
        accepted_at: datetime,
        user_agent: str | None,
    ) -> DebtOfferAcceptanceSnapshot:
        if not isinstance(resolved_offer, ResolvedCurrentOffer):
            raise ValueError("Debt current offer is invalid")
        if resolved_offer.version.purpose is not OfferPurpose.DEBT_ACCEPTANCE:
            raise ValueError("Debt current offer purpose must be DEBT_ACCEPTANCE")
        if not isinstance(language, OfferLanguage):
            raise ValueError("Debt acceptance language is invalid")
        if not isinstance(displayed_offer_text_id, UUID):
            raise ValueError("Displayed offer text identity is invalid")
        if (
            resolved_offer.text.id != displayed_offer_text_id
            or resolved_offer.text.variant.language is not language
        ):
            raise DebtOfferAcceptanceStaleError("Debt offer changed")
        return cls(
            user_id=user_id,
            debt_id=debt_id,
            offer_version_id=resolved_offer.version.id,
            offer_text_id=resolved_offer.text.id,
            purpose=OfferPurpose.DEBT_ACCEPTANCE,
            language=language,
            version_number=resolved_offer.version.version_number,
            content_hash=resolved_offer.text.variant.content_hash,
            accepted_at=accepted_at,
            user_agent=user_agent,
        )

    def __repr__(self) -> str:
        return (
            "DebtOfferAcceptanceSnapshot("
            "user_id=<redacted>, debt_id=<redacted>, offer_version_id=<redacted>, "
            "offer_text_id=<redacted>, "
            f"purpose={self.purpose.value!r}, language={self.language.value!r}, "
            f"version_number={self.version_number!r}, "
            f"content_hash={self.content_hash!r}, "
            f"accepted_at={self.accepted_at!r}, user_agent=<redacted>)"
        )


class DebtOfferAcceptanceStaleError(ValueError):
    """Current debt acceptance evidence no longer matches the displayed offer."""


@dataclass(frozen=True, slots=True, repr=False)
class StoredDebtOfferAcceptance:
    id: UUID = field(repr=False)
    acceptance: DebtOfferAcceptanceSnapshot

    def __post_init__(self) -> None:
        _require_uuid(self.id, field_name="debt_offer_acceptance_id")
        if not isinstance(self.acceptance, DebtOfferAcceptanceSnapshot):
            raise ValueError("Stored debt acceptance is invalid")

    def __repr__(self) -> str:
        return "StoredDebtOfferAcceptance(id=<redacted>, acceptance=<redacted>)"


@dataclass(frozen=True, slots=True)
class DebtOfferAcceptanceResult:
    outcome: DebtOfferAcceptanceOutcome
    acceptance: StoredDebtOfferAcceptance | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DebtOfferAcceptanceOutcome):
            raise ValueError("Debt acceptance outcome is invalid")
        requires_acceptance = self.outcome in {
            DebtOfferAcceptanceOutcome.ACCEPTED,
            DebtOfferAcceptanceOutcome.REPLAY,
        }
        if requires_acceptance != isinstance(
            self.acceptance, StoredDebtOfferAcceptance
        ):
            raise ValueError("Debt acceptance result is invalid")

    @classmethod
    def accepted(
        cls, acceptance: StoredDebtOfferAcceptance
    ) -> DebtOfferAcceptanceResult:
        return cls(outcome=DebtOfferAcceptanceOutcome.ACCEPTED, acceptance=acceptance)

    @classmethod
    def replay(cls, acceptance: StoredDebtOfferAcceptance) -> DebtOfferAcceptanceResult:
        return cls(outcome=DebtOfferAcceptanceOutcome.REPLAY, acceptance=acceptance)

    @classmethod
    def stale(cls) -> DebtOfferAcceptanceResult:
        return cls(outcome=DebtOfferAcceptanceOutcome.STALE)

    @property
    def is_replay(self) -> bool:
        return self.outcome is DebtOfferAcceptanceOutcome.REPLAY


@dataclass(frozen=True, slots=True)
class StoredOfferText:
    id: UUID
    variant: OfferTextVariant

    def __post_init__(self) -> None:
        _require_uuid(self.id, field_name="offer_text_id")


@dataclass(frozen=True, slots=True)
class StoredRegistrationOfferAcceptance:
    id: UUID
    acceptance: RegistrationOfferAcceptance

    def __post_init__(self) -> None:
        _require_uuid(self.id, field_name="offer_acceptance_id")


@dataclass(frozen=True, slots=True)
class ResolvedCurrentOffer:
    version: OfferVersion
    text: StoredOfferText

    def __post_init__(self) -> None:
        if self.version.status is not OfferStatus.CURRENT:
            raise ValueError("Resolved offer version must be current")
        if self.text.variant.offer_version_id != self.version.id:
            raise ValueError("Resolved offer text belongs to another version")


@runtime_checkable
class OfferVersionRepository(Protocol):
    def create_draft(
        self,
        *,
        purpose: OfferPurpose,
        created_by_user_id: UUID,
        created_at: datetime,
    ) -> OfferVersion: ...

    def list_versions(
        self,
        *,
        purpose: OfferPurpose | None = None,
    ) -> tuple[OfferVersion, ...]: ...

    def get_version(self, *, version_id: UUID) -> OfferVersion | None: ...

    def lock_version(self, *, version_id: UUID) -> OfferVersion | None: ...

    def lock_versions_for_purpose(
        self,
        *,
        purpose: OfferPurpose,
    ) -> tuple[OfferVersion, ...]: ...

    def list_texts(
        self,
        *,
        version_id: UUID,
    ) -> tuple[StoredOfferText, ...]: ...

    def get_text(
        self,
        *,
        version_id: UUID,
        language: OfferLanguage,
    ) -> StoredOfferText | None: ...

    def save_draft_text(
        self,
        *,
        variant: OfferTextVariant,
        now: datetime,
    ) -> StoredOfferText: ...

    def save_lifecycle_state(self, *, version: OfferVersion) -> OfferVersion: ...


@runtime_checkable
class CurrentOfferResolver(Protocol):
    def resolve_current(
        self,
        *,
        purpose: OfferPurpose,
        language: OfferLanguage,
    ) -> ResolvedCurrentOffer | None: ...

    def resolve_current_for_acceptance(
        self,
        *,
        language: OfferLanguage,
    ) -> ResolvedCurrentOffer | None: ...


@runtime_checkable
class OfferAcceptanceRepository(Protocol):
    def get_acceptance(
        self,
        *,
        user_id: UUID,
        offer_text_id: UUID,
        purpose: OfferPurpose,
    ) -> StoredRegistrationOfferAcceptance | None: ...

    def create_acceptance(
        self,
        *,
        acceptance: RegistrationOfferAcceptance,
    ) -> StoredRegistrationOfferAcceptance: ...


@runtime_checkable
class HasAcceptedCurrentRegistrationOffer(Protocol):
    def __call__(self, *, user_id: UUID) -> bool: ...


def require_unique_offer_text_variants(
    variants: Iterable[OfferTextVariant],
) -> tuple[OfferTextVariant, ...]:
    materialized = tuple(variants)
    identities: set[tuple[UUID, OfferLanguage]] = set()
    for variant in materialized:
        identity = (variant.offer_version_id, variant.language)
        if identity in identities:
            raise ValueError("Offer version already has this language")
        identities.add(identity)
    return materialized


def next_offer_version_number(current_max: int | None) -> int:
    if current_max is None:
        return 1
    if (
        not isinstance(current_max, int)
        or isinstance(current_max, bool)
        or current_max < 1
    ):
        raise ValueError("Current maximum offer version must be positive")
    return current_max + 1


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _require_uuid(value: object, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{field_name} must be a UUID")


def _require_normalized_user_agent(value: str | None) -> None:
    if value is None:
        return
    if not value or len(value) > 512:
        raise ValueError("Normalized user agent must be 1 to 512 characters")
    if value != " ".join(value.split()):
        raise ValueError("User agent must already be normalized")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("User agent must already be normalized")
