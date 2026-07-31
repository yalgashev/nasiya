import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.authorization as offer_authorization
from app.audit.contracts import (
    AuditActorKind,
    AuditEventType,
    AuditObjectType,
)
from app.audit.models import AuditLog
from app.auth.deps import LoginRequired, require_user
from app.auth.models import User
from app.offers.authorization import (
    PlatformAdminActor,
    PlatformAdminAuthorizationError,
    PlatformAdminBootstrapStatus,
    assert_platform_admin_actor,
    bootstrap_first_platform_admin,
    require_platform_admin_actor,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 17, 0, tzinfo=UTC)
_FUTURE_TIMEOUT_SECONDS = 20


def _user(
    session: Session,
    *,
    phone: str,
    is_active: bool = True,
    is_platform_admin: bool = False,
) -> User:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=is_active,
        is_platform_admin=is_platform_admin,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _route_app(current_user: User | None) -> FastAPI:
    application = FastAPI()

    @application.get("/offer-admin-boundary")
    def protected(
        actor: Annotated[
            PlatformAdminActor,
            Depends(require_platform_admin_actor),
        ],
    ) -> dict[str, bool]:
        assert isinstance(actor, PlatformAdminActor)
        return {"authorized": True}

    if current_user is None:

        def anonymous_user() -> User:
            raise LoginRequired()

        application.dependency_overrides[require_user] = anonymous_user
    else:
        application.dependency_overrides[require_user] = lambda: current_user
    return application


def _detached_user(
    *,
    is_active: bool = True,
    is_platform_admin: bool = False,
) -> User:
    return User(
        id=uuid4(),
        phone="+998900000927",
        password_hash=None,
        is_active=is_active,
        is_platform_admin=is_platform_admin,
        created_at=NOW,
        updated_at=NOW,
    )


def test_offer_route_dependency_redirects_anonymous_and_denies_non_admin() -> None:
    anonymous = TestClient(_route_app(None), follow_redirects=False).get(
        "/offer-admin-boundary"
    )
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/auth/login"

    non_admin_user = _detached_user()
    denied = TestClient(_route_app(non_admin_user)).get("/offer-admin-boundary")
    assert denied.status_code == 403
    assert denied.headers["x-error-code"] == "FORBIDDEN"
    assert denied.json()["detail"]["code"] == "FORBIDDEN"
    assert str(non_admin_user.id) not in denied.text
    assert non_admin_user.phone not in denied.text

    inactive_admin = _detached_user(
        is_active=False,
        is_platform_admin=True,
    )
    inactive = TestClient(_route_app(inactive_admin)).get("/offer-admin-boundary")
    assert inactive.status_code == 403
    assert inactive.headers["x-error-code"] == "FORBIDDEN"


def test_offer_route_dependency_returns_opaque_actor_only_for_admin() -> None:
    admin = _detached_user(is_platform_admin=True)

    response = TestClient(_route_app(admin)).get("/offer-admin-boundary")

    assert response.status_code == 200
    assert response.json() == {"authorized": True}
    actor = require_platform_admin_actor(admin)
    assert actor.user_id == admin.id
    assert str(admin.id) not in repr(actor)
    with pytest.raises(
        (TypeError, ValueError),
        match="constructed directly|missing.*token",
    ):
        PlatformAdminActor(admin.id)


def test_service_boundary_rechecks_active_platform_admin_row(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _user(
            session,
            phone="+998900000931",
            is_platform_admin=True,
        )
        actor = require_platform_admin_actor(admin)
        assert_platform_admin_actor(session, actor)

    with Session(m2_test_database) as session, session.begin():
        canonical = session.get(User, actor.user_id)
        assert canonical is not None
        canonical.is_platform_admin = False

    with Session(m2_test_database) as session, session.begin():
        with pytest.raises(
            PlatformAdminAuthorizationError,
            match="authorization failed",
        ):
            assert_platform_admin_actor(session, actor)
        assert session.scalar(select(1)) == 1


@pytest.mark.parametrize(
    ("target_kind", "expected"),
    [
        ("missing", PlatformAdminBootstrapStatus.USER_NOT_FOUND),
        ("inactive", PlatformAdminBootstrapStatus.USER_INACTIVE),
    ],
)
def test_bootstrap_rejects_missing_or_inactive_target_without_audit(
    m2_test_database: Engine,
    target_kind: str,
    expected: PlatformAdminBootstrapStatus,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        if target_kind == "inactive":
            target_id = _user(
                session,
                phone="+998900000932",
                is_active=False,
            ).id
        else:
            target_id = uuid4()

        result = bootstrap_first_platform_admin(
            session,
            target_user_id=target_id,
            occurred_at=NOW,
        )

        assert result is expected
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.is_platform_admin.is_(True))
            )
            == 0
        )


def test_bootstrap_promotes_one_existing_active_user_with_system_audit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        target = _user(session, phone="+998900000933")
        other = _user(session, phone="+998900000934")
        target_id = target.id
        other_id = other.id

        result = bootstrap_first_platform_admin(
            session,
            target_user_id=target_id,
            occurred_at=NOW,
        )
        replay = bootstrap_first_platform_admin(
            session,
            target_user_id=other_id,
            occurred_at=NOW,
        )

        assert result is PlatformAdminBootstrapStatus.BOOTSTRAPPED
        assert replay is PlatformAdminBootstrapStatus.ADMIN_ALREADY_EXISTS

    with Session(m2_test_database) as session:
        assert session.get(User, target_id).is_platform_admin is True
        assert session.get(User, other_id).is_platform_admin is False
        audit = session.scalar(select(AuditLog))
        assert audit is not None
        assert audit.event_type == AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value
        assert audit.actor_kind == AuditActorKind.SYSTEM.value
        assert audit.actor_user_id is None
        assert audit.object_type == AuditObjectType.USER.value
        assert audit.object_id == target_id
        assert audit.occurred_at == NOW
        assert audit.payload == {"bootstrap_method": "operator_cli"}


def test_parallel_bootstrap_attempts_create_exactly_one_admin_and_audit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        target_ids = (
            _user(session, phone="+998900000935").id,
            _user(session, phone="+998900000936").id,
        )

    barrier = Barrier(2)

    def bootstrap(target_id: UUID) -> PlatformAdminBootstrapStatus:
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=10)
            return bootstrap_first_platform_admin(
                session,
                target_user_id=target_id,
                occurred_at=NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(bootstrap, target_id) for target_id in target_ids]
        results = [future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures]

    assert sorted(results) == [
        PlatformAdminBootstrapStatus.ADMIN_ALREADY_EXISTS,
        PlatformAdminBootstrapStatus.BOOTSTRAPPED,
    ]
    with Session(m2_test_database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.is_platform_admin.is_(True))
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_bootstrap_audit_failure_rolls_back_admin_grant(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        target_id = _user(session, phone="+998900000937").id

    def fail_audit(*_args, **_kwargs) -> None:
        raise RuntimeError("audit append unavailable")

    monkeypatch.setattr(offer_authorization, "append_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit append unavailable"):
        with Session(m2_test_database) as session, session.begin():
            bootstrap_first_platform_admin(
                session,
                target_user_id=target_id,
                occurred_at=NOW,
            )

    with Session(m2_test_database) as session:
        target = session.get(User, target_id)
        assert target is not None
        assert target.is_platform_admin is False
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_authorization_foundation_has_no_tenant_role_or_transaction_owner() -> None:
    source = inspect.getsource(offer_authorization)

    assert "ShopRole" not in source
    assert "app.shop" not in source
    assert ".order_by(User.id).with_for_update()" in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
