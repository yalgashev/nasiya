from fastapi import Request

from app.storage.contracts import ObjectStorageService


class CustomerDocumentStorageUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Customer document storage is unavailable")

    def __repr__(self) -> str:
        return "CustomerDocumentStorageUnavailable()"


def get_customer_document_storage_service(
    request: Request,
) -> ObjectStorageService:
    storage = getattr(request.app.state, "customer_document_storage_service", None)
    if not isinstance(storage, ObjectStorageService):
        raise CustomerDocumentStorageUnavailable
    return storage
