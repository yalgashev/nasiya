import inspect as python_inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.audit.repository as audit_repository
from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
)
from app.audit.models import AuditLog
from app.audit.repository import append_audit_event
from app.auth.models import User
from app.offers.content import (
    canonicalize_offer_text,
    compute_offer_content_hash,
)
from app.offers.contracts import (
    OfferTextVariant,
    RegistrationOfferAcceptance,
)
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance as OfferAcceptanceModel
from app.offers.models import OfferText as OfferTextModel
from app.offers.models import OfferVersion as OfferVersionModel
from app.offers.repository import (
    OfferAcceptanceInsertConflict,
    OfferVersionAllocationConflict,
    SqlAlchemyOfferAcceptanceRepository,
    SqlAlchemyOfferVersionRepository,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
CURRENT_AT = NOW + timedelta(minutes=1)
HASH = "a" * 64
_BARRIER_TIMEOUT_SECONDS = 10
_FUTURE_TIMEOUT_SECONDS = 20


def _user(session: Session, *, phone: str) -> User:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    return user


def _draft(*, user_id: UUID, version_number: int) -> OfferVersionModel:
    return OfferVersionModel(
        purpose=OfferPurpose.REGISTRATION.value,
        version_number=version_number,
        status=OfferStatus.DRAFT.value,
        created_by_user_id=user_id,
        created_at=NOW,
    )


def _approved(*, user_id: UUID, version_number: int) -> OfferVersionModel:
    return OfferVersionModel(
        purpose=OfferPurpose.REGISTRATION.value,
        version_number=version_number,
        status=OfferStatus.APPROVED.value,
        created_by_user_id=user_id,
        created_at=NOW,
        legal_review_authority="Nasiya Legal",
        legal_reviewed_at=NOW,
        legal_review_reference=f"LEGAL-2026-{version_number}",
        approved_by_user_id=user_id,
        approved_at=NOW,
    )


def _text(*, version_id: UUID) -> OfferTextModel:
    return OfferTextModel(
        offer_version_id=version_id,
        language=OfferLanguage.UZ_LATN.value,
        title="Taklif",
        body="Taklif matni",
        content_hash=HASH,
        created_at=NOW,
        updated_at=NOW,
    )


def _acceptance(
    *,
    user_id: UUID,
    version_id: UUID,
    text_id: UUID,
) -> RegistrationOfferAcceptance:
    return RegistrationOfferAcceptance(
        user_id=user_id,
        offer_version_id=version_id,
        offer_text_id=text_id,
        purpose=OfferPurpose.REGISTRATION,
        language=OfferLanguage.UZ_LATN,
        version_number=1,
        content_hash=HASH,
        accepted_at=NOW,
        user_agent="Persistence Test Browser",
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def test_duplicate_version_and_language_constraints_recover_savepoint(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user = _user(session, phone="+998900000925")
        version = _draft(user_id=user.id, version_number=1)
        session.add(version)
        session.flush()
        text = _text(version_id=version.id)
        session.add(text)
        session.flush()

        with pytest.raises(IntegrityError) as version_error:
            with session.begin_nested():
                session.add(_draft(user_id=user.id, version_number=1))
                session.flush()
        assert _constraint_name(version_error.value) == (
            "uq_offer_versions_purpose_version_number"
        )
        assert session.scalar(select(1)) == 1

        with pytest.raises(IntegrityError) as language_error:
            with session.begin_nested():
                session.add(_text(version_id=version.id))
                session.flush()
        assert _constraint_name(language_error.value) == (
            "uq_offer_texts_offer_version_id_language"
        )
        assert session.scalar(select(1)) == 1
        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 1
        assert session.scalar(select(func.count()).select_from(OfferTextModel)) == 1


def test_offer_foreign_key_violation_is_named_and_savepoint_isolated(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        with pytest.raises(IntegrityError) as error:
            with session.begin_nested():
                session.add(_text(version_id=uuid4()))
                session.flush()

        assert _constraint_name(error.value) == (
            "fk_offer_texts_offer_version_id_offer_versions_id"
        )
        assert session.scalar(select(1)) == 1
        assert session.scalar(select(func.count()).select_from(OfferTextModel)) == 0


class _BarrierVersionRepository(SqlAlchemyOfferVersionRepository):
    def __init__(self, session: Session, barrier: Barrier) -> None:
        super().__init__(session)
        self._barrier = barrier

    def _lock_version_rows_for_purpose(
        self,
        purpose: OfferPurpose,
    ) -> tuple[OfferVersionModel, ...]:
        rows = super()._lock_version_rows_for_purpose(purpose)
        self._barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return rows


def test_parallel_first_version_allocation_has_one_named_conflict(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user_id = _user(session, phone="+998900000926").id

    barrier = Barrier(2)

    def allocate() -> tuple[str, bool]:
        with Session(m2_test_database) as session, session.begin():
            repository = _BarrierVersionRepository(session, barrier)
            try:
                repository.create_draft(
                    purpose=OfferPurpose.REGISTRATION,
                    created_by_user_id=user_id,
                    created_at=NOW,
                )
            except OfferVersionAllocationConflict:
                return ("conflict", session.scalar(select(1)) == 1)
            return ("created", session.scalar(select(1)) == 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(allocate) for _index in range(2)]
        outcomes = [
            future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures
        ]

    assert sorted(status for status, _usable in outcomes) == [
        "conflict",
        "created",
    ]
    assert all(usable for _status, usable in outcomes)
    with Session(m2_test_database) as session, session.begin():
        repository = SqlAlchemyOfferVersionRepository(session)
        persisted = repository.list_versions(purpose=OfferPurpose.REGISTRATION)
        assert len(persisted) == 1
        assert persisted[0].version_number == 1
        next_version = repository.create_draft(
            purpose=OfferPurpose.REGISTRATION,
            created_by_user_id=user_id,
            created_at=NOW,
        )
        assert next_version.version_number == 2


def test_parallel_current_candidates_have_one_database_winner(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user = _user(session, phone="+998900000927")
        candidates = (
            _approved(user_id=user.id, version_number=1),
            _approved(user_id=user.id, version_number=2),
        )
        session.add_all(candidates)
        session.flush()
        candidate_ids = tuple(candidate.id for candidate in candidates)
        actor_id = user.id

    barrier = Barrier(2)

    def make_current(version_id: UUID) -> tuple[str, str | None, bool]:
        with Session(m2_test_database) as session:
            candidate = session.get(OfferVersionModel, version_id)
            assert candidate is not None
            barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            candidate.status = OfferStatus.CURRENT.value
            candidate.current_by_user_id = actor_id
            candidate.current_at = CURRENT_AT
            try:
                session.flush()
                session.commit()
                return ("current", None, session.scalar(select(1)) == 1)
            except IntegrityError as exc:
                constraint = _constraint_name(exc)
                session.rollback()
                return ("conflict", constraint, session.scalar(select(1)) == 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(make_current, version_id) for version_id in candidate_ids
        ]
        outcomes = [
            future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures
        ]

    assert sorted(status for status, _constraint, _usable in outcomes) == [
        "conflict",
        "current",
    ]
    assert {
        constraint for status, constraint, _usable in outcomes if status == "conflict"
    } == {"uq_offer_versions_current_purpose"}
    assert all(usable for _status, _constraint, usable in outcomes)
    with Session(m2_test_database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OfferVersionModel)
                .where(OfferVersionModel.status == OfferStatus.CURRENT.value)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OfferVersionModel)
                .where(OfferVersionModel.status == OfferStatus.APPROVED.value)
            )
            == 1
        )


def test_parallel_acceptance_insert_conflict_keeps_session_usable_for_reread(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user = _user(session, phone="+998900000928")
        version = _draft(user_id=user.id, version_number=1)
        session.add(version)
        session.flush()
        text = _text(version_id=version.id)
        session.add(text)
        session.flush()
        evidence = _acceptance(
            user_id=user.id,
            version_id=version.id,
            text_id=text.id,
        )

    barrier = Barrier(2)

    def accept() -> tuple[str, UUID, bool]:
        with Session(m2_test_database) as session, session.begin():
            repository = SqlAlchemyOfferAcceptanceRepository(session)
            barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            try:
                stored = repository.create_acceptance(acceptance=evidence)
                return ("created", stored.id, session.scalar(select(1)) == 1)
            except OfferAcceptanceInsertConflict:
                replay = repository.get_acceptance(
                    user_id=evidence.user_id,
                    offer_text_id=evidence.offer_text_id,
                    purpose=OfferPurpose.REGISTRATION,
                )
                assert replay is not None
                return ("conflict", replay.id, session.scalar(select(1)) == 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(accept) for _index in range(2)]
        outcomes = [
            future.result(timeout=_FUTURE_TIMEOUT_SECONDS) for future in futures
        ]

    assert sorted(status for status, _row_id, _usable in outcomes) == [
        "conflict",
        "created",
    ]
    assert len({row_id for _status, row_id, _usable in outcomes}) == 1
    assert all(usable for _status, _row_id, usable in outcomes)
    with Session(m2_test_database) as session:
        assert (
            session.scalar(select(func.count()).select_from(OfferAcceptanceModel)) == 1
        )


def _audit_events(*, actor_id: UUID) -> tuple[AuditEvent, ...]:
    version_id = uuid4()
    text_id = uuid4()
    acceptance_id = uuid4()
    return (
        AuditEvent(
            event_type=AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.USER,
            object_id=actor_id,
            occurred_at=NOW,
            candidate_metadata={"bootstrap_method": "operator_cli"},
        ),
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_CREATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_id,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=version_id,
            occurred_at=NOW,
            candidate_metadata={
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 1,
                "status": OfferStatus.DRAFT,
            },
        ),
        AuditEvent(
            event_type=AuditEventType.OFFER_TEXT_UPDATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_id,
            object_type=AuditObjectType.OFFER_TEXT,
            object_id=text_id,
            occurred_at=NOW,
            candidate_metadata={
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 1,
                "language": OfferLanguage.UZ_LATN,
                "content_hash": HASH,
            },
        ),
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_APPROVED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_id,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=version_id,
            occurred_at=NOW,
            candidate_metadata={
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 1,
                "from_status": OfferStatus.DRAFT,
                "to_status": OfferStatus.APPROVED,
                "legal_review_authority": "Nasiya Legal",
                "legal_review_reference": "LEGAL-2026-925",
                "legal_reviewed_at": NOW,
            },
        ),
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_MADE_CURRENT,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_id,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=version_id,
            occurred_at=NOW,
            candidate_metadata={
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 1,
                "from_status": OfferStatus.APPROVED,
                "to_status": OfferStatus.CURRENT,
                "previous_current_version_id": None,
            },
        ),
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_DEMOTED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_id,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=version_id,
            occurred_at=NOW,
            candidate_metadata={
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 1,
                "from_status": OfferStatus.CURRENT,
                "to_status": OfferStatus.APPROVED,
                "replacement_version_id": uuid4(),
            },
        ),
        AuditEvent(
            event_type=AuditEventType.OFFER_REGISTRATION_ACCEPTED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_id,
            object_type=AuditObjectType.OFFER_ACCEPTANCE,
            object_id=acceptance_id,
            occurred_at=NOW,
            candidate_metadata={
                "purpose": OfferPurpose.REGISTRATION,
                "offer_version_id": version_id,
                "offer_text_id": text_id,
                "version_number": 1,
                "language": OfferLanguage.UZ_LATN,
                "content_hash": HASH,
            },
        ),
    )


