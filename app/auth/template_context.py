from typing import Any

from app.auth.csrf import get_csrf_token
from app.auth.models import Session as AuthSession


def get_csrf_template_context(
    session: AuthSession | None,
) -> dict[str, str | None]:
    if session is None:
        return {"csrf_token": None}
    return {"csrf_token": get_csrf_token(session).as_form_value()}


def with_csrf_context(
    context: dict[str, Any],
    session: AuthSession | None,
) -> dict[str, Any]:
    return {**context, **get_csrf_template_context(session)}
