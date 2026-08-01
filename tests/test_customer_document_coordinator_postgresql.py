import asyncio
import inspect
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_document.contracts import (
    CustomerDocumentActor,
    CustomerDocumentAttachment,
    CustomerDocumentStatus,
    CustomerDocumentSubmissionId,
    ExpectedCurrentCustomerDocument,
    UploadOwnCustomerDocument,
)
from app.customer_document.coordinator import (
    CustomerDocumentServiceError,
    upload_and_attach_own_customer_document,
)
from app.customer_document.models import CustomerDocument
from app.customer_document.repository import SqlAlchemyCustomerDocumentRepository
from app.customer_document.service import (
    create_own_current_customer_document_url,
    has_current_customer_identity_document,
)
from app.db import create_database_session_factory
from app.settings import Settings
from app.storage.errors import StorageAccessDeniedError, StorageUploadError
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.rate_limit import record_storage_upload_attempt
from app.storage.repository import load_object_file_for_update
from app.telegram.client_ip import ResolvedClientIp
from tests.storage_fake import FakeObjectStorageService, FakeStorageOperation

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)
CLIENT_IP = ResolvedClientIp("203.0.113.210")
RATE_LIMIT_KEY = "m10-document-rate-limit-key-not-sensitive"


class AsyncBytesSource:
    def __init__(
        self,
        payload: bytes,
        *,
        before_first_read: Callable[[], None] | None = None,
        assert_no_session: Callable[[], None] | None = None,
    ) -> None:
        self._payload = payload
        self._position = 0
        self._before_first_read = before_first_read
        self._assert_no_session = assert_no_session
        self.read_count = 0

    async def seek(self, offset: int) -> None:
        if self._assert_no_session is not None:
            self._assert_no_session()
        self._position = offset

    async def read(self, size: int) -> bytes:
        if self._assert_no_session is not None:
            self._assert_no_session()
        if self.read_count == 0 and self._before_first_read is not None:
            self._before_first_read()
        self.read_count += 1
        chunk = self._payload[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


class SessionCheckingStorage(FakeObjectStorageService):
    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    def put_object(self, **kwargs):
        _assert_no_checked_out_connection(self._engine)
        return super().put_object(**kwargs)

    def head_object(self, **kwargs):
        _assert_no_checked_out_connection(self._engine)
        return super().head_object(**kwargs)

    def create_presigned_get_url(self, **kwargs):
        _assert_no_checked_out_connection(self._engine)
        return super().create_presigned_get_url(**kwargs)


def _png_bytes() -> bytes:
    output = BytesIO()
    with Image.new("RGBA", (4, 3), (20, 40, 60, 128)) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _settings(engine: Engine, *, attempts: int = 5) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        object_storage_endpoint_url="https://m10-storage.invalid",
        object_storage_region="region-1",
        object_storage_bucket="m10-private-documents",
        object_storage_access_key="m10-test-access",
        object_storage_secret_key="m10-test-secret",
        object_storage_use_ssl=True,
        object_storage_upload_rate_limit_user_attempts=attempts,
        object_storage_upload_rate_limit_ip_attempts=attempts,
    )


