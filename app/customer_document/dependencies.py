from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request

from app.settings import ObjectStorageSettingsError, Settings
from app.storage.contracts import ObjectStorageService, StorageProviderError
from app.storage.s3 import S3ObjectStorageService, create_s3_client


class CustomerDocumentStorageUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Customer document storage is unavailable")

    def __repr__(self) -> str:
        return "CustomerDocumentStorageUnavailable()"


@contextmanager
def get_customer_document_storage_service(
    request: Request,
) -> Iterator[ObjectStorageService]:
    storage = getattr(request.app.state, "customer_document_storage_service", None)
    if storage is not None:
        if not isinstance(storage, ObjectStorageService):
            raise CustomerDocumentStorageUnavailable
        yield storage
        return

    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise CustomerDocumentStorageUnavailable
    try:
        config = settings.require_object_storage_config()
        client = create_s3_client(config)
        composed = S3ObjectStorageService(client)
    except (ObjectStorageSettingsError, StorageProviderError, TypeError, ValueError):
        raise CustomerDocumentStorageUnavailable from None

    try:
        yield composed
    finally:
        client.close()
