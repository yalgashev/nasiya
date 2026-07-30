"""Development-only end-to-end storage acceptance coordinator."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from uuid import UUID

import httpx
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app.auth.error_codes import ErrorCode
from app.settings import Settings
from app.storage.contracts import (
    BucketName,
    ObjectFileAccessAuthorizer,
    ObjectKey,
    ObjectReadAuthorizationRequest,
    ObjectReadAuthorizationResult,
    ObjectStorageService,
    PresignedObjectUrl,
)
from app.storage.errors import StorageUploadError
from app.storage.models import ObjectFileStatus
from app.storage.repository import load_object_file
from app.storage.service import (
    create_authorized_presigned_get_url,
    delete_available_object,
    ingest_sanitized_image,
)
from app.telegram.client_ip import ResolvedClientIp

STORAGE_SMOKE_EXPECTED_CHECKS = 8
STORAGE_SMOKE_HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, repr=False)
class FetchedSmokeObject:
    content_type: str
    payload: bytes = field(repr=False)

    def __repr__(self) -> str:
        return (
            "FetchedSmokeObject("
            f"content_type={self.content_type!r}, "
            f"size_bytes={len(self.payload)!r}, payload=<redacted>"
            ")"
        )


@dataclass(frozen=True)
class StorageSmokeResult:
    passed_checks: int
    expected_checks: int


@dataclass(frozen=True, repr=False)
class _DeletedObjectTarget:
    bucket: BucketName
    object_key: ObjectKey

    def __repr__(self) -> str:
        return "_DeletedObjectTarget(bucket=<redacted>, object_key=<redacted>)"


class _AsyncSyntheticImageSource:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._position = 0

    async def seek(self, offset: int) -> None:
        self._position = offset

    async def read(self, size: int) -> bytes:
        chunk = self._payload[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


class _SmokeObjectAuthorizer:
    def __init__(self, *, actor_user_id: UUID, object_file_id: UUID) -> None:
        self._actor_user_id = actor_user_id
        self._object_file_id = object_file_id

    def authorize(
        self,
        request: ObjectReadAuthorizationRequest,
    ) -> ObjectReadAuthorizationResult:
        if (
            request.actor_user_id == self._actor_user_id
            and request.object_file_id == self._object_file_id
            and request.domain_parent_reference == self._object_file_id
        ):
            return ObjectReadAuthorizationResult.ALLOWED
        return ObjectReadAuthorizationResult.DENIED


assert isinstance(
    _SmokeObjectAuthorizer(
        actor_user_id=UUID(int=1),
        object_file_id=UUID(int=2),
    ),
    ObjectFileAccessAuthorizer,
)


async def run_storage_smoke(
    session_factory: sessionmaker[Session],
    *,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
    settings: Settings,
    storage: ObjectStorageService,
    fetch_presigned: Callable[[PresignedObjectUrl, int], FetchedSmokeObject],
) -> StorageSmokeResult:
    ingested_object_id: UUID | None = None
    deleted = False
    smoke_failed = False
    try:
        ingested = await ingest_sanitized_image(
            session_factory,
            source=_AsyncSyntheticImageSource(_synthetic_png_bytes()),
            actor_user_id=actor_user_id,
            client_ip=client_ip,
            now=now,
            settings=settings,
            storage=storage,
        )
        ingested_object_id = ingested.object_file_id
        request = ObjectReadAuthorizationRequest(
            actor_user_id=actor_user_id,
            object_file_id=ingested.object_file_id,
            domain_parent_reference=ingested.object_file_id,
        )
        presigned_url = create_authorized_presigned_get_url(
            session_factory,
            request=request,
            authorizer=_SmokeObjectAuthorizer(
                actor_user_id=actor_user_id,
                object_file_id=ingested.object_file_id,
            ),
            storage=storage,
            settings=settings,
        )
        fetched = fetch_presigned(
            presigned_url,
            settings.object_storage_max_upload_bytes,
        )
        if (
            fetched.content_type != ingested.content_type
            or len(fetched.payload) != ingested.size_bytes
            or sha256(fetched.payload).hexdigest()
            != ingested.checksum_sha256.as_internal_value()
        ):
            raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR)

        delete_result = delete_available_object(
            session_factory,
            object_file_id=ingested.object_file_id,
            storage=storage,
            now=now,
        )
        if delete_result.status is not ObjectFileStatus.DELETED:
            raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR)
        deleted = True
        target = _load_deleted_target(
            session_factory,
            object_file_id=ingested.object_file_id,
        )
        if (
            storage.head_object(
                bucket=target.bucket,
                key=target.object_key,
            )
            is not None
        ):
            raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR)
    except Exception:
        smoke_failed = True

    if smoke_failed:
        if ingested_object_id is not None and not deleted:
            _best_effort_delete(
                session_factory,
                object_file_id=ingested_object_id,
                storage=storage,
                now=now,
            )
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
    return StorageSmokeResult(
        passed_checks=STORAGE_SMOKE_EXPECTED_CHECKS,
        expected_checks=STORAGE_SMOKE_EXPECTED_CHECKS,
    )


def fetch_presigned_smoke_object(
    presigned_url: PresignedObjectUrl,
    max_bytes: int,
) -> FetchedSmokeObject:
    if not isinstance(presigned_url, PresignedObjectUrl):
        raise TypeError("Storage smoke URL must be redacted and typed")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("Storage smoke fetch limit must be positive")

    payload = bytearray()
    content_type = ""
    with httpx.Client(
        timeout=STORAGE_SMOKE_HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        with client.stream(
            "GET",
            presigned_url.as_response_value(),
        ) as response:
            if response.status_code != 200:
                raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR)
            content_type = response.headers.get("content-type", "").partition(";")[0]
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR)
    if not payload:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR)
    return FetchedSmokeObject(
        content_type=content_type,
        payload=bytes(payload),
    )


def _synthetic_png_bytes() -> bytes:
    output = BytesIO()
    with Image.new("RGBA", (7, 5), (30, 90, 150, 175)) as image:
        image.save(
            output,
            format="PNG",
            pnginfo=None,
            optimize=False,
        )
    return output.getvalue()


def _load_deleted_target(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
) -> _DeletedObjectTarget:
    with session_factory() as session:
        object_file = load_object_file(
            session,
            object_file_id=object_file_id,
        )
        if object_file is None or object_file.status != ObjectFileStatus.DELETED.value:
            raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR)
        return _DeletedObjectTarget(
            bucket=BucketName(object_file.bucket),
            object_key=ObjectKey(object_file.object_key),
        )


def _best_effort_delete(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    storage: ObjectStorageService,
    now: datetime,
) -> None:
    try:
        delete_available_object(
            session_factory,
            object_file_id=object_file_id,
            storage=storage,
            now=now,
        )
    except Exception:
        pass