def _seed_owner(
    engine: Engine,
    *,
    phone: str,
    is_platform_admin: bool = False,
) -> tuple[UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        user = User(
            phone=phone,
            is_active=True,
            is_platform_admin=is_platform_admin,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(user)
        session.flush()
        customer = Customer(
            user_id=user.id,
            onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(customer)
        session.flush()
        return user.id, customer.id


def _command(
    actor_id: UUID,
    *,
    submission_id: UUID | None = None,
    expected_document_id: UUID | None = None,
) -> UploadOwnCustomerDocument:
    return UploadOwnCustomerDocument(
        actor=CustomerDocumentActor(actor_id),
        submission_id=CustomerDocumentSubmissionId(submission_id or uuid4()),
        expected_current=ExpectedCurrentCustomerDocument(expected_document_id),
    )


def _run_upload(
    engine: Engine,
    *,
    command: UploadOwnCustomerDocument,
    source: AsyncBytesSource,
    storage: FakeObjectStorageService,
    settings: Settings,
):
    return asyncio.run(
        upload_and_attach_own_customer_document(
            create_database_session_factory(engine),
            command=command,
            source=source,
            client_ip=CLIENT_IP,
            now=NOW,
            settings=settings,
            storage=storage,
        )
    )


def _seed_available_object(engine: Engine, *, actor_id: UUID) -> UUID:
    object_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            ObjectFile(
                id=object_id,
                bucket="m10-private-documents",
                object_key=f"v1/objects/{object_id.hex}.png",
                content_type="image/png",
                size_bytes=128,
                checksum_sha256="a" * 64,
                width_px=4,
                height_px=3,
                status=ObjectFileStatus.AVAILABLE.value,
                created_by_user_id=actor_id,
                failure_code=None,
                created_at=NOW,
                updated_at=NOW,
                available_at=NOW,
                terminal_at=None,
                deleted_at=None,
            )
        )
    return object_id


def test_upload_attach_and_completed_replay_use_one_m8_attempt(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id = _seed_owner(
        m2_test_database,
        phone="+998900001301",
    )
    command = _command(actor_id)
    storage = SessionCheckingStorage(m2_test_database)
    settings = _settings(m2_test_database)
    first_source = AsyncBytesSource(
        _png_bytes(),
        assert_no_session=lambda: _assert_no_checked_out_connection(m2_test_database),
    )

    first = _run_upload(
        m2_test_database,
        command=command,
        source=first_source,
        storage=storage,
        settings=settings,
    )
    replay_source = AsyncBytesSource(b"must-not-be-read")
    replay = _run_upload(
        m2_test_database,
        command=command,
        source=replay_source,
        storage=storage,
        settings=settings,
    )

    assert first.submission_replayed is False
    assert replay.document_id == first.document_id
    assert replay.submission_replayed is True
    assert replay_source.read_count == 0
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(CustomerDocument)) == 1
        assert session.scalar(select(func.count()).select_from(AuthRateLimit)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.CUSTOMER_DOCUMENT_ATTACHED.value
                )
            )
            == 1
        )
        assert has_current_customer_identity_document(
            session,
            customer_id=customer_id,
        )


def test_early_rate_limit_denial_reads_no_source_and_calls_no_provider(
    m2_test_database: Engine,
) -> None:
    actor_id, _ = _seed_owner(m2_test_database, phone="+998900001302")
    settings = _settings(m2_test_database, attempts=1)
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        record_storage_upload_attempt(
            session,
            settings,
            actor_id,
            CLIENT_IP,
            NOW,
        )
        record_storage_upload_attempt(
            session,
            settings,
            actor_id,
            CLIENT_IP,
            NOW,
        )
    source = AsyncBytesSource(_png_bytes())
    storage = FakeObjectStorageService()

    with pytest.raises(StorageUploadError) as exc_info:
        _run_upload(
            m2_test_database,
            command=_command(actor_id),
            source=source,
            storage=storage,
            settings=settings,
        )

    assert exc_info.value.code is ErrorCode.RATE_LIMITED
    assert source.read_count == 0
    assert storage.calls == ()
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(ObjectFile)) == 0


def test_post_ingest_stale_failure_claims_orphan_without_provider_delete(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id = _seed_owner(
        m2_test_database,
        phone="+998900001303",
    )
    competing_object_id = _seed_available_object(
        m2_test_database,
        actor_id=actor_id,
    )

    def attach_competing_current() -> None:
        with Session(m2_test_database) as session, session.begin():
            assert (
                load_object_file_for_update(
                    session,
                    object_file_id=competing_object_id,
                )
                is not None
            )
            SqlAlchemyCustomerDocumentRepository(session).attach_current_document(
                attachment=CustomerDocumentAttachment(
                    id=uuid4(),
                    customer_id=customer_id,
                    object_file_id=competing_object_id,
                    submission_id=CustomerDocumentSubmissionId(uuid4()),
                    status=CustomerDocumentStatus.CURRENT,
                    attached_by_user_id=actor_id,
                    attached_at=NOW,
                ),
                expected_current=ExpectedCurrentCustomerDocument(None),
            )

    source = AsyncBytesSource(
        _png_bytes(),
        before_first_read=attach_competing_current,
    )
    storage = FakeObjectStorageService()

    with pytest.raises(CustomerDocumentServiceError) as exc_info:
        _run_upload(
            m2_test_database,
            command=_command(actor_id),
            source=source,
            storage=storage,
            settings=_settings(m2_test_database),
        )

    assert exc_info.value.code is ErrorCode.CUSTOMER_DOCUMENT_CHANGED
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]
    with Session(m2_test_database) as session:
        statuses = tuple(
            session.scalars(select(ObjectFile.status).order_by(ObjectFile.created_at))
        )
        assert sorted(statuses) == sorted(
            [ObjectFileStatus.AVAILABLE.value, ObjectFileStatus.DELETE_PENDING.value]
        )
        assert session.scalar(select(func.count()).select_from(CustomerDocument)) == 1


