import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.engine import Engine

import app.storage.service as storage_service
from alembic import command
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    ObjectReadAuthorizationRequest,
    ObjectReadAuthorizationResult,
    SanitizedImageMetadata,
)
from app.storage.errors import (
    StorageAccessDeniedError,
    StorageInternalCode,
    StorageUploadError,
)
from app.storage.models import ObjectFileStatus
from app.storage.repository import (
    create_pending_object_file,
    mark_object_file_available,
    mark_object_file_delete_pending,
    mark_object_file_deleted,
    mark_object_file_failed,
)
from app.storage.service import create_authorized_presigned_get_url
from tests.storage_fake import FakeObjectStorageService, FakeStorageOperation

NOW = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-authorized-storage-read"


@pytest.fixture(scope="module", autouse=True)
def ensure_m8_schema(test_database_engine: Engine) -> None:
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")


@dataclass
class FakeAuthorizer:
    result: ObjectReadAuthorizationResult

    def __post_init__(self) -> None:
        self.calls: list[ObjectReadAuthorizationRequest] = []

    def authorize(
        self,
        request: ObjectReadAuthorizationRequest,
    ) -> ObjectReadAuthorizationResult:
        self.calls.append(request)
        return self.result


class ParentBindingAuthorizer:
    def __init__(
        self,
        *,
        expected_actor_user_id: UUID,
        expected_object_file_id: UUID,
    ) -> None:
        self.expected_actor_user_id = expected_actor_user_id
        self.expected_object_file_id = expected_object_file_id
        self.calls = 0

    def authorize(
        self,
        request: ObjectReadAuthorizationRequest,
    ) -> ObjectReadAuthorizationResult:
        self.calls += 1
        if (
            request.actor_user_id == self.expected_actor_user_id
            and request.object_file_id == self.expected_object_file_id
            and request.domain_parent_reference == self.expected_object_file_id
        ):
            return ObjectReadAuthorizationResult.ALLOWED
        return ObjectReadAuthorizationResult.DENIED


