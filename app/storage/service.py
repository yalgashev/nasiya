"""Explicit storage coordinators that keep SDK calls outside DB phases."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.error_codes import ErrorCode
from app.settings import ObjectStorageSettingsError, Settings
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectFileAccessAuthorizer,
    ObjectKey,
    ObjectReadAuthorizationRequest,
    ObjectReadAuthorizationResult,
    ObjectStorageService,
    PresignedObjectUrl,
    SanitizedImage,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
    StoredObjectHead,
)
from app.storage.errors import (
    StorageAccessDeniedError,
    StorageInternalCode,
    StorageUploadError,
)
from app.storage.image import (
    AsyncImageSource,
    ImageDimensionLimits,
    generate_object_key,
    read_bounded_image,
    sanitize_bounded_image,
)
from app.storage.models import ObjectFileStatus
from app.storage.rate_limit import (
    StorageUploadRateLimitResult,
    record_storage_upload_attempt,
)
from app.storage.repository import (
    OBJECT_FILE_CLAIM_MAX_BATCH_SIZE,
    ClaimedObjectFile,
    ObjectFileStateError,
    claim_stale_delete_pending,
    claim_stale_pending_uploads,
    create_pending_object_file,
    load_available_object_file,
    load_object_file_for_update,
    mark_delete_outcome_unknown,
    mark_object_file_available,
    mark_object_file_delete_pending,
    mark_object_file_deleted,
    mark_object_file_failed,
    mark_pending_upload_outcome_unknown,
)
from app.telegram.client_ip import ResolvedClientIp


@dataclass(frozen=True, repr=False)
class PreparedImageUpload:
    object_file_id: UUID
    bucket: BucketName
    object_key: ObjectKey
    image: SanitizedImage

    def __repr__(self) -> str:
        metadata = self.image.metadata
        return (
            "PreparedImageUpload("
            f"object_file_id={self.object_file_id!r}, "
            "bucket=<redacted>, object_key=<redacted>, "
            f"content_type={metadata.content_type!r}, "
            f"size_bytes={metadata.size_bytes!r}, "
            f"width_px={metadata.width_px!r}, "
            f"height_px={metadata.height_px!r}, "
            "checksum_sha256=<redacted>, image=<redacted>"
            ")"
        )


@dataclass(frozen=True, repr=False)
class IngestedImageResult:
    object_file_id: UUID
    content_type: str
    size_bytes: int
    width_px: int
    height_px: int
    checksum_sha256: ObjectChecksumSha256

    def __repr__(self) -> str:
        return (
            "IngestedImageResult("
            f"object_file_id={self.object_file_id!r}, "
            f"content_type={self.content_type!r}, "
            f"size_bytes={self.size_bytes!r}, "
            f"width_px={self.width_px!r}, "
            f"height_px={self.height_px!r}, "
            "checksum_sha256=<redacted>"
            ")"
        )


@dataclass(frozen=True, repr=False)
class StorageReconcileResult:
    claimed_count: int
    available_count: int
    failed_count: int
    deleted_count: int
    pending_count: int
    delete_pending_count: int
    safe_codes: tuple[StorageInternalCode, ...]

    def __repr__(self) -> str:
        rendered_codes = tuple(code.value for code in self.safe_codes)
        return (
            "StorageReconcileResult("
            f"claimed_count={self.claimed_count!r}, "
            f"available_count={self.available_count!r}, "
            f"failed_count={self.failed_count!r}, "
            f"deleted_count={self.deleted_count!r}, "
            f"pending_count={self.pending_count!r}, "
            f"delete_pending_count={self.delete_pending_count!r}, "
            f"safe_codes={rendered_codes!r}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class StorageDeleteResult:
    status: ObjectFileStatus
    safe_code: StorageInternalCode | None

    def __repr__(self) -> str:
        safe_code = self.safe_code.value if self.safe_code is not None else None
        return (
            "StorageDeleteResult("
            f"status={self.status.value!r}, safe_code={safe_code!r}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class StorageDeleteBatchResult:
    claimed_count: int
    deleted_count: int
    pending_count: int
    safe_codes: tuple[StorageInternalCode, ...]

    def __repr__(self) -> str:
        rendered_codes = tuple(code.value for code in self.safe_codes)
        return (
            "StorageDeleteBatchResult("
            f"claimed_count={self.claimed_count!r}, "
            f"deleted_count={self.deleted_count!r}, "
            f"pending_count={self.pending_count!r}, "
            f"safe_codes={rendered_codes!r}"
            ")"
        )


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


@dataclass(frozen=True, repr=False)
class _ObjectDeleteTarget:
    object_file_id: UUID
    bucket: BucketName
    object_key: ObjectKey
    safe_code: StorageInternalCode | None

    def __repr__(self) -> str:
        safe_code = self.safe_code.value if self.safe_code is not None else None
        return (
            "_ObjectDeleteTarget("
            f"object_file_id={self.object_file_id!r}, "
            "bucket=<redacted>, object_key=<redacted>, "
            f"safe_code={safe_code!r}"
            ")"
        )


async def ingest_sanitized_image(
    session_factory: sessionmaker[Session],
    *,
    source: AsyncImageSource,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
    settings: Settings,
    storage: ObjectStorageService,
) -> IngestedImageResult:
    if not isinstance(storage, ObjectStorageService):
        raise TypeError("Storage upload provider must implement the storage protocol")

    prepared = await prepare_sanitized_image_upload(
        session_factory,
        source=source,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=now,
        settings=settings,
    )
    put_result: StorageProviderOperationResult | None = None
    put_failure_kind: StorageProviderFailureKind | None = None
    try:
        put_result = storage.put_object(
            bucket=prepared.bucket,
            key=prepared.object_key,
            image=prepared.image,
        )
    except StorageProviderError as exc:
        put_failure_kind = exc.kind
    if (
        put_failure_kind is StorageProviderFailureKind.DEFINITE
        or put_result is not StorageProviderOperationResult.SUCCESS
        and put_failure_kind is None
    ):
        _mark_prepared_upload_failed(
            session_factory,
            prepared=prepared,
            now=now,
            failure_code=StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
        )
        raise StorageUploadError(
            ErrorCode.FILE_STORAGE_ERROR,
            internal_code=StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
        ) from None
    put_was_ambiguous = put_failure_kind is StorageProviderFailureKind.AMBIGUOUS

    stored_head: StoredObjectHead | None = None
    head_failed = False
    try:
        stored_head = storage.head_object(
            bucket=prepared.bucket,
            key=prepared.object_key,
        )
    except StorageProviderError:
        head_failed = True
    if head_failed:
        _mark_prepared_upload_outcome_unknown(
            session_factory,
            prepared=prepared,
            now=now,
        )
        raise StorageUploadError(
            ErrorCode.FILE_STORAGE_ERROR,
            internal_code=StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN,
        ) from None
    if stored_head is None:
        if put_was_ambiguous:
            _mark_prepared_upload_outcome_unknown(
                session_factory,
                prepared=prepared,
                now=now,
            )
            raise StorageUploadError(
                ErrorCode.FILE_STORAGE_ERROR,
                internal_code=StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN,
            ) from None
        _mark_prepared_upload_failed(
            session_factory,
            prepared=prepared,
            now=now,
            failure_code=StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
        )
        raise StorageUploadError(
            ErrorCode.FILE_STORAGE_ERROR,
            internal_code=StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
        ) from None
    if not _stored_head_matches(prepared, stored_head):
        cleanup_code = _delete_mismatched_object_file(
            session_factory,
            object_file_id=prepared.object_file_id,
            bucket=prepared.bucket,
            object_key=prepared.object_key,
            now=now,
            storage=storage,
        )
        raise StorageUploadError(
            ErrorCode.FILE_STORAGE_ERROR,
            internal_code=cleanup_code,
        ) from None

    _mark_prepared_upload_available(
        session_factory,
        prepared=prepared,
        now=now,
    )
    return _ingested_image_result(prepared)


def reconcile_stale_object_uploads(
    session_factory: sessionmaker[Session],
    *,
    storage: ObjectStorageService,
    now: datetime,
    stale_seconds: int,
    batch_size: int,
) -> StorageReconcileResult:
    current_time = _validate_reconcile_inputs(
        storage=storage,
        now=now,
        stale_seconds=stale_seconds,
        batch_size=batch_size,
    )
    claims: list[ClaimedObjectFile] | None = None
    claim_failed = False
    try:
        with session_factory.begin() as session:
            claims = claim_stale_pending_uploads(
                session,
                now=current_time,
                stale_seconds=stale_seconds,
                batch_size=batch_size,
            )
    except SQLAlchemyError:
        claim_failed = True
    if claim_failed or claims is None:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None

    available_count = 0
    failed_count = 0
    deleted_count = 0
    pending_count = 0
    delete_pending_count = 0
    safe_codes: list[StorageInternalCode] = []
    for claim in claims:
        stored_head: StoredObjectHead | None = None
        head_failed = False
        try:
            stored_head = storage.head_object(
                bucket=claim.bucket,
                key=claim.object_key,
            )
        except StorageProviderError:
            head_failed = True
        if head_failed:
            _mark_object_upload_outcome_unknown(
                session_factory,
                object_file_id=claim.object_file_id,
                now=current_time,
            )
            pending_count += 1
            safe_codes.append(StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN)
            continue
        if stored_head is None:
            _mark_object_upload_failed(
                session_factory,
                object_file_id=claim.object_file_id,
                now=current_time,
                failure_code=StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
            )
            failed_count += 1
            safe_codes.append(StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD)
            continue
        if _claimed_head_matches(claim, stored_head):
            _mark_object_upload_available(
                session_factory,
                object_file_id=claim.object_file_id,
                now=current_time,
            )
            available_count += 1
            continue

        cleanup_code = _delete_mismatched_object_file(
            session_factory,
            object_file_id=claim.object_file_id,
            bucket=claim.bucket,
            object_key=claim.object_key,
            now=current_time,
            storage=storage,
        )
        safe_codes.append(cleanup_code)
        if cleanup_code is StorageInternalCode.DELETE_OUTCOME_UNKNOWN:
            delete_pending_count += 1
        else:
            deleted_count += 1

    return StorageReconcileResult(
        claimed_count=len(claims),
        available_count=available_count,
        failed_count=failed_count,
        deleted_count=deleted_count,
        pending_count=pending_count,
        delete_pending_count=delete_pending_count,
        safe_codes=tuple(safe_codes),
    )


def delete_available_object(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    storage: ObjectStorageService,
    now: datetime,
) -> StorageDeleteResult:
    current_time = _validate_delete_inputs(
        object_file_id=object_file_id,
        storage=storage,
        now=now,
    )
    prepared = _prepare_available_object_delete(
        session_factory,
        object_file_id=object_file_id,
        now=current_time,
    )
    if isinstance(prepared, StorageDeleteResult):
        return prepared
    return _delete_object_target(
        session_factory,
        target=prepared,
        storage=storage,
        now=current_time,
    )


def reconcile_stale_object_deletes(
    session_factory: sessionmaker[Session],
    *,
    storage: ObjectStorageService,
    now: datetime,
    stale_seconds: int,
    batch_size: int,
) -> StorageDeleteBatchResult:
    current_time = _validate_reconcile_inputs(
        storage=storage,
        now=now,
        stale_seconds=stale_seconds,
        batch_size=batch_size,
    )
    claims: list[ClaimedObjectFile] | None = None
    claim_failed = False
    try:
        with session_factory.begin() as session:
            claims = claim_stale_delete_pending(
                session,
                now=current_time,
                stale_seconds=stale_seconds,
                batch_size=batch_size,
            )
    except SQLAlchemyError:
        claim_failed = True
    if claim_failed or claims is None:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None

    deleted_count = 0
    pending_count = 0
    safe_codes: list[StorageInternalCode] = []
    for claim in claims:
        claim_safe_code = _safe_delete_code(claim.failure_code)
        stored_head: StoredObjectHead | None = None
        head_failed = False
        try:
            stored_head = storage.head_object(
                bucket=claim.bucket,
                key=claim.object_key,
            )
        except StorageProviderError:
            head_failed = True
        if head_failed:
            _mark_object_delete_outcome_unknown(
                session_factory,
                object_file_id=claim.object_file_id,
                now=current_time,
            )
            pending_count += 1
            safe_codes.append(StorageInternalCode.DELETE_OUTCOME_UNKNOWN)
            continue
        if stored_head is None:
            _mark_object_upload_deleted(
                session_factory,
                object_file_id=claim.object_file_id,
                now=current_time,
            )
            deleted_count += 1
            if claim_safe_code is not None:
                safe_codes.append(claim_safe_code)
            continue

        delete_result = _delete_object_target(
            session_factory,
            target=_ObjectDeleteTarget(
                object_file_id=claim.object_file_id,
                bucket=claim.bucket,
                object_key=claim.object_key,
                safe_code=claim_safe_code,
            ),
            storage=storage,
            now=current_time,
        )
        if delete_result.status is ObjectFileStatus.DELETED:
            deleted_count += 1
        else:
            pending_count += 1
        if delete_result.safe_code is not None:
            safe_codes.append(delete_result.safe_code)

    return StorageDeleteBatchResult(
        claimed_count=len(claims),
        deleted_count=deleted_count,
        pending_count=pending_count,
        safe_codes=tuple(safe_codes),
    )


async def prepare_sanitized_image_upload(
    session_factory: sessionmaker[Session],
    *,
    source: AsyncImageSource,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
    settings: Settings,
) -> PreparedImageUpload:
    """Commit a pending row and return a detached, redacted provider envelope."""
    current_time = _validate_upload_inputs(
        source=source,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=now,
        settings=settings,
    )
    config = _require_storage_config(settings)
    rate_limit_result = _record_upload_attempt(
        session_factory,
        settings=settings,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=current_time,
    )
    if not rate_limit_result.allowed:
        raise StorageUploadError(ErrorCode.RATE_LIMITED)

    bounded_source = await read_bounded_image(
        source,
        max_bytes=config.max_upload_bytes,
    )
    image = sanitize_bounded_image(
        bounded_source,
        limits=ImageDimensionLimits(
            max_dimension=config.max_image_dimension,
            max_pixels=config.max_image_pixels,
        ),
        max_output_bytes=config.max_upload_bytes,
    )
    object_file_id = uuid4()
    object_key = generate_object_key(
        image.metadata.canonical_extension,
        uuid_factory=uuid4,
    )
    prepared = PreparedImageUpload(
        object_file_id=object_file_id,
        bucket=BucketName(config.bucket),
        object_key=object_key,
        image=image,
    )

    transaction_failed = False
    try:
        with session_factory.begin() as session:
            create_pending_object_file(
                session,
                object_file_id=prepared.object_file_id,
                bucket=prepared.bucket,
                object_key=prepared.object_key,
                metadata=prepared.image.metadata,
                created_by_user_id=actor_user_id,
                now=current_time,
            )
    except SQLAlchemyError:
        transaction_failed = True
    if transaction_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
    return prepared


def create_authorized_presigned_get_url(
    session_factory: sessionmaker[Session],
    *,
    request: ObjectReadAuthorizationRequest,
    authorizer: ObjectFileAccessAuthorizer,
    storage: ObjectStorageService,
    settings: Settings,
) -> PresignedObjectUrl:
    if not isinstance(request, ObjectReadAuthorizationRequest):
        raise TypeError("Storage read request must be typed")
    if not isinstance(authorizer, ObjectFileAccessAuthorizer):
        raise TypeError("Storage read authorizer must implement the protocol")
    if not isinstance(storage, ObjectStorageService):
        raise TypeError("Storage read provider must implement the protocol")
    if not isinstance(settings, Settings):
        raise TypeError("Storage read settings must be typed")

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

    config = _require_storage_config(settings)
    return storage.create_presigned_get_url(
        bucket=authorized_object.bucket,
        key=authorized_object.object_key,
        ttl_seconds=config.presigned_ttl_seconds,
    )


def _validate_upload_inputs(
    *,
    source: AsyncImageSource,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
    settings: Settings,
) -> datetime:
    if not callable(getattr(source, "seek", None)) or not callable(
        getattr(source, "read", None)
    ):
        raise TypeError("Storage upload source must be async-readable")
    if not isinstance(actor_user_id, UUID) or actor_user_id.int == 0:
        raise ValueError("Storage upload actor must be a non-zero UUID")
    if not isinstance(client_ip, ResolvedClientIp):
        raise TypeError("Storage upload client IP must be resolved")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Storage upload timestamp must be timezone-aware")
    if not isinstance(settings, Settings):
        raise TypeError("Storage upload settings must be typed")
    return now.astimezone(UTC)


def _require_storage_config(settings: Settings):
    config = None
    configuration_failed = False
    try:
        config = settings.require_object_storage_config()
    except ObjectStorageSettingsError:
        configuration_failed = True
    if configuration_failed or config is None:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
    return config


def _record_upload_attempt(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> StorageUploadRateLimitResult:
    result: StorageUploadRateLimitResult | None = None
    transaction_failed = False
    try:
        with session_factory.begin() as session:
            result = record_storage_upload_attempt(
                session,
                settings,
                actor_user_id,
                client_ip,
                now,
            )
    except SQLAlchemyError:
        transaction_failed = True
    if transaction_failed or result is None:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
    return result


def _validate_reconcile_inputs(
    *,
    storage: ObjectStorageService,
    now: datetime,
    stale_seconds: int,
    batch_size: int,
) -> datetime:
    if not isinstance(storage, ObjectStorageService):
        raise TypeError("Storage reconcile provider must implement the protocol")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Storage reconcile timestamp must be timezone-aware")
    if (
        isinstance(stale_seconds, bool)
        or not isinstance(stale_seconds, int)
        or stale_seconds < 1
    ):
        raise ValueError("Storage reconcile stale seconds must be positive")
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size < 1
        or batch_size > OBJECT_FILE_CLAIM_MAX_BATCH_SIZE
    ):
        raise ValueError("Storage reconcile batch size must be between 1 and 5000")
    return now.astimezone(UTC)


def _validate_delete_inputs(
    *,
    object_file_id: UUID,
    storage: ObjectStorageService,
    now: datetime,
) -> datetime:
    if not isinstance(object_file_id, UUID) or object_file_id.int == 0:
        raise ValueError("Storage delete object id must be a non-zero UUID")
    if not isinstance(storage, ObjectStorageService):
        raise TypeError("Storage delete provider must implement the protocol")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Storage delete timestamp must be timezone-aware")
    return now.astimezone(UTC)


def _stored_head_matches(
    prepared: PreparedImageUpload,
    stored_head: StoredObjectHead,
) -> bool:
    metadata = prepared.image.metadata
    return not (
        stored_head.size_bytes != metadata.size_bytes
        or stored_head.content_type != metadata.content_type
        or stored_head.checksum_sha256 != metadata.checksum_sha256
    )


def _claimed_head_matches(
    claim: ClaimedObjectFile,
    stored_head: StoredObjectHead,
) -> bool:
    return not (
        stored_head.size_bytes != claim.size_bytes
        or stored_head.content_type != claim.content_type
        or stored_head.checksum_sha256 != claim.checksum_sha256
    )


def _ingested_image_result(
    prepared: PreparedImageUpload,
) -> IngestedImageResult:
    metadata = prepared.image.metadata
    return IngestedImageResult(
        object_file_id=prepared.object_file_id,
        content_type=metadata.content_type,
        size_bytes=metadata.size_bytes,
        width_px=metadata.width_px,
        height_px=metadata.height_px,
        checksum_sha256=metadata.checksum_sha256,
    )


def _safe_delete_code(value: str | None) -> StorageInternalCode | None:
    if value is None:
        return None
    try:
        safe_code = StorageInternalCode(value)
    except ValueError:
        raise ObjectFileStateError from None
    if safe_code not in {
        StorageInternalCode.OBJECT_METADATA_MISMATCH,
        StorageInternalCode.DELETE_OUTCOME_UNKNOWN,
    }:
        raise ObjectFileStateError
    return safe_code


def _prepare_available_object_delete(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> _ObjectDeleteTarget | StorageDeleteResult:
    target: _ObjectDeleteTarget | None = None
    existing_result: StorageDeleteResult | None = None
    prepare_failed = False
    try:
        with session_factory.begin() as session:
            object_file = load_object_file_for_update(
                session,
                object_file_id=object_file_id,
            )
            if object_file is None:
                raise ObjectFileStateError
            status = ObjectFileStatus(object_file.status)
            if status in {
                ObjectFileStatus.DELETE_PENDING,
                ObjectFileStatus.DELETED,
            }:
                existing_result = StorageDeleteResult(
                    status=status,
                    safe_code=_safe_delete_code(object_file.failure_code),
                )
            elif status is ObjectFileStatus.AVAILABLE:
                transitioned = mark_object_file_delete_pending(
                    session,
                    object_file_id=object_file_id,
                    failure_code=None,
                    now=now,
                )
                if transitioned is None:
                    raise ObjectFileStateError
                target = _ObjectDeleteTarget(
                    object_file_id=object_file_id,
                    bucket=BucketName(transitioned.bucket),
                    object_key=ObjectKey(transitioned.object_key),
                    safe_code=None,
                )
            else:
                raise ObjectFileStateError
    except (ObjectFileStateError, SQLAlchemyError, ValueError):
        prepare_failed = True
    if prepare_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
    if existing_result is not None:
        return existing_result
    if target is None:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
    return target


def _delete_object_target(
    session_factory: sessionmaker[Session],
    *,
    target: _ObjectDeleteTarget,
    storage: ObjectStorageService,
    now: datetime,
) -> StorageDeleteResult:
    provider_result: StorageProviderOperationResult | None = None
    delete_failed = False
    try:
        provider_result = storage.delete_object(
            bucket=target.bucket,
            key=target.object_key,
        )
    except StorageProviderError:
        delete_failed = True
    if not delete_failed and provider_result in {
        StorageProviderOperationResult.SUCCESS,
        StorageProviderOperationResult.MISSING,
    }:
        _mark_object_upload_deleted(
            session_factory,
            object_file_id=target.object_file_id,
            now=now,
        )
        return StorageDeleteResult(
            status=ObjectFileStatus.DELETED,
            safe_code=target.safe_code,
        )

    stored_head: StoredObjectHead | None = None
    head_failed = False
    try:
        stored_head = storage.head_object(
            bucket=target.bucket,
            key=target.object_key,
        )
    except StorageProviderError:
        head_failed = True
    if not head_failed and stored_head is None:
        _mark_object_upload_deleted(
            session_factory,
            object_file_id=target.object_file_id,
            now=now,
        )
        return StorageDeleteResult(
            status=ObjectFileStatus.DELETED,
            safe_code=target.safe_code,
        )

    _mark_object_delete_outcome_unknown(
        session_factory,
        object_file_id=target.object_file_id,
        now=now,
    )
    return StorageDeleteResult(
        status=ObjectFileStatus.DELETE_PENDING,
        safe_code=StorageInternalCode.DELETE_OUTCOME_UNKNOWN,
    )


def _delete_mismatched_object_file(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    bucket: BucketName,
    object_key: ObjectKey,
    now: datetime,
    storage: ObjectStorageService,
) -> StorageInternalCode:
    _mark_object_upload_delete_pending(
        session_factory,
        object_file_id=object_file_id,
        now=now,
    )

    delete_result: StorageProviderOperationResult | None = None
    delete_failed = False
    try:
        delete_result = storage.delete_object(
            bucket=bucket,
            key=object_key,
        )
    except StorageProviderError:
        delete_failed = True
    if delete_failed or delete_result is not StorageProviderOperationResult.SUCCESS:
        _mark_object_delete_outcome_unknown(
            session_factory,
            object_file_id=object_file_id,
            now=now,
        )
        return StorageInternalCode.DELETE_OUTCOME_UNKNOWN

    _mark_object_upload_deleted(
        session_factory,
        object_file_id=object_file_id,
        now=now,
    )
    return StorageInternalCode.OBJECT_METADATA_MISMATCH


def _mark_prepared_upload_available(
    session_factory: sessionmaker[Session],
    *,
    prepared: PreparedImageUpload,
    now: datetime,
) -> None:
    _mark_object_upload_available(
        session_factory,
        object_file_id=prepared.object_file_id,
        now=now,
    )


def _mark_object_upload_available(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> None:
    transition_failed = False
    try:
        with session_factory.begin() as session:
            transitioned = mark_object_file_available(
                session,
                object_file_id=object_file_id,
                now=now,
            )
            if transitioned is None:
                raise ObjectFileStateError
    except (ObjectFileStateError, SQLAlchemyError):
        transition_failed = True
    if transition_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None


def _mark_prepared_upload_outcome_unknown(
    session_factory: sessionmaker[Session],
    *,
    prepared: PreparedImageUpload,
    now: datetime,
) -> None:
    _mark_object_upload_outcome_unknown(
        session_factory,
        object_file_id=prepared.object_file_id,
        now=now,
    )


def _mark_object_upload_outcome_unknown(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> None:
    transition_failed = False
    try:
        with session_factory.begin() as session:
            transitioned = mark_pending_upload_outcome_unknown(
                session,
                object_file_id=object_file_id,
                now=now,
            )
            if transitioned is None:
                raise ObjectFileStateError
    except (ObjectFileStateError, SQLAlchemyError):
        transition_failed = True
    if transition_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None


def _mark_object_upload_delete_pending(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> None:
    transition_failed = False
    try:
        with session_factory.begin() as session:
            transitioned = mark_object_file_delete_pending(
                session,
                object_file_id=object_file_id,
                failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
                now=now,
            )
            if transitioned is None:
                raise ObjectFileStateError
    except (ObjectFileStateError, SQLAlchemyError):
        transition_failed = True
    if transition_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None


def _mark_object_upload_deleted(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> None:
    transition_failed = False
    try:
        with session_factory.begin() as session:
            transitioned = mark_object_file_deleted(
                session,
                object_file_id=object_file_id,
                now=now,
            )
            if transitioned is None:
                raise ObjectFileStateError
    except (ObjectFileStateError, SQLAlchemyError):
        transition_failed = True
    if transition_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None


def _mark_object_delete_outcome_unknown(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> None:
    transition_failed = False
    try:
        with session_factory.begin() as session:
            transitioned = mark_delete_outcome_unknown(
                session,
                object_file_id=object_file_id,
                now=now,
            )
            if transitioned is None:
                raise ObjectFileStateError
    except (ObjectFileStateError, SQLAlchemyError):
        transition_failed = True
    if transition_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None


def _mark_prepared_upload_failed(
    session_factory: sessionmaker[Session],
    *,
    prepared: PreparedImageUpload,
    now: datetime,
    failure_code: StorageInternalCode,
) -> None:
    _mark_object_upload_failed(
        session_factory,
        object_file_id=prepared.object_file_id,
        now=now,
        failure_code=failure_code,
    )


def _mark_object_upload_failed(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
    failure_code: StorageInternalCode,
) -> None:
    transition_failed = False
    try:
        with session_factory.begin() as session:
            transitioned = mark_object_file_failed(
                session,
                object_file_id=object_file_id,
                failure_code=failure_code,
                now=now,
            )
            if transitioned is None:
                raise ObjectFileStateError
    except (ObjectFileStateError, SQLAlchemyError):
        transition_failed = True
    if transition_failed:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
