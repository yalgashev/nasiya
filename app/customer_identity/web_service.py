from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.customer_document.repository import SqlAlchemyCustomerDocumentRepository
from app.customer_document.service import has_current_customer_identity_document
from app.customer_identity.contracts import (
    CustomerIdentityActor,
    CustomerIdentityMaskedSummary,
)
from app.customer_identity.crypto import CustomerIdentityCryptoConfig
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.service import get_own_customer_identity_view


@dataclass(frozen=True, slots=True, repr=False)
class OwnCustomerIdentityPageState:
    identity: CustomerIdentityMaskedSummary | None = field(repr=False)
    current_document_id: UUID | None = field(repr=False)
    has_current_document: bool

    def __repr__(self) -> str:
        return (
            "OwnCustomerIdentityPageState("
            "identity=<redacted>, current_document_id=<redacted>, "
            f"has_current_document={self.has_current_document!r})"
        )


def get_own_customer_identity_page_state(
    session: Session,
    *,
    actor: CustomerIdentityActor,
    crypto_config: CustomerIdentityCryptoConfig,
) -> OwnCustomerIdentityPageState:
    identity_repository = SqlAlchemyCustomerIdentityRepository(session)
    summary = get_own_customer_identity_view(
        repository=identity_repository,
        crypto_config=crypto_config,
        actor=actor,
    )
    own_customer = identity_repository.lock_own_customer_draft(
        actor_user_id=actor.user_id
    )
    if own_customer is None:
        raise RuntimeError("Own customer draft disappeared")
    current = SqlAlchemyCustomerDocumentRepository(session).lock_current_documents(
        customer_id=own_customer.customer_id
    )
    if len(current) > 1:
        raise RuntimeError("Customer document state is unavailable")
    current_document_id = current[0].id if current else None
    return OwnCustomerIdentityPageState(
        identity=summary,
        current_document_id=current_document_id,
        has_current_document=has_current_customer_identity_document(
            session,
            customer_id=own_customer.customer_id,
        ),
    )
