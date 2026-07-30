import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.storage.repository as storage_repository
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
    OBJECT_FILE_CLAIM_MAX_BATCH_SIZE,
    ClaimedObjectFile,
    claim_stale_delete_pending,
    claim_stale_pending_uploads,
    create_pending_object_file,
    mark_object_file_available,
    mark_object_file_delete_pending,
    mark_object_file_deleted,
    mark_object_file_failed,
)

BASE = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
CLAIM_TIME = BASE + timedelta(hours=1)
STALE_SECONDS = 60


def _add_user(session: Session) -> User:
    user = User(phone="+998900008341", created_at=BASE, updated_at=BASE)
    session.add(user)
    session.flush()
    return user


def _create_pending(
    session: Session,
    *,
    user_id: UUID,
    now: datetime,
    suffix: str = "png",
) -> ObjectFile:
    object_file_id = uuid4()
    content_type = f"image/{suffix}"
    if suffix == "jpg":
        content_type = "image/jpeg"
    return create_pending_object_file(
        session,
        object_file_id=object_file_id,
        bucket=BucketName("nasiya-private-test"),
        object_key=ObjectKey(f"v1/objects/{object_file_id.hex}.{suffix}"),
        metadata=SanitizedImageMetadata(
            content_type=content_type,
            canonical_extension=suffix,
            size_bytes=80,
            width_px=4,
            height_px=5,
            checksum_sha256=ObjectChecksumSha256("c" * 64),
        ),
        created_by_user_id=user_id,
        now=now,
    )


def test_claim_repository_contract_is_bounded_and_has_no_external_io() -> None:
    source = inspect.getsource(storage_repository)
    claim_source = inspect.getsource(storage_repository._claim_stale_object_files)

    assert OBJECT_FILE_CLAIM_MAX_BATCH_SIZE == 5000
    assert "with_for_update(skip_locked=True)" in claim_source
    assert "updated_at.asc()" in claim_source
    assert "ObjectFile.id.asc()" in claim_source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "ObjectStorageService" not in source
    assert "put_object" not in source
    assert "head_object" not in source
    assert "delete_object" not in source
    assert "boto" not in source
    assert "httpx" not in source
    assert "logger" not in source
    assert "print(" not in source

    for function in (
        claim_stale_pending_uploads,
        claim_stale_delete_pending,
    ):
        parameters = list(inspect.signature(function).parameters.values())
        assert parameters[0].name == "session"
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in parameters[1:]
        )


