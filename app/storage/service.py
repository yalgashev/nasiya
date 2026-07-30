"""Explicit storage coordinators that keep SDK calls outside DB phases."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.storage.contracts import (
    BucketName,
    ObjectFileAccessAuthorizer,
    ObjectKey,
    ObjectReadAuthorizationRequest,
    ObjectReadAuthorizationResult,
    ObjectStorageService,
    PresignedObjectUrl,
)
from app.storage.errors import StorageAccessDeniedError
from app.storage.repository import load_available_object_file


@dataclass(frozen=True, repr=False)
class _AuthorizedObjectRead:
    object_file_id: UUID
    bucket: BucketName
    object_key: ObjectKey

    def __repr__(self) -> str:
        return (
            "_AuthorizedObjectRead("
            f"object_file_id={self.object_file_id!r}, "
            "bucket=<redacted>, object_key=<redacted>"
            ")"
        )


def create_authorized_presigned_get_url(
    session_factory: sessionmaker[Session],
    *,
    request: ObjectReadAuthorizationRequest,
    authorizer: ObjectFileAccessAuthorizer,
    storage: ObjectStorageService,
    ttl_seconds: int,
) -> PresignedObjectUrl:
    authorization = authorizer.authorize(request)
    if authorization is not ObjectReadAuthorizationResult.ALLOWED:
        raise StorageAccessDeniedError

    with session_factory() as session:
        object_file = load_available_object_file(
            session,
            object_file_id=request.object_file_id,
        )
        if object_file is None:
            raise StorageAccessDeniedError
        authorized_object = _AuthorizedObjectRead(
            object_file_id=object_file.id,
            bucket=BucketName(object_file.bucket),
            object_key=ObjectKey(object_file.object_key),
        )

    return storage.create_presigned_get_url(
        bucket=authorized_object.bucket,
        key=authorized_object.object_key,
        ttl_seconds=ttl_seconds,
    )
