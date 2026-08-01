from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.orm import sessionmaker

from app.auth.deps import (
    CurrentSessionContext,
    CurrentSessionStatus,
    LoginRequired,
    get_current_session_context,
    require_user,
)
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.customer_document.contracts import CustomerDocumentActor
from app.settings import Settings


class CustomerDocumentRequestSecurityError(RuntimeError):
    def __init__(self) -> None:
        self.code = ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE
        super().__init__(self.code.value)

    def __repr__(self) -> str:
        return (
            "CustomerDocumentRequestSecurityError(code='CUSTOMER_DOCUMENT_UNAVAILABLE')"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _CsrfSessionSnapshot:
    csrf_secret: str = field(repr=False)
    expires_at: datetime
    revoked_at: datetime | None

    def __repr__(self) -> str:
        return (
            "_CsrfSessionSnapshot(csrf_secret=<redacted>, "
            f"expires_at={self.expires_at!r}, revoked_at={self.revoked_at!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDocumentRequestContext:
    actor: CustomerDocumentActor
    csrf_context: CurrentSessionContext = field(repr=False)

    def __repr__(self) -> str:
        return (
            "CustomerDocumentRequestContext(actor=<redacted>, csrf_context=<redacted>)"
        )


def resolve_customer_document_request_context(
    request: Request,
    *,
    session_factory: sessionmaker[DatabaseSession],
    settings: Settings,
    now: datetime,
) -> CustomerDocumentRequestContext:
    try:
        with session_factory.begin() as session:
            current = get_current_session_context(
                request,
                session,
                settings,
                now,
            )
            user = require_user(current)
            auth_session = current.get_session_row()
            if auth_session is None:
                raise LoginRequired
            csrf_snapshot = _CsrfSessionSnapshot(
                csrf_secret=auth_session.csrf_secret,
                expires_at=auth_session.expires_at,
                revoked_at=auth_session.revoked_at,
            )
            csrf_context = CurrentSessionContext(
                status=CurrentSessionStatus.AUTHENTICATED,
                session_id=current.session_id,
                user_id=user.id,
                _session=cast(AuthSession, csrf_snapshot),
            )
            actor = CustomerDocumentActor(user.id)
    except LoginRequired:
        raise
    except SQLAlchemyError:
        raise CustomerDocumentRequestSecurityError from None
    return CustomerDocumentRequestContext(
        actor=actor,
        csrf_context=csrf_context,
    )
