from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.customer_activation.contracts import PreparedCustomerActivation
from app.customer_identity.contracts import IdentityRevision


@runtime_checkable
class RegistrationOfferReadinessPort(Protocol):
    def lock_earliest_exact_current_acceptance(
        self,
        *,
        actor_user_id: UUID,
    ) -> UUID | None: ...


@runtime_checkable
class CustomerIdentityReadinessPort(Protocol):
    def lock_complete_identity_revision(
        self,
        *,
        customer_id: UUID,
    ) -> IdentityRevision | None: ...


@runtime_checkable
class CustomerDocumentReadinessPort(Protocol):
    def lock_current_available_document(
        self,
        *,
        customer_id: UUID,
    ) -> UUID | None: ...


@runtime_checkable
class CurrentSessionRotationPort(Protocol):
    def replace_current_authenticated_session(
        self,
        *,
        actor_user_id: UUID,
        current_session_id: UUID,
        now: datetime,
    ) -> PreparedCustomerActivation | None: ...
