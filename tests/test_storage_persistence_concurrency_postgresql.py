from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    SanitizedImageMetadata,
)
from app.storage.errors import StorageInternalCode
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.repository import (
    ClaimedObjectFile,
    ObjectFileStateError,
    claim_stale_pending_uploads,
    create_pending_object_file,
    mark_object_file_available,
    mark_object_file_delete_pending,
)

BASE = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)
LATER = BASE + timedelta(minutes=10)


def _metadata() -> SanitizedImageMetadata:
    return SanitizedImageMetadata(
        content_type="image/png",
        canonical_extension="png",
        size_bytes=64,
        width_px=4,
        height_px=4,
        checksum_sha256=ObjectChecksumSha256("d" * 64),
    )


def _create_pending(
    session: Session,
    *,
    user_id: UUID,
    object_file_id: UUID | None = None,
    key_id: UUID | None = None,
    now: datetime = BASE,
) -> ObjectFile:
    resolved_object_id = object_file_id or uuid4()
    resolved_key_id = key_id or resolved_object_id
    return create_pending_object_file(
        session,
        object_file_id=resolved_object_id,
        bucket=BucketName("nasiya-private-test"),
        object_key=ObjectKey(f"v1/objects/{resolved_key_id.hex}.png"),
        metadata=_metadata(),
        created_by_user_id=user_id,
        now=now,
    )


def _seed_pending(engine: Engine) -> tuple[UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = User(phone="+998900008351", created_at=BASE, updated_at=BASE)
        session.add(user)
        session.flush()
        object_file = _create_pending(session, user_id=user.id)
        user_id = user.id
        object_file_id = object_file.id
        session.commit()
        return user_id, object_file_id
    finally:
        session.close()


@pytest.mark.integration
def test_concurrent_duplicate_bucket_key_has_exactly_one_winner(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    try:
        user = User(phone="+998900008352", created_at=BASE, updated_at=BASE)
        setup_session.add(user)
        setup_session.commit()
        user_id = user.id
    finally:
        setup_session.close()

    shared_key_id = uuid4()
    barrier = Barrier(2)

    def insert_competitor() -> tuple[str, str | None]:
        session = session_factory()
        try:
            barrier.wait()
            try:
                _create_pending(
                    session,
                    user_id=user_id,
                    key_id=shared_key_id,
                )
                session.commit()
                return ("created", None)
            except IntegrityError as exc:
                constraint_name = exc.orig.diag.constraint_name
                session.rollback()
                assert session.scalar(select(1)) == 1
                return ("conflict", constraint_name)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: insert_competitor(), range(2)))

    assert sorted(status for status, _constraint in outcomes) == [
        "conflict",
        "created",
    ]
    assert {constraint for status, constraint in outcomes if status == "conflict"} == {
        "uq_object_files_bucket_object_key"
    }
    with m2_test_database.connect() as connection:
        row_count = connection.scalar(
            select(func.count())
            .select_from(ObjectFile)
            .where(
                ObjectFile.bucket == "nasiya-private-test",
                ObjectFile.object_key == f"v1/objects/{shared_key_id.hex}.png",
            )
        )
    assert row_count == 1


@pytest.mark.integration
def test_expected_duplicate_inside_savepoint_leaves_session_usable(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = User(phone="+998900008353", created_at=BASE, updated_at=BASE)
        session.add(user)
        session.flush()
        key_id = uuid4()
        _create_pending(session, user_id=user.id, key_id=key_id)
        session.flush()

        with pytest.raises(IntegrityError) as exc_info:
            with session.begin_nested():
                _create_pending(session, user_id=user.id, key_id=key_id)

        assert exc_info.value.orig.diag.constraint_name == (
            "uq_object_files_bucket_object_key"
        )
        assert session.scalar(select(1)) == 1
        assert session.scalar(select(func.count()).select_from(ObjectFile)) == 1
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_two_available_transition_callers_converge_idempotently(
    m2_test_database: Engine,
) -> None:
    _user_id, object_file_id = _seed_pending(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    barrier = Barrier(2)

    def transition() -> tuple[str, datetime]:
        session = session_factory()
        try:
            barrier.wait()
            object_file = mark_object_file_available(
                session,
                object_file_id=object_file_id,
                now=LATER,
            )
            assert object_file is not None
            result = (object_file.status, object_file.available_at)
            session.commit()
            return result
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: transition(), range(2)))

    assert outcomes == [
        (ObjectFileStatus.AVAILABLE.value, LATER),
        (ObjectFileStatus.AVAILABLE.value, LATER),
    ]
    with Session(m2_test_database) as verification_session:
        persisted = verification_session.get(ObjectFile, object_file_id)
        assert persisted.status == ObjectFileStatus.AVAILABLE.value
        assert persisted.available_at == LATER
        assert persisted.failure_code is None