def _seed_object(
    engine: Engine,
    *,
    available: bool,
) -> tuple[UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        user = User(phone="+998900008411", created_at=NOW, updated_at=NOW)
        session.add(user)
        session.flush()
        object_file_id = uuid4()
        object_file = create_pending_object_file(
            session,
            object_file_id=object_file_id,
            bucket=BucketName("nasiya-private-test"),
            object_key=ObjectKey(f"v1/objects/{object_file_id.hex}.png"),
            metadata=SanitizedImageMetadata(
                content_type="image/png",
                canonical_extension="png",
                size_bytes=32,
                width_px=4,
                height_px=3,
                checksum_sha256=ObjectChecksumSha256("e" * 64),
            ),
            created_by_user_id=user.id,
            now=NOW,
        )
        if available:
            mark_object_file_available(
                session,
                object_file_id=object_file.id,
                now=NOW,
            )
        return user.id, object_file.id


def _request(
    *,
    actor_user_id: UUID,
    object_file_id: UUID,
    parent_reference: object,
) -> ObjectReadAuthorizationRequest:
    return ObjectReadAuthorizationRequest(
        actor_user_id=actor_user_id,
        object_file_id=object_file_id,
        domain_parent_reference=parent_reference,
    )


def _settings(*, ttl_seconds: int = 300) -> Settings:
    return Settings(
        _env_file=None,
        debug=False,
        database_url="postgresql+psycopg://nasiya:test@127.0.0.1/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        object_storage_endpoint_url="https://storage-read.invalid",
        object_storage_region="region-1",
        object_storage_bucket="nasiya-private-test",
        object_storage_access_key="synthetic-storage-read-access",
        object_storage_secret_key="synthetic-storage-read-secret",
        object_storage_use_ssl=True,
        object_storage_presigned_ttl_seconds=ttl_seconds,
    )


def _seed_terminal_object(
    engine: Engine,
    *,
    status: ObjectFileStatus,
) -> tuple[UUID, UUID]:
    actor_id, object_file_id = _seed_object(engine, available=False)
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        if status is ObjectFileStatus.FAILED:
            mark_object_file_failed(
                session,
                object_file_id=object_file_id,
                failure_code=StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
                now=NOW,
            )
        elif status is ObjectFileStatus.DELETED:
            mark_object_file_delete_pending(
                session,
                object_file_id=object_file_id,
                failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
                now=NOW,
            )
            mark_object_file_deleted(
                session,
                object_file_id=object_file_id,
                now=NOW,
            )
        else:
            raise AssertionError("Unsupported terminal test status")
    return actor_id, object_file_id


@pytest.mark.integration
def test_allowed_available_object_presigns_after_authorization(
    m2_test_database: Engine,
) -> None:
    actor_id, object_file_id = _seed_object(
        m2_test_database,
        available=True,
    )
    authorizer = ParentBindingAuthorizer(
        expected_actor_user_id=actor_id,
        expected_object_file_id=object_file_id,
    )
    fake_storage = FakeObjectStorageService()

    url = create_authorized_presigned_get_url(
        create_database_session_factory(m2_test_database),
        request=_request(
            actor_user_id=actor_id,
            object_file_id=object_file_id,
            parent_reference=object_file_id,
        ),
        authorizer=authorizer,
        storage=fake_storage,
        settings=_settings(),
    )

    assert authorizer.calls == 1
    assert [call.operation for call in fake_storage.calls] == [
        FakeStorageOperation.PRESIGN_GET
    ]
    assert fake_storage.calls[0].ttl_seconds == 300
    assert all(
        call.operation is not FakeStorageOperation.HEAD for call in fake_storage.calls
    )
    assert "presigned-test" not in repr(url)


@pytest.mark.integration
def test_creator_denied_performs_zero_sdk_calls(
    m2_test_database: Engine,
) -> None:
    creator_id, object_file_id = _seed_object(
        m2_test_database,
        available=True,
    )
    authorizer = FakeAuthorizer(ObjectReadAuthorizationResult.DENIED)
    fake_storage = FakeObjectStorageService()

    with pytest.raises(StorageAccessDeniedError) as exc_info:
        create_authorized_presigned_get_url(
            create_database_session_factory(m2_test_database),
            request=_request(
                actor_user_id=creator_id,
                object_file_id=object_file_id,
                parent_reference=object_file_id,
            ),
            authorizer=authorizer,
            storage=fake_storage,
            settings=_settings(),
        )

    assert exc_info.value.code is ErrorCode.FILE_ACCESS_DENIED
    assert len(authorizer.calls) == 1
    assert fake_storage.calls == ()


@pytest.mark.integration
def test_foreign_parent_object_is_denied_before_sdk(
    m2_test_database: Engine,
) -> None:
    actor_id, object_file_id = _seed_object(
        m2_test_database,
        available=True,
    )
    foreign_object_id = uuid4()
    authorizer = ParentBindingAuthorizer(
        expected_actor_user_id=actor_id,
        expected_object_file_id=object_file_id,
    )
    fake_storage = FakeObjectStorageService()

    with pytest.raises(StorageAccessDeniedError):
        create_authorized_presigned_get_url(
            create_database_session_factory(m2_test_database),
            request=_request(
                actor_user_id=actor_id,
                object_file_id=object_file_id,
                parent_reference=foreign_object_id,
            ),
            authorizer=authorizer,
            storage=fake_storage,
            settings=_settings(),
        )

    assert authorizer.calls == 1
    assert fake_storage.calls == ()


@pytest.mark.integration
def test_foreign_actor_is_denied_before_sdk(
    m2_test_database: Engine,
) -> None:
    actor_id, object_file_id = _seed_object(
        m2_test_database,
        available=True,
    )
    authorizer = ParentBindingAuthorizer(
        expected_actor_user_id=actor_id,
        expected_object_file_id=object_file_id,
    )
    fake_storage = FakeObjectStorageService()

    with pytest.raises(StorageAccessDeniedError):
        create_authorized_presigned_get_url(
            create_database_session_factory(m2_test_database),
            request=_request(
                actor_user_id=uuid4(),
                object_file_id=object_file_id,
                parent_reference=object_file_id,
            ),
            authorizer=authorizer,
            storage=fake_storage,
            settings=_settings(),
        )

    assert authorizer.calls == 1
    assert fake_storage.calls == ()


@pytest.mark.integration
@pytest.mark.parametrize("available", (False, True))
def test_missing_and_non_available_are_same_closed_access_error(
    m2_test_database: Engine,
    available: bool,
) -> None:
    actor_id, stored_object_id = _seed_object(
        m2_test_database,
        available=available,
    )
    requested_object_id = stored_object_id if not available else uuid4()
    fake_storage = FakeObjectStorageService()

    with pytest.raises(StorageAccessDeniedError) as exc_info:
        create_authorized_presigned_get_url(
            create_database_session_factory(m2_test_database),
            request=_request(
                actor_user_id=actor_id,
                object_file_id=requested_object_id,
                parent_reference=requested_object_id,
            ),
            authorizer=FakeAuthorizer(ObjectReadAuthorizationResult.ALLOWED),
            storage=fake_storage,
            settings=_settings(),
        )

    assert exc_info.value.code is ErrorCode.FILE_ACCESS_DENIED
    assert str(exc_info.value) == ErrorCode.FILE_ACCESS_DENIED.value
    assert fake_storage.calls == ()


@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    (ObjectFileStatus.FAILED, ObjectFileStatus.DELETED),
)
def test_failed_and_deleted_objects_are_same_closed_access_error(
    m2_test_database: Engine,
    status: ObjectFileStatus,
) -> None:
    actor_id, object_file_id = _seed_terminal_object(
        m2_test_database,
        status=status,
    )
    fake_storage = FakeObjectStorageService()

    with pytest.raises(StorageAccessDeniedError) as exc_info:
        create_authorized_presigned_get_url(
            create_database_session_factory(m2_test_database),
            request=_request(
                actor_user_id=actor_id,
                object_file_id=object_file_id,
                parent_reference=object_file_id,
            ),
            authorizer=FakeAuthorizer(ObjectReadAuthorizationResult.ALLOWED),
            storage=fake_storage,
            settings=_settings(),
        )

    assert exc_info.value.code is ErrorCode.FILE_ACCESS_DENIED
    assert fake_storage.calls == ()


