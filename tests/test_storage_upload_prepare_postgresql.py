import asyncio
import inspect
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

import app.storage.service as storage_service
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.db import create_database_session_factory
from app.settings import Settings
from app.storage.contracts import BucketName, ObjectKey, SanitizedImage
from app.storage.errors import StorageInternalCode, StorageUploadError
from app.storage.image import (
    BoundedImageBytes,
    ImageDimensionLimits,
    ImageSanitizationError,
)
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.rate_limit import record_storage_upload_attempt
from app.storage.service import (
    ingest_sanitized_image,
    prepare_sanitized_image_upload,
)
from app.telegram.client_ip import ResolvedClientIp
from tests.storage_fake import (
    FakeObjectStorageService,
    FakeStorageOperation,
    FakeStorageOutcome,
)

NOW = datetime(2026, 7, 30, 22, 0, tzinfo=UTC)
ACTOR_USER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CLIENT_IP = ResolvedClientIp("203.0.113.70")
RAW_ENDPOINT = "https://m8-upload-private.invalid"
RAW_ACCESS_KEY = "m8-upload-access-never-render"
RAW_SECRET_KEY = "m8-upload-secret-never-render"
RAW_BUCKET = "m8-upload-private"
RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-storage-upload-prepare"


class AsyncBytesSource:
    def __init__(
        self,
        payload: bytes,
        *,
        events: list[str] | None = None,
        assert_no_session: Callable[[], None] | None = None,
    ) -> None:
        self._payload = payload
        self._position = 0
        self.events = events if events is not None else []
        self._assert_no_session = assert_no_session

    async def seek(self, offset: int) -> None:
        if self._assert_no_session is not None:
            self._assert_no_session()
        self.events.append("source_seek")
        self._position = offset

    async def read(self, size: int) -> bytes:
        if self._assert_no_session is not None:
            self._assert_no_session()
        self.events.append("source_read")
        chunk = self._payload[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


def _png_bytes() -> bytes:
    output = BytesIO()
    with Image.new("RGBA", (4, 3), (10, 20, 30, 128)) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _settings(
    engine: Engine,
    *,
    user_attempts: int = 5,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        object_storage_endpoint_url=RAW_ENDPOINT,
        object_storage_region="region-1",
        object_storage_bucket=RAW_BUCKET,
        object_storage_access_key=RAW_ACCESS_KEY,
        object_storage_secret_key=RAW_SECRET_KEY,
        object_storage_use_ssl=True,
        object_storage_upload_rate_limit_user_attempts=user_attempts,
    )


def _object_count(engine: Engine) -> int:
    session_factory = create_database_session_factory(engine)
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(ObjectFile)) or 0


def _seed_actor(engine: Engine) -> None:
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        session.add(
            User(
                id=ACTOR_USER_ID,
                phone="+998900008501",
                created_at=NOW,
                updated_at=NOW,
            )
        )


@pytest.mark.integration
def test_prepare_commits_pending_row_and_returns_redacted_detached_envelope(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    source = AsyncBytesSource(_png_bytes())
    settings = _settings(m2_test_database)
    storage = FakeObjectStorageService()

    prepared = asyncio.run(
        prepare_sanitized_image_upload(
            create_database_session_factory(m2_test_database),
            source=source,
            actor_user_id=ACTOR_USER_ID,
            client_ip=CLIENT_IP,
            now=NOW,
            settings=settings,
        )
    )

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        stored = session.get(ObjectFile, prepared.object_file_id)
        rate_count = session.scalar(select(func.count()).select_from(AuthRateLimit))

    assert stored is not None
    assert stored.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert stored.created_by_user_id == ACTOR_USER_ID
    assert stored.bucket == prepared.bucket.as_internal_value()
    assert stored.object_key == prepared.object_key.as_internal_value()
    assert stored.content_type == prepared.image.metadata.content_type
    assert stored.size_bytes == prepared.image.metadata.size_bytes
    assert stored.checksum_sha256 == (
        prepared.image.metadata.checksum_sha256.as_internal_value()
    )
    assert rate_count == 2
    assert storage.calls == ()

    rendered = repr(prepared)
    for hidden in (
        RAW_ENDPOINT,
        RAW_ACCESS_KEY,
        RAW_SECRET_KEY,
        RAW_BUCKET,
        prepared.object_key.as_internal_value(),
        prepared.image.metadata.checksum_sha256.as_internal_value(),
    ):
        assert hidden not in rendered
    assert prepared.image.sanitized_bytes.as_internal_bytes() not in rendered.encode()


@pytest.mark.integration
def test_rate_limit_rejection_precedes_source_read_row_and_provider(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database, user_attempts=1)
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        first = record_storage_upload_attempt(
            session,
            settings,
            ACTOR_USER_ID,
            CLIENT_IP,
            NOW,
        )
    source = AsyncBytesSource(_png_bytes())
    storage = FakeObjectStorageService()

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                session_factory,
                source=source,
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW + timedelta(seconds=1),
                settings=settings,
                storage=storage,
            )
        )

    assert first.allowed is True
    assert exc_info.value.code is ErrorCode.RATE_LIMITED
    assert source.events == []
    assert _object_count(m2_test_database) == 0
    assert storage.calls == ()


