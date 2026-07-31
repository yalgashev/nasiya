import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.offers.service as offer_service
from app.audit.contracts import AuditEventType, AuditObjectType
from app.audit.models import AuditLog
from app.auth.models import User
from app.offers.authorization import (
    PlatformAdminAuthorizationError,
    require_platform_admin_actor,
)
from app.offers.enums import OfferPurpose, OfferStatus
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.service import create_offer_draft_version

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 18, 0, tzinfo=UTC)
_FUTURE_TIMEOUT_SECONDS = 20


def _admin(session: Session, *, phone: str = "+998900000938") -> User:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=True,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def test_create_draft_is_monotonic_draft_only_and_safely_audited(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _admin(session)
        actor = require_platform_admin_actor(admin)

        first = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )
        second = create_offer_draft_version(
            session,
            actor=actor,
            purpose=OfferPurpose.REGISTRATION,
            now=NOW,
        )

        assert (first.version_number, second.version_number) == (1, 2)
        assert first.status is OfferStatus.DRAFT
        assert second.status is OfferStatus.DRAFT
        assert first.created_by_user_id == admin.id
        assert first.legal_review is None
        assert first.approved_by_user_id is None
        assert first.current_by_user_id is None

    with Session(m2_test_database) as session:
        versions = tuple(
            session.scalars(
                select(OfferVersionModel).order_by(OfferVersionModel.version_number)
            )
        )
        assert [version.status for version in versions] == ["DRAFT", "DRAFT"]
        audits = tuple(
            session.scalars(
                select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.id)
            )
        )
        assert len(audits) == 2
        assert all(
            audit.event_type == AuditEventType.OFFER_VERSION_CREATED.value
            for audit in audits
        )
        assert all(
            audit.object_type == AuditObjectType.OFFER_VERSION.value for audit in audits
        )
        assert sorted(
            (audit.payload for audit in audits),
            key=lambda payload: payload["version_number"],
        ) == [
            {
                "purpose": OfferPurpose.REGISTRATION.value,
                "version_number": 1,
                "status": OfferStatus.DRAFT.value,
            },
            {
                "purpose": OfferPurpose.REGISTRATION.value,
                "version_number": 2,
                "status": OfferStatus.DRAFT.value,
            },
        ]


def test_create_draft_rechecks_actor_and_denies_revoked_admin(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin = _admin(session)
        actor = require_platform_admin_actor(admin)
        admin.is_platform_admin = False
        session.flush()

        with pytest.raises(
            PlatformAdminAuthorizationError,
            match="authorization failed",
        ):
            create_offer_draft_version(
                session,
                actor=actor,
                purpose=OfferPurpose.REGISTRATION,
                now=NOW,
            )

        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_parallel_create_requests_allocate_two_unique_monotonic_versions(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor = require_platform_admin_actor(_admin(session))

    barrier = Barrier(2)

    def create() -> int:
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=10)
            version = create_offer_draft_version(
                session,
                actor=actor,
                purpose=OfferPurpose.REGISTRATION,
                now=NOW,
            )
            return version.version_number

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create) for _index in range(2)]
        version_numbers = [
            future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures
        ]

    assert sorted(version_numbers) == [1, 2]
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 2
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(OfferVersionModel)
                .where(OfferVersionModel.status != OfferStatus.DRAFT.value)
            )
            == 0
        )


def test_create_draft_and_audit_follow_outer_rollback(
    m2_test_database: Engine,
) -> None:
    with pytest.raises(RuntimeError, match="force caller rollback"):
        with Session(m2_test_database) as session, session.begin():
            actor = require_platform_admin_actor(_admin(session))
            create_offer_draft_version(
                session,
                actor=actor,
                purpose=OfferPurpose.DEBT_ACCEPTANCE,
                now=NOW,
            )
            raise RuntimeError("force caller rollback")

    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_offer_service_never_owns_transaction_or_logs_content() -> None:
    source = inspect.getsource(offer_service.create_offer_draft_version)

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "title" not in source
    assert "body" not in source
    assert "logging" not in source
    assert "logger" not in source
