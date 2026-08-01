from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT
from app.customer.repository import get_customer_by_user_id
from app.customer_document.contracts import (
    CustomerDocumentAccessParentRequest,
    CustomerDocumentActor,
)
from app.customer_document.repository import SqlAlchemyCustomerDocumentRepository
from app.storage.contracts import (
    ObjectReadAuthorizationRequest,
    ObjectReadAuthorizationResult,
)


class OwnCurrentCustomerDocumentAuthorizer:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        actor: CustomerDocumentActor,
    ) -> None:
        if not isinstance(actor, CustomerDocumentActor):
            raise TypeError("Customer document actor is invalid")
        self._session_factory = session_factory
        self._actor = actor

    def __repr__(self) -> str:
        return (
            "OwnCurrentCustomerDocumentAuthorizer("
            "session_factory=<redacted>, actor=<redacted>)"
        )

    def resolve_current_parent(self) -> CustomerDocumentAccessParentRequest | None:
        with self._session_factory() as session:
            return resolve_own_current_document_parent(session, actor=self._actor)

    def authorize(
        self,
        request: ObjectReadAuthorizationRequest,
    ) -> ObjectReadAuthorizationResult:
        if (
            not isinstance(request, ObjectReadAuthorizationRequest)
            or request.actor_user_id != self._actor.user_id
            or not isinstance(
                request.domain_parent_reference,
                CustomerDocumentAccessParentRequest,
            )
        ):
            return ObjectReadAuthorizationResult.DENIED
        expected_parent = request.domain_parent_reference
        actual_parent = self.resolve_current_parent()
        if (
            actual_parent is None
            or actual_parent != expected_parent
            or actual_parent.object_file_id != request.object_file_id
        ):
            return ObjectReadAuthorizationResult.DENIED
        return ObjectReadAuthorizationResult.ALLOWED


def resolve_own_current_document_parent(
    session: Session,
    *,
    actor: CustomerDocumentActor,
) -> CustomerDocumentAccessParentRequest | None:
    customer = get_customer_by_user_id(session, actor.user_id)
    if (
        customer is None
        or customer.onboarding_status != CUSTOMER_ONBOARDING_STATUS_DRAFT
    ):
        return None
    return SqlAlchemyCustomerDocumentRepository(session).resolve_access_parent(
        customer_id=customer.id
    )