@pytest.mark.integration
def test_source_and_provider_boundaries_have_no_open_session(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_actor(m2_test_database)
    events: list[str] = []
    opened_sessions: list[Session] = []
    active_session_count = 0

    class TrackingSession(Session):
        def __init__(self, **kwargs: object) -> None:
            nonlocal active_session_count
            super().__init__(**kwargs)
            self._tracking_closed = False
            active_session_count += 1
            opened_sessions.append(self)
            events.append("session_open")

        def close(self) -> None:
            nonlocal active_session_count
            if not self._tracking_closed:
                active_session_count -= 1
                self._tracking_closed = True
                events.append("session_close")
            super().close()

    def assert_no_session() -> None:
        assert active_session_count == 0

    original_sanitize = storage_service.sanitize_bounded_image

    def checked_sanitize(
        source: BoundedImageBytes,
        *,
        limits: ImageDimensionLimits,
        max_output_bytes: int,
    ) -> SanitizedImage:
        assert_no_session()
        events.append("sanitize")
        return original_sanitize(
            source,
            limits=limits,
            max_output_bytes=max_output_bytes,
        )

    monkeypatch.setattr(
        storage_service,
        "sanitize_bounded_image",
        checked_sanitize,
    )

    class OrderedStorage(FakeObjectStorageService):
        def put_object(
            self,
            *,
            bucket: BucketName,
            key: ObjectKey,
            image: SanitizedImage,
        ):
            assert_no_session()
            events.append("provider_put")
            return super().put_object(bucket=bucket, key=key, image=image)

        def head_object(
            self,
            *,
            bucket: BucketName,
            key: ObjectKey,
        ):
            assert_no_session()
            events.append("provider_head")
            return super().head_object(bucket=bucket, key=key)

    source = AsyncBytesSource(
        _png_bytes(),
        events=events,
        assert_no_session=assert_no_session,
    )
    tracking_factory = sessionmaker(
        bind=m2_test_database,
        class_=TrackingSession,
    )
    storage = OrderedStorage()
    result = asyncio.run(
        ingest_sanitized_image(
            tracking_factory,
            source=source,
            actor_user_id=ACTOR_USER_ID,
            client_ip=CLIENT_IP,
            now=NOW,
            settings=_settings(m2_test_database),
            storage=storage,
        )
    )

    assert active_session_count == 0
    assert result.object_file_id is not None
    assert len(opened_sessions) == 3
    assert len({id(session) for session in opened_sessions}) == 3
    assert opened_sessions[1] is not opened_sessions[2]
    assert events == [
        "session_open",
        "session_close",
        "source_seek",
        "source_read",
        "source_read",
        "sanitize",
        "session_open",
        "session_close",
        "provider_put",
        "provider_head",
        "session_open",
        "session_close",
    ]


@pytest.mark.integration
def test_ingest_puts_heads_commits_available_and_returns_safe_summary(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    storage = FakeObjectStorageService()

    result = asyncio.run(
        ingest_sanitized_image(
            create_database_session_factory(m2_test_database),
            source=AsyncBytesSource(_png_bytes()),
            actor_user_id=ACTOR_USER_ID,
            client_ip=CLIENT_IP,
            now=NOW,
            settings=_settings(m2_test_database),
            storage=storage,
        )
    )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.get(ObjectFile, result.object_file_id)
    assert stored is not None
    assert stored.status == ObjectFileStatus.AVAILABLE.value
    assert stored.available_at == NOW
    assert stored.failure_code is None
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    assert storage.object_count == 1
    assert result.content_type == stored.content_type == "image/png"
    assert result.size_bytes == stored.size_bytes
    assert result.width_px == stored.width_px == 4
    assert result.height_px == stored.height_px == 3
    assert result.checksum_sha256.as_internal_value() == stored.checksum_sha256

    rendered = f"{result!r} {result!s}"
    assert RAW_BUCKET not in rendered
    assert stored.object_key not in rendered
    assert stored.checksum_sha256 not in rendered
    assert "http" not in rendered
    assert "url" not in rendered.casefold()


@pytest.mark.integration
def test_tx_s1_commit_failure_rolls_back_and_makes_zero_provider_calls(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)

    class FailingCommitSession(Session):
        pass

    commit_count = 0
    sensitive_detail = (
        f"{RAW_ENDPOINT} {RAW_ACCESS_KEY} {RAW_SECRET_KEY} private database detail"
    )

    def fail_second_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise OperationalError(
                sensitive_detail,
                {"secret": RAW_SECRET_KEY},
                RuntimeError(sensitive_detail),
            )

    event.listen(FailingCommitSession, "before_commit", fail_second_commit)
    failing_factory = sessionmaker(
        bind=m2_test_database,
        class_=FailingCommitSession,
    )
    storage = FakeObjectStorageService()
    try:
        with pytest.raises(StorageUploadError) as exc_info:
            asyncio.run(
                ingest_sanitized_image(
                    failing_factory,
                    source=AsyncBytesSource(_png_bytes()),
                    actor_user_id=ACTOR_USER_ID,
                    client_ip=CLIENT_IP,
                    now=NOW,
                    settings=_settings(m2_test_database),
                    storage=storage,
                )
            )
    finally:
        event.remove(FailingCommitSession, "before_commit", fail_second_commit)

    error = exc_info.value
    assert commit_count == 2
    assert error.code is ErrorCode.FILE_STORAGE_ERROR
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sensitive_detail not in f"{error!s} {error!r}"
    assert _object_count(m2_test_database) == 0
    assert storage.calls == ()


@pytest.mark.integration
def test_tx_s2_commit_failure_returns_no_object_and_leaves_pending_for_reconcile(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)

    class FailingResultCommitSession(Session):
        pass

    commit_count = 0
    sensitive_detail = f"{RAW_ENDPOINT} {RAW_SECRET_KEY} result commit detail"

    def fail_third_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise OperationalError(
                sensitive_detail,
                {"secret": RAW_SECRET_KEY},
                RuntimeError(sensitive_detail),
            )

    event.listen(FailingResultCommitSession, "before_commit", fail_third_commit)
    failing_factory = sessionmaker(
        bind=m2_test_database,
        class_=FailingResultCommitSession,
    )
    storage = FakeObjectStorageService()
    try:
        with pytest.raises(StorageUploadError) as exc_info:
            asyncio.run(
                ingest_sanitized_image(
                    failing_factory,
                    source=AsyncBytesSource(_png_bytes()),
                    actor_user_id=ACTOR_USER_ID,
                    client_ip=CLIENT_IP,
                    now=NOW,
                    settings=_settings(m2_test_database),
                    storage=storage,
                )
            )
    finally:
        event.remove(
            FailingResultCommitSession,
            "before_commit",
            fail_third_commit,
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    error = exc_info.value
    assert commit_count == 3
    assert error.code is ErrorCode.FILE_STORAGE_ERROR
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sensitive_detail not in f"{error!s} {error!r}"
    assert stored is not None
    assert stored.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert stored.available_at is None
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    assert storage.object_count == 1


@pytest.mark.integration
def test_missing_head_unknown_tx_s2_failure_keeps_last_committed_pending(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)

    class FailingUnknownCommitSession(Session):
        pass

    commit_count = 0
    sensitive_detail = f"{RAW_ENDPOINT} {RAW_SECRET_KEY} unknown commit detail"

    def fail_third_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise OperationalError(
                sensitive_detail,
                {"secret": RAW_ACCESS_KEY},
                RuntimeError(sensitive_detail),
            )

    event.listen(FailingUnknownCommitSession, "before_commit", fail_third_commit)
    failing_factory = sessionmaker(
        bind=m2_test_database,
        class_=FailingUnknownCommitSession,
    )
    storage = FakeObjectStorageService()
    storage.queue_head_outcome(FakeStorageOutcome.MISSING)
    try:
        with pytest.raises(StorageUploadError) as exc_info:
            asyncio.run(
                ingest_sanitized_image(
                    failing_factory,
                    source=AsyncBytesSource(_png_bytes()),
                    actor_user_id=ACTOR_USER_ID,
                    client_ip=CLIENT_IP,
                    now=NOW,
                    settings=_settings(m2_test_database),
                    storage=storage,
                )
            )
    finally:
        event.remove(
            FailingUnknownCommitSession,
            "before_commit",
            fail_third_commit,
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
        row_count = session.scalar(select(func.count()).select_from(ObjectFile))
    error = exc_info.value
    assert commit_count == 3
    assert error.code is ErrorCode.FILE_STORAGE_ERROR
    assert error.internal_code is None
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sensitive_detail not in f"{error!s} {error!r}"
    assert stored is not None
    assert stored.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert stored.failure_code is None
    assert stored.available_at is None
    assert stored.terminal_at is None
    assert stored.deleted_at is None
    assert row_count == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    assert storage.object_count == 1


@pytest.mark.integration
def test_definite_put_failure_marks_failed_once_without_head_or_presign(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    storage = FakeObjectStorageService()
    storage.queue_put_outcome(FakeStorageOutcome.DEFINITE_FAILURE)

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                create_database_session_factory(m2_test_database),
                source=AsyncBytesSource(_png_bytes()),
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW,
                settings=_settings(m2_test_database),
                storage=storage,
            )
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    error = exc_info.value
    assert error.code is ErrorCode.FILE_STORAGE_ERROR
    assert error.internal_code is StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE
    assert error.__cause__ is None
    assert error.__context__ is None
    assert stored is not None
    assert stored.status == ObjectFileStatus.FAILED.value
    assert stored.failure_code == (
        StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE.value
    )
    assert stored.available_at is None
    assert stored.terminal_at == NOW
    assert stored.deleted_at is None
    assert [call.operation for call in storage.calls] == [FakeStorageOperation.PUT]
    assert storage.object_count == 0


@pytest.mark.integration
def test_definite_failure_transition_is_idempotent_for_same_pending_row(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    prepared = asyncio.run(
        prepare_sanitized_image_upload(
            session_factory,
            source=AsyncBytesSource(_png_bytes()),
            actor_user_id=ACTOR_USER_ID,
            client_ip=CLIENT_IP,
            now=NOW,
            settings=_settings(m2_test_database),
        )
    )

    for offset in (1, 2):
        storage_service._mark_prepared_upload_failed(
            session_factory,
            prepared=prepared,
            now=NOW + timedelta(seconds=offset),
            failure_code=StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
        )

    with session_factory() as session:
        stored = session.get(ObjectFile, prepared.object_file_id)
    assert stored is not None
    assert stored.status == ObjectFileStatus.FAILED.value
    assert stored.failure_code == (
        StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE.value
    )
    assert stored.terminal_at == NOW + timedelta(seconds=1)
    assert stored.updated_at == NOW + timedelta(seconds=1)


@pytest.mark.integration
def test_definite_failure_tx_s2_commit_error_leaves_committed_pending_row(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)

    class FailingFailureCommitSession(Session):
        pass

    commit_count = 0

    def fail_third_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise OperationalError(
                "sensitive failure transition detail",
                {"secret": RAW_SECRET_KEY},
                RuntimeError(RAW_ENDPOINT),
            )

    event.listen(FailingFailureCommitSession, "before_commit", fail_third_commit)
    failing_factory = sessionmaker(
        bind=m2_test_database,
        class_=FailingFailureCommitSession,
    )
    storage = FakeObjectStorageService()
    storage.queue_put_outcome(FakeStorageOutcome.DEFINITE_FAILURE)
    try:
        with pytest.raises(StorageUploadError) as exc_info:
            asyncio.run(
                ingest_sanitized_image(
                    failing_factory,
                    source=AsyncBytesSource(_png_bytes()),
                    actor_user_id=ACTOR_USER_ID,
                    client_ip=CLIENT_IP,
                    now=NOW,
                    settings=_settings(m2_test_database),
                    storage=storage,
                )
            )
    finally:
        event.remove(
            FailingFailureCommitSession,
            "before_commit",
            fail_third_commit,
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert commit_count == 3
    assert exc_info.value.code is ErrorCode.FILE_STORAGE_ERROR
    assert stored is not None
    assert stored.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert stored.failure_code is None
    assert stored.terminal_at is None
    assert [call.operation for call in storage.calls] == [FakeStorageOperation.PUT]


@pytest.mark.integration
def test_nested_sensitive_provider_exception_is_not_chained_or_rendered(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    sensitive_detail = f"{RAW_ENDPOINT} {RAW_BUCKET} {RAW_ACCESS_KEY} {RAW_SECRET_KEY}"

    class SensitiveDefiniteStorage(FakeObjectStorageService):
        def put_object(
            self,
            *,
            bucket: BucketName,
            key: ObjectKey,
            image: SanitizedImage,
        ):
            self.queue_put_outcome(FakeStorageOutcome.DEFINITE_FAILURE)
            try:
                raise RuntimeError(sensitive_detail)
            except RuntimeError:
                return super().put_object(bucket=bucket, key=key, image=image)

    storage = SensitiveDefiniteStorage()
    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                create_database_session_factory(m2_test_database),
                source=AsyncBytesSource(_png_bytes()),
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW,
                settings=_settings(m2_test_database),
                storage=storage,
            )
        )

    error = exc_info.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sensitive_detail not in f"{error!s} {error!r}"
    assert [call.operation for call in storage.calls] == [FakeStorageOperation.PUT]


@pytest.mark.integration
def test_ambiguous_accepted_put_heads_once_and_marks_same_row_available(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    storage = FakeObjectStorageService()
    storage.queue_put_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)

    result = asyncio.run(
        ingest_sanitized_image(
            create_database_session_factory(m2_test_database),
            source=AsyncBytesSource(_png_bytes()),
            actor_user_id=ACTOR_USER_ID,
            client_ip=CLIENT_IP,
            now=NOW,
            settings=_settings(m2_test_database),
            storage=storage,
        )
    )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert stored is not None
    assert stored.id == result.object_file_id
    assert stored.status == ObjectFileStatus.AVAILABLE.value
    assert stored.failure_code is None
    assert _object_count(m2_test_database) == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    assert storage.object_count == 1


@pytest.mark.integration
def test_ambiguous_missing_head_stays_pending_unknown_without_put_retry(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    storage = FakeObjectStorageService()
    storage.queue_put_outcome(FakeStorageOutcome.TIMEOUT)

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                create_database_session_factory(m2_test_database),
                source=AsyncBytesSource(_png_bytes()),
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW,
                settings=_settings(m2_test_database),
                storage=storage,
            )
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert exc_info.value.internal_code is StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN
    assert stored is not None
    assert stored.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert stored.failure_code == StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN.value
    assert stored.available_at is None
    assert stored.terminal_at is None
    assert _object_count(m2_test_database) == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    assert storage.object_count == 0


@pytest.mark.integration
def test_ambiguous_mismatch_deletes_object_and_marks_same_row_deleted(
    m2_test_database: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_actor(m2_test_database)
    storage = FakeObjectStorageService()
    storage.queue_put_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)
    storage.queue_head_outcome(FakeStorageOutcome.MISMATCH)

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                create_database_session_factory(m2_test_database),
                source=AsyncBytesSource(_png_bytes()),
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW,
                settings=_settings(m2_test_database),
                storage=storage,
            )
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    error = exc_info.value
    assert error.internal_code is StorageInternalCode.OBJECT_METADATA_MISMATCH
    assert stored is not None
    assert stored.status == ObjectFileStatus.DELETED.value
    assert stored.failure_code == StorageInternalCode.OBJECT_METADATA_MISMATCH.value
    assert stored.terminal_at == NOW
    assert stored.deleted_at == NOW
    assert _object_count(m2_test_database) == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.DELETE,
    ]
    assert storage.object_count == 0
    assert stored.object_key not in f"{error!s} {error!r} {caplog.text}"
    assert "http" not in f"{error!s} {error!r} {caplog.text}".casefold()


@pytest.mark.integration
def test_ambiguous_mismatch_delete_timeout_stays_delete_pending_unknown(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    storage = FakeObjectStorageService()
    storage.queue_put_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)
    storage.queue_head_outcome(FakeStorageOutcome.MISMATCH)
    storage.queue_delete_outcome(FakeStorageOutcome.TIMEOUT)

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                create_database_session_factory(m2_test_database),
                source=AsyncBytesSource(_png_bytes()),
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW,
                settings=_settings(m2_test_database),
                storage=storage,
            )
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert exc_info.value.internal_code is StorageInternalCode.DELETE_OUTCOME_UNKNOWN
    assert stored is not None
    assert stored.status == ObjectFileStatus.DELETE_PENDING.value
    assert stored.failure_code == StorageInternalCode.DELETE_OUTCOME_UNKNOWN.value
    assert stored.terminal_at is None
    assert stored.deleted_at is None
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.DELETE,
    ]
    assert storage.object_count == 1


@pytest.mark.integration
def test_ambiguous_exact_tx_s2_failure_does_not_repeat_put_or_hide_success(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)

    class FailingAmbiguousResultSession(Session):
        pass

    commit_count = 0

    def fail_third_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise OperationalError(
                "sensitive ambiguous result transition detail",
                {"secret": RAW_SECRET_KEY},
                RuntimeError(RAW_ENDPOINT),
            )

    event.listen(FailingAmbiguousResultSession, "before_commit", fail_third_commit)
    failing_factory = sessionmaker(
        bind=m2_test_database,
        class_=FailingAmbiguousResultSession,
    )
    storage = FakeObjectStorageService()
    storage.queue_put_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)
    try:
        with pytest.raises(StorageUploadError) as exc_info:
            asyncio.run(
                ingest_sanitized_image(
                    failing_factory,
                    source=AsyncBytesSource(_png_bytes()),
                    actor_user_id=ACTOR_USER_ID,
                    client_ip=CLIENT_IP,
                    now=NOW,
                    settings=_settings(m2_test_database),
                    storage=storage,
                )
            )
    finally:
        event.remove(
            FailingAmbiguousResultSession,
            "before_commit",
            fail_third_commit,
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    error = exc_info.value
    assert commit_count == 3
    assert error.code is ErrorCode.FILE_STORAGE_ERROR
    assert error.__cause__ is None
    assert error.__context__ is None
    assert stored is not None
    assert stored.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert stored.failure_code is None
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    assert storage.object_count == 1


@pytest.mark.integration
def test_configuration_failure_precedes_rate_source_row_and_provider(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    settings.object_storage_secret_key = None
    source = AsyncBytesSource(_png_bytes())
    storage = FakeObjectStorageService()

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            prepare_sanitized_image_upload(
                create_database_session_factory(m2_test_database),
                source=source,
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW,
                settings=settings,
            )
        )

    with create_database_session_factory(m2_test_database)() as session:
        rate_count = session.scalar(select(func.count()).select_from(AuthRateLimit))
    assert exc_info.value.code is ErrorCode.FILE_STORAGE_ERROR
    assert source.events == []
    assert rate_count == 0
    assert _object_count(m2_test_database) == 0
    assert storage.calls == ()


@pytest.mark.integration
def test_sanitization_failure_creates_no_object_or_provider_call(
    m2_test_database: Engine,
) -> None:
    storage = FakeObjectStorageService()

    with pytest.raises(ImageSanitizationError):
        asyncio.run(
            prepare_sanitized_image_upload(
                create_database_session_factory(m2_test_database),
                source=AsyncBytesSource(b"not-an-image"),
                actor_user_id=ACTOR_USER_ID,
                client_ip=CLIENT_IP,
                now=NOW,
                settings=_settings(m2_test_database),
            )
        )

    assert _object_count(m2_test_database) == 0
    assert storage.calls == ()


def test_prepare_surface_has_no_route_domain_owner_or_transaction_leak() -> None:
    source = inspect.getsource(storage_service)
    prepare_source = inspect.getsource(storage_service.prepare_sanitized_image_upload)

    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "owner_id" not in prepare_source
    assert "owner_type" not in prepare_source
    assert "domain_parent" not in prepare_source
    assert "filename" not in prepare_source
    assert "put_object" not in prepare_source
    assert "head_object" not in prepare_source
    assert ".commit(" not in prepare_source
    assert ".rollback(" not in prepare_source
    assert ".close(" not in prepare_source
    assert (
        "event"
        not in inspect.getsource(storage_service.ingest_sanitized_image).casefold()
    )