@pytest.mark.integration
def test_pending_claim_boundary_order_batch_marker_and_status_exclusions(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = _add_user(session)
        oldest = _create_pending(
            session,
            user_id=user.id,
            now=BASE,
        )
        boundary = _create_pending(
            session,
            user_id=user.id,
            now=CLAIM_TIME - timedelta(seconds=STALE_SECONDS),
        )
        fresh = _create_pending(
            session,
            user_id=user.id,
            now=CLAIM_TIME - timedelta(seconds=STALE_SECONDS - 1),
        )
        available = _create_pending(session, user_id=user.id, now=BASE)
        failed = _create_pending(session, user_id=user.id, now=BASE)
        delete_pending = _create_pending(session, user_id=user.id, now=BASE)
        deleted = _create_pending(session, user_id=user.id, now=BASE)
        mark_object_file_available(
            session,
            object_file_id=available.id,
            now=BASE,
        )
        mark_object_file_failed(
            session,
            object_file_id=failed.id,
            failure_code=StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
            now=BASE,
        )
        mark_object_file_delete_pending(
            session,
            object_file_id=delete_pending.id,
            failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
            now=BASE,
        )
        mark_object_file_delete_pending(
            session,
            object_file_id=deleted.id,
            failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
            now=BASE,
        )
        mark_object_file_deleted(
            session,
            object_file_id=deleted.id,
            now=BASE,
        )
        session.commit()

        claims = claim_stale_pending_uploads(
            session,
            now=CLAIM_TIME,
            stale_seconds=STALE_SECONDS,
            batch_size=1,
        )
        assert [claim.object_file_id for claim in claims] == [oldest.id]
        assert claims[0].status is ObjectFileStatus.PENDING_UPLOAD
        assert claims[0].claimed_at == CLAIM_TIME
        assert "v1/objects/" not in repr(claims[0])
        assert "nasiya-private-test" not in repr(claims[0])
        assert "c" * 64 not in repr(claims[0])
        session.commit()

        second_claims = claim_stale_pending_uploads(
            session,
            now=CLAIM_TIME,
            stale_seconds=STALE_SECONDS,
            batch_size=5000,
        )
        assert [claim.object_file_id for claim in second_claims] == [boundary.id]
        assert fresh.id not in {claim.object_file_id for claim in second_claims}
        assert available.id not in {claim.object_file_id for claim in second_claims}
        assert failed.id not in {claim.object_file_id for claim in second_claims}
        assert delete_pending.id not in {
            claim.object_file_id for claim in second_claims
        }
        assert deleted.id not in {claim.object_file_id for claim in second_claims}
        session.commit()

        persisted = session.get(ObjectFile, oldest.id)
        assert persisted.updated_at == CLAIM_TIME
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_delete_pending_claim_selects_only_delete_pending(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = _add_user(session)
        delete_pending = _create_pending(session, user_id=user.id, now=BASE)
        pending = _create_pending(session, user_id=user.id, now=BASE)
        available = _create_pending(session, user_id=user.id, now=BASE)
        deleted = _create_pending(session, user_id=user.id, now=BASE)
        mark_object_file_delete_pending(
            session,
            object_file_id=delete_pending.id,
            failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
            now=BASE,
        )
        mark_object_file_available(
            session,
            object_file_id=available.id,
            now=BASE,
        )
        mark_object_file_delete_pending(
            session,
            object_file_id=deleted.id,
            failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
            now=BASE,
        )
        mark_object_file_deleted(
            session,
            object_file_id=deleted.id,
            now=BASE,
        )
        session.commit()

        claims = claim_stale_delete_pending(
            session,
            now=CLAIM_TIME,
            stale_seconds=STALE_SECONDS,
            batch_size=10,
        )
        assert len(claims) == 1
        assert claims[0].object_file_id == delete_pending.id
        assert claims[0].status is ObjectFileStatus.DELETE_PENDING
        claimed_ids = {claim.object_file_id for claim in claims}
        assert pending.id not in claimed_ids
        assert available.id not in claimed_ids
        assert deleted.id not in claimed_ids
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_skip_locked_gives_two_workers_distinct_claims(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = _add_user(setup_session)
        first = _create_pending(
            setup_session,
            user_id=user.id,
            now=BASE,
        )
        second = _create_pending(
            setup_session,
            user_id=user.id,
            now=BASE + timedelta(seconds=1),
        )
        setup_session.commit()

        first_claims = claim_stale_pending_uploads(
            first_session,
            now=CLAIM_TIME,
            stale_seconds=STALE_SECONDS,
            batch_size=1,
        )
        second_claims = claim_stale_pending_uploads(
            second_session,
            now=CLAIM_TIME,
            stale_seconds=STALE_SECONDS,
            batch_size=1,
        )
        assert [claim.object_file_id for claim in first_claims] == [first.id]
        assert [claim.object_file_id for claim in second_claims] == [second.id]
        assert first_claims[0].object_file_id != second_claims[0].object_file_id
    finally:
        setup_session.close()
        first_session.rollback()
        first_session.close()
        second_session.rollback()
        second_session.close()


@pytest.mark.integration
def test_claim_snapshot_survives_outer_commit_and_session_close(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = _add_user(session)
        object_file = _create_pending(session, user_id=user.id, now=BASE)
        object_file_id = object_file.id
        session.commit()
        claims = claim_stale_pending_uploads(
            session,
            now=CLAIM_TIME,
            stale_seconds=STALE_SECONDS,
            batch_size=1,
        )
        session.commit()
    finally:
        session.close()

    assert claims == [
        ClaimedObjectFile(
            object_file_id=object_file_id,
            bucket=BucketName("nasiya-private-test"),
            object_key=ObjectKey(f"v1/objects/{object_file_id.hex}.png"),
            content_type="image/png",
            size_bytes=80,
            checksum_sha256=ObjectChecksumSha256("c" * 64),
            width_px=4,
            height_px=5,
            status=ObjectFileStatus.PENDING_UPLOAD,
            failure_code=None,
            claimed_at=CLAIM_TIME,
        )
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("stale_seconds", "batch_size"),
    (
        (0, 1),
        (-1, 1),
        (True, 1),
        (1, 0),
        (1, 5001),
        (1, True),
    ),
)
def test_claim_rejects_invalid_boundaries(
    m2_test_database: Engine,
    stale_seconds: int,
    batch_size: int,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        with pytest.raises(ValueError):
            claim_stale_pending_uploads(
                session,
                now=CLAIM_TIME,
                stale_seconds=stale_seconds,
                batch_size=batch_size,
            )
        assert session.scalar(select(1)) == 1
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_claim_rejects_naive_time(m2_test_database: Engine) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            claim_stale_delete_pending(
                session,
                now=CLAIM_TIME.replace(tzinfo=None),
                stale_seconds=1,
                batch_size=1,
            )
    finally:
        session.rollback()
        session.close()
