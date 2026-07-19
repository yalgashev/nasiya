import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.auth.models import Session as AuthSession

CSRF_TOKEN_CONTEXT: Final = b"nasiya-csrf-v1"


@dataclass(frozen=True, repr=False)
class CsrfToken:
    _value: str

    def __post_init__(self) -> None:
        if not self._value:
            raise ValueError("CSRF token cannot be empty")

    def as_form_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "CsrfToken(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-csrf-token>"


def get_csrf_token(session: AuthSession) -> CsrfToken:
    return CsrfToken(_derive_csrf_token_value(session.csrf_secret))


def verify_csrf_token(
    session: AuthSession,
    submitted_token: str | None,
    now: datetime,
) -> bool:
    if submitted_token is None or not submitted_token:
        return False
    if session.revoked_at is not None:
        return False
    if _as_utc(session.expires_at) <= _as_utc(now):
        return False

    expected_token = _derive_csrf_token_value(session.csrf_secret)
    return hmac.compare_digest(submitted_token, expected_token)


def _derive_csrf_token_value(csrf_secret: str) -> str:
    return hmac.new(
        key=csrf_secret.encode("utf-8"),
        msg=CSRF_TOKEN_CONTEXT,
        digestmod=hashlib.sha256,
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("CSRF timestamps must be timezone-aware")
    return value.astimezone(UTC)