def test_concurrent_same_submission_converges_and_claims_only_loser_object(
    m2_test_database: Engine,
) -> None:
    actor_id, _ = _seed_owner(m2_test_database, phone="+998900001306")
    command = _command(actor_id)
    settings = _settings(m2_test_database)
    barrier = Barrier(2)
    storages = (FakeObjectStorageService(), FakeObjectStorageService())

    def worker(storage: FakeObjectStorageService):
        return _run_upload(
            m2_test_database,
            command=command,
            source=AsyncBytesSource(
                _png_bytes(),
                before_first_read=lambda: barrier.wait(timeout=10),
            ),
            storage=storage,
            settings=settings,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(worker, storages))

    assert results[0].document_id == results[1].document_id
    assert sorted(result.submission_replayed for result in results) == [False, True]
    assert all(
        [call.operation for call in storage.calls]
        == [FakeStorageOperation.PUT, FakeStorageOperation.HEAD]
        for storage in storages
    )
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(CustomerDocument)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type
                    == AuditEventType.CUSTOMER_DOCUMENT_ATTACHED.value
                )
            )
            == 1
        )
        statuses = tuple(session.scalars(select(ObjectFile.status)))
        assert sorted(statuses) == sorted(
            [ObjectFileStatus.AVAILABLE.value, ObjectFileStatus.DELETE_PENDING.value]
        )


def test_own_current_access_presigns_after_authorization_and_audits(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id = _seed_owner(
        m2_test_database,
        phone="+998900001304",
    )
    other_actor_id, _ = _seed_owner(
        m2_test_database,
        phone="+998900001305",
        is_platform_admin=True,
    )
    storage = SessionCheckingStorage(m2_test_database)
    settings = _settings(m2_test_database)
    attached = _run_upload(
        m2_test_database,
        command=_command(actor_id),
        source=AsyncBytesSource(_png_bytes()),
        storage=storage,
        settings=settings,
    )

    url = create_own_current_customer_document_url(
        create_database_session_factory(m2_test_database),
        actor=CustomerDocumentActor(actor_id),
        storage=storage,
        settings=settings,
        now=NOW,
    )
    assert url.as_response_value().startswith("https://")
    assert storage.calls[-1].operation is FakeStorageOperation.PRESIGN_GET
    assert storage.calls[-1].ttl_seconds == 300
    calls_before_denial = storage.calls
    with pytest.raises(StorageAccessDeniedError):
        create_own_current_customer_document_url(
            create_database_session_factory(m2_test_database),
            actor=CustomerDocumentActor(other_actor_id),
            storage=storage,
            settings=settings,
            now=NOW,
        )
    assert storage.calls == calls_before_denial
    with Session(m2_test_database) as session:
        access_audit = session.scalar(
            select(AuditLog).where(
                AuditLog.event_type
                == AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED.value
            )
        )
        assert access_audit is not None
        assert access_audit.object_id == attached.document_id
        assert access_audit.payload == {"ttl_seconds": 300}
        assert has_current_customer_identity_document(
            session,
            customer_id=customer_id,
        )


def test_document_coordinator_reuses_only_public_m8_composition_boundaries() -> None:
    from app.customer_document import coordinator

    source = inspect.getsource(coordinator)
    for forbidden in (
        "record_storage_upload_attempt",
        "delete_available_object",
        "reconcile_stale_object_deletes",
        "_delete_",
        "presigned_put",
        ".commit(",
        ".rollback(",
        ".close(",
        "ShopRole",
        "is_platform_admin",
    ):
        assert forbidden not in source
    assert "ingest_sanitized_image" in source
    assert "check_storage_upload_rate_limit" in source
    assert "claim_unattached_object_for_compensation" in source


def _assert_no_checked_out_connection(engine: Engine) -> None:
    checked_out = getattr(engine.pool, "checkedout", None)
    if callable(checked_out):
        assert checked_out() == 0