@pytest.mark.integration
@pytest.mark.parametrize("ttl_seconds", (60, 600, 900))
def test_presign_uses_current_configured_expiry(
    m2_test_database: Engine,
    ttl_seconds: int,
) -> None:
    actor_id, object_file_id = _seed_object(
        m2_test_database,
        available=True,
    )
    fake_storage = FakeObjectStorageService()

    create_authorized_presigned_get_url(
        create_database_session_factory(m2_test_database),
        request=_request(
            actor_user_id=actor_id,
            object_file_id=object_file_id,
            parent_reference=object_file_id,
        ),
        authorizer=FakeAuthorizer(ObjectReadAuthorizationResult.ALLOWED),
        storage=fake_storage,
        settings=_settings(ttl_seconds=ttl_seconds),
    )

    assert fake_storage.calls[0].ttl_seconds == ttl_seconds


@pytest.mark.integration
def test_unavailable_presign_config_fails_closed_without_sdk_call(
    m2_test_database: Engine,
) -> None:
    actor_id, object_file_id = _seed_object(
        m2_test_database,
        available=True,
    )
    settings = _settings()
    settings.object_storage_secret_key = None
    fake_storage = FakeObjectStorageService()

    with pytest.raises(StorageUploadError) as exc_info:
        create_authorized_presigned_get_url(
            create_database_session_factory(m2_test_database),
            request=_request(
                actor_user_id=actor_id,
                object_file_id=object_file_id,
                parent_reference=object_file_id,
            ),
            authorizer=FakeAuthorizer(ObjectReadAuthorizationResult.ALLOWED),
            storage=fake_storage,
            settings=settings,
        )

    assert exc_info.value.code is ErrorCode.FILE_STORAGE_ERROR
    assert fake_storage.calls == ()


def test_coordinator_closes_db_phase_before_presign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    object_file_id = uuid4()

    class SessionContext:
        def __enter__(self) -> object:
            events.append("db_open")
            return object()

        def __exit__(self, *_args: object) -> None:
            events.append("db_closed")

    class SessionFactory:
        def __call__(self) -> SessionContext:
            return SessionContext()

    class AvailableObject:
        id = object_file_id
        bucket = "nasiya-private-test"
        object_key = f"v1/objects/{object_file_id.hex}.png"

    class OrderedStorage(FakeObjectStorageService):
        def create_presigned_get_url(self, **kwargs):
            events.append("presign")
            return super().create_presigned_get_url(**kwargs)

    class OrderedAuthorizer(FakeAuthorizer):
        def authorize(self, request):
            events.append("authorize")
            return super().authorize(request)

    monkeypatch.setattr(
        storage_service,
        "load_available_object_file",
        lambda *_args, **_kwargs: AvailableObject(),
    )

    create_authorized_presigned_get_url(
        SessionFactory(),  # type: ignore[arg-type]
        request=_request(
            actor_user_id=uuid4(),
            object_file_id=object_file_id,
            parent_reference=object_file_id,
        ),
        authorizer=OrderedAuthorizer(ObjectReadAuthorizationResult.ALLOWED),
        storage=OrderedStorage(),
        settings=_settings(),
    )

    assert events == ["authorize", "db_open", "db_closed", "presign"]


@pytest.mark.integration
def test_presigned_url_is_not_persisted_or_logged(
    m2_test_database: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    actor_id, object_file_id = _seed_object(
        m2_test_database,
        available=True,
    )
    fake_storage = FakeObjectStorageService()
    raw_url = create_authorized_presigned_get_url(
        create_database_session_factory(m2_test_database),
        request=_request(
            actor_user_id=actor_id,
            object_file_id=object_file_id,
            parent_reference=object_file_id,
        ),
        authorizer=FakeAuthorizer(ObjectReadAuthorizationResult.ALLOWED),
        storage=fake_storage,
        settings=_settings(),
    ).as_response_value()

    columns = {
        column["name"]
        for column in sqlalchemy_inspect(m2_test_database).get_columns("object_files")
    }
    assert "url" not in columns
    assert "presigned_url" not in columns
    assert raw_url not in caplog.text


def test_service_has_no_route_log_or_creator_authorization_shortcut() -> None:
    source = inspect.getsource(storage_service)
    presign_source = inspect.getsource(
        storage_service.create_authorized_presigned_get_url
    )

    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "logger" not in source
    assert "logging" not in source
    assert "print(" not in source
    assert "created_by_user_id" not in presign_source
    assert "head_object" not in presign_source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