def test_audit_log_schema_registry_and_payload_constraint_are_exact(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)
    columns = inspector.get_columns("audit_log")
    assert tuple((column["name"], column["nullable"]) for column in columns) == (
        ("id", False),
        ("occurred_at", False),
        ("event_type", False),
        ("actor_kind", False),
        ("actor_user_id", True),
        ("object_type", False),
        ("object_id", False),
        ("payload", False),
    )
    checks = {
        check["name"]: check["sqltext"]
        for check in inspector.get_check_constraints("audit_log")
    }
    assert set(checks) == {
        "ck_audit_log_actor_kind_allowed",
        "ck_audit_log_actor_matches_event",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_payload_exact_shape",
    }
    assert all(
        event_type.value in checks["ck_audit_log_event_type_allowed"]
        for event_type in AuditEventType
    )
    assert inspector.get_unique_constraints("audit_log") == []

    with Session(m2_test_database) as session, session.begin():
        actor = _user(session, phone="+998900000929")
        for event in _audit_events(actor_id=actor.id):
            append_audit_event(session, event)
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 7

        with pytest.raises(IntegrityError) as payload_error:
            with session.begin_nested():
                session.add(
                    AuditLog(
                        occurred_at=NOW,
                        event_type=AuditEventType.OFFER_VERSION_CREATED.value,
                        actor_kind=AuditActorKind.USER.value,
                        actor_user_id=actor.id,
                        object_type=AuditObjectType.OFFER_VERSION.value,
                        object_id=uuid4(),
                        payload={
                            "purpose": OfferPurpose.REGISTRATION.value,
                            "version_number": 1,
                            "status": OfferStatus.DRAFT.value,
                            "body": "FORBIDDEN LEGAL BODY",
                        },
                    )
                )
                session.flush()
        assert _constraint_name(payload_error.value) == (
            "ck_audit_log_payload_exact_shape"
        )
        assert session.scalar(select(1)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 7


def test_audit_application_api_is_append_only() -> None:
    assert audit_repository.__all__ == [
        "SqlAlchemyAuditWriter",
        "append_audit_event",
    ]
    source = python_inspect.getsource(audit_repository)
    assert "def append_audit_event(" in source
    assert ".add(" in source
    assert ".flush(" in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "select(" not in source
    assert "update(" not in source
    assert "delete(" not in source


def test_audit_validation_failure_rolls_back_business_mutation(
    m2_test_database: Engine,
) -> None:
    phone = "+998900000930"
    with pytest.raises(ValueError, match="missing required metadata"):
        with Session(m2_test_database) as session, session.begin():
            user = _user(session, phone=phone)
            canonical = canonicalize_offer_text(
                title="Maxfiy sarlavha",
                body="Maxfiy yuridik matn",
            )
            version = SqlAlchemyOfferVersionRepository(session).create_draft(
                purpose=OfferPurpose.REGISTRATION,
                created_by_user_id=user.id,
                created_at=NOW,
            )
            variant = OfferTextVariant(
                offer_version_id=version.id,
                language=OfferLanguage.UZ_LATN,
                title=canonical.title,
                body=canonical.body,
                content_hash=compute_offer_content_hash(canonical),
            )
            SqlAlchemyOfferVersionRepository(session).save_draft_text(
                variant=variant,
                now=NOW,
            )
            append_audit_event(
                session,
                AuditEvent(
                    event_type=AuditEventType.OFFER_TEXT_UPDATED,
                    actor_kind=AuditActorKind.USER,
                    actor_user_id=user.id,
                    object_type=AuditObjectType.OFFER_TEXT,
                    object_id=uuid4(),
                    occurred_at=NOW,
                    candidate_metadata={
                        "purpose": OfferPurpose.REGISTRATION,
                        "body": "FORBIDDEN LEGAL BODY",
                    },
                ),
            )

    with Session(m2_test_database) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(User).where(User.phone == phone)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(OfferVersionModel)) == 0
        assert session.scalar(select(func.count()).select_from(OfferTextModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
