from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from app.auth.models import User
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.service import (
    IssuedTelegramLinkToken,
    issue_link_token_after_rate_limit,
    issue_relink_token_after_rate_limit,
    record_link_token_issuance_rate_limit,
)


def issue_link_token_in_one_test_transaction(
    session: Session,
    settings: Settings,
    current_user: User,
    client_ip: ResolvedClientIp,
    now: datetime,
    token_generator: Callable[[int], str] | None = None,
) -> IssuedTelegramLinkToken:
    """Preserve inherited service-test setup without a production mixed seam."""

    record_link_token_issuance_rate_limit(
        session,
        settings,
        current_user,
        client_ip,
        now,
    )
    return issue_link_token_after_rate_limit(
        session,
        current_user,
        now,
        token_generator,
    )


def issue_relink_token_in_one_test_transaction(
    session: Session,
    settings: Settings,
    current_user: User,
    client_ip: ResolvedClientIp,
    now: datetime,
    token_generator: Callable[[int], str] | None = None,
) -> IssuedTelegramLinkToken:
    """Preserve inherited service-test setup without a production mixed seam."""

    record_link_token_issuance_rate_limit(
        session,
        settings,
        current_user,
        client_ip,
        now,
    )
    return issue_relink_token_after_rate_limit(
        session,
        current_user,
        now,
        token_generator,
    )
