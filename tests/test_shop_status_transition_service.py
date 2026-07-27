from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.shop.enums import ShopStatus, ShopStatusAction
from app.shop.models import Shop, ShopStatusEvent
from app.shop.service import (
    ShopStatusTransitionOutcome,
    ShopStatusTransitionResult,
    reactivate_shop,
    suspend_shop,
)
from app.shop.values import ShopId, UserId

NOW = datetime(2026, 7, 27, 22, 0, tzinfo=UTC)
PAST = datetime(2026, 7, 27, 21, 0, tzinfo=UTC)
TransitionFunc = Callable[..., ShopStatusTransitionResult]


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def unique_phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def add_user(session: Session) -> User:
    user = User(phone=unique_phone())
    session.add(user)
    session.flush()
    return user


def add_shop_row(
    session: Session,
    *,
    status: ShopStatus = ShopStatus.ACTIVE,
) -> Shop:
    shop = Shop(
        name="Status Transition Shop",
        phone=unique_phone(),
        status=status.value,
        created_at=PAST,
        updated_at=PAST,
    )
    session.add(shop)
    session.flush()
    return shop


def test_status_transition_docstring_defines_m5_admin_boundary() -> None:
    source = Path("app/shop/service.py").read_text()

    assert "Production authorization" in source
    assert "platform admins" in source
    assert "admin milestone" in source
    assert "production-guarded development CLI" in source
    assert "actor_user_id may be nullable" in source


@pytest.mark.integration
def test_suspend_shop_changes_active_to_suspended_and_writes_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session, status=ShopStatus.ACTIVE)
    actor = add_user(db_session)

    result = suspend_shop(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=UserId(actor.id),
        reason="  compliance hold  ",
        now=NOW,
    )

    assert result.succeeded is True
    assert result.error is None
    assert result.transition is not None
    assert result.transition.shop_id == shop.id
    assert result.transition.status is ShopStatus.SUSPENDED
    assert result.transition.outcome is ShopStatusTransitionOutcome.TRANSITIONED

    db_session.refresh(shop)
    assert shop.status == ShopStatus.SUSPENDED.value
    assert shop.updated_at == NOW

    event = only_status_event(db_session)
    assert event.shop_id == shop.id
    assert event.action == ShopStatusAction.SUSPENDED.value
    assert event.actor_user_id == actor.id
    assert event.reason == "compliance hold"
    assert event.created_at == NOW


@pytest.mark.integration
def test_reactivate_shop_changes_suspended_to_active_and_writes_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session, status=ShopStatus.SUSPENDED)

    result = reactivate_shop(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=None,
        reason="manual review passed",
        now=NOW,
    )

    assert result.succeeded is True
    assert result.error is None
    assert result.transition is not None
    assert result.transition.shop_id == shop.id
    assert result.transition.status is ShopStatus.ACTIVE
    assert result.transition.outcome is ShopStatusTransitionOutcome.TRANSITIONED

    db_session.refresh(shop)
    assert shop.status == ShopStatus.ACTIVE.value
    assert shop.updated_at == NOW

    event = only_status_event(db_session)
    assert event.shop_id == shop.id
    assert event.action == ShopStatusAction.REACTIVATED.value
    assert event.actor_user_id is None
    assert event.reason == "manual review passed"
    assert event.created_at == NOW


@pytest.mark.integration
def test_suspend_shop_noops_when_already_suspended_without_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session, status=ShopStatus.SUSPENDED)

    result = suspend_shop(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=None,
        reason="already handled",
        now=NOW,
    )

    assert result.succeeded is True
    assert result.transition is not None
    assert result.transition.shop_id == shop.id
    assert result.transition.status is ShopStatus.SUSPENDED
    assert result.transition.outcome is ShopStatusTransitionOutcome.NOOP

    db_session.refresh(shop)
    assert shop.status == ShopStatus.SUSPENDED.value
    assert shop.updated_at == PAST
    assert count_rows(db_session, ShopStatusEvent) == 0


@pytest.mark.integration
def test_reactivate_shop_noops_when_already_active_without_event(
    db_session: Session,
) -> None:
    shop = add_shop_row(db_session, status=ShopStatus.ACTIVE)

    result = reactivate_shop(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=None,
        reason="already open",
        now=NOW,
    )

    assert result.succeeded is True
    assert result.transition is not None
    assert result.transition.shop_id == shop.id
    assert result.transition.status is ShopStatus.ACTIVE
    assert result.transition.outcome is ShopStatusTransitionOutcome.NOOP

    db_session.refresh(shop)
    assert shop.status == ShopStatus.ACTIVE.value
    assert shop.updated_at == PAST
    assert count_rows(db_session, ShopStatusEvent) == 0


@pytest.mark.parametrize(
    ("transition_func", "initial_status"),
    [
        (suspend_shop, ShopStatus.ACTIVE),
        (reactivate_shop, ShopStatus.SUSPENDED),
    ],
)
@pytest.mark.parametrize("bad_reason", [None, "", "   ", "\n\t"])
@pytest.mark.integration
def test_status_transition_requires_nonblank_reason(
    db_session: Session,
    transition_func: TransitionFunc,
    initial_status: ShopStatus,
    bad_reason: str | None,
) -> None:
    shop = add_shop_row(db_session, status=initial_status)

    result = transition_func(
        db_session,
        shop_id=ShopId(shop.id),
        actor_user_id=None,
        reason=bad_reason,
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.REASON_REQUIRED
    db_session.refresh(shop)
    assert shop.status == initial_status.value
    assert shop.updated_at == PAST
    assert count_rows(db_session, ShopStatusEvent) == 0


@pytest.mark.parametrize("transition_func", [suspend_shop, reactivate_shop])
@pytest.mark.integration
def test_status_transition_missing_shop_is_safe_forbidden(
    db_session: Session,
    transition_func: TransitionFunc,
) -> None:
    result = transition_func(
        db_session,
        shop_id=ShopId(uuid4()),
        actor_user_id=None,
        reason="known reason",
        now=NOW,
    )

    assert result.succeeded is False
    assert result.error is ErrorCode.FORBIDDEN
    assert count_rows(db_session, ShopStatusEvent) == 0


def only_status_event(session: Session) -> ShopStatusEvent:
    event = session.scalar(select(ShopStatusEvent))
    assert event is not None
    return event


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0