@pytest.mark.integration
def test_two_stale_workers_have_one_claim_owner(
    m2_test_database: Engine,
) -> None:
    _user_id, object_file_id = _seed_pending(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    barrier = Barrier(2)

    def claim() -> list[ClaimedObjectFile]:
        session = session_factory()
        try:
            barrier.wait()
            claims = claim_stale_pending_uploads(
                session,
                now=LATER,
                stale_seconds=60,
                batch_size=1,
            )
            session.commit()
            return claims
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: claim(), range(2)))

    assert sorted(len(claims) for claims in outcomes) == [0, 1]
    claimed_ids = [claim.object_file_id for claims in outcomes for claim in claims]
    assert claimed_ids == [object_file_id]


@pytest.mark.integration
def test_delete_vs_available_serializes_to_one_valid_winner(
    m2_test_database: Engine,
) -> None:
    _user_id, object_file_id = _seed_pending(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    barrier = Barrier(2)

    def make_available() -> str:
        session = session_factory()
        try:
            barrier.wait()
            try:
                mark_object_file_available(
                    session,
                    object_file_id=object_file_id,
                    now=LATER,
                )
                session.commit()
                return "available"
            except ObjectFileStateError:
                session.rollback()
                return "state_error"
        finally:
            session.close()

    def make_delete_pending() -> str:
        session = session_factory()
        try:
            barrier.wait()
            try:
                mark_object_file_delete_pending(
                    session,
                    object_file_id=object_file_id,
                    failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
                    now=LATER,
                )
                session.commit()
                return "delete_pending"
            except ObjectFileStateError:
                session.rollback()
                return "state_error"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        available_future = executor.submit(make_available)
        delete_future = executor.submit(make_delete_pending)
        outcomes = [available_future.result(), delete_future.result()]

    assert outcomes.count("state_error") == 1
    assert outcomes.count("available") + outcomes.count("delete_pending") == 1
    with Session(m2_test_database) as verification_session:
        persisted = verification_session.get(ObjectFile, object_file_id)
        assert persisted.status in {
            ObjectFileStatus.AVAILABLE.value,
            ObjectFileStatus.DELETE_PENDING.value,
        }
        if persisted.status == ObjectFileStatus.AVAILABLE.value:
            assert persisted.available_at == LATER
            assert persisted.failure_code is None
        else:
            assert persisted.available_at is None
            assert (
                persisted.failure_code
                == StorageInternalCode.OBJECT_METADATA_MISMATCH.value
            )
        assert persisted.terminal_at is None
        assert persisted.deleted_at is None


@pytest.mark.integration
def test_no_event_table_and_no_sensitive_claim_logging(
    m2_test_database: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _user_id, _object_file_id = _seed_pending(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        claims = claim_stale_pending_uploads(
            session,
            now=LATER,
            stale_seconds=60,
            batch_size=1,
        )
        session.commit()
    finally:
        session.close()

    tables = set(inspect(m2_test_database).get_table_names())
    assert {
        table_name for table_name in tables if table_name.startswith("object_file")
    } == {"object_files"}
    assert "nasiya-private-test" not in caplog.text
    assert "v1/objects/" not in caplog.text
    assert "d" * 64 not in caplog.text
    assert "nasiya-private-test" not in repr(claims)
    assert "v1/objects/" not in repr(claims)
    assert "d" * 64 not in repr(claims)
