from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session as DatabaseSession

from app.auth.models import Session as AuthSession
from app.auth.redirects import get_safe_redirect_target
from app.auth.sessions import CreatedSession, rotate_session
from app.otp.contracts import OtpInternalOutcome
from app.otp.verification import OtpVerificationResult
from app.settings import Settings


def rotate_session_after_otp_consume(
    session: DatabaseSession,
    *,
    verification_result: OtpVerificationResult,
    current_session: AuthSession | None,
    user_agent: str | None,
    now: datetime,
    settings: Settings,
) -> CreatedSession | None:
    if (
        verification_result.outcome is not OtpInternalOutcome.OTP_CONSUMED
        or verification_result.user_id is None
    ):
        return None
    return rotate_session(
        db=session,
        current_session=current_session,
        user_id=verification_result.user_id,
        user_agent=user_agent,
        now=now,
        settings=settings,
    )


def get_otp_success_redirect_target(next_url: str | None) -> str:
    return get_safe_redirect_target(next_url)
