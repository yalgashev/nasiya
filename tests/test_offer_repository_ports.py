import inspect

from app.audit.contracts import AuditWriter
from app.offers.contracts import (
    CurrentOfferResolver,
    OfferAcceptanceRepository,
    OfferVersionRepository,
)


def _public_methods(protocol: type) -> set[str]:
    return {
        name
        for name, value in protocol.__dict__.items()
        if callable(value) and not name.startswith("_")
    }


def test_offer_version_repository_port_is_minimal_and_transaction_free() -> None:
    assert _public_methods(OfferVersionRepository) == {
        "create_draft",
        "list_versions",
        "get_version",
        "lock_version",
        "lock_versions_for_purpose",
        "list_texts",
        "get_text",
        "save_draft_text",
        "save_lifecycle_state",
    }


def test_current_and_acceptance_ports_expose_only_required_operations() -> None:
    assert _public_methods(CurrentOfferResolver) == {
        "resolve_current",
        "resolve_current_for_acceptance",
    }
    assert _public_methods(OfferAcceptanceRepository) == {
        "get_acceptance",
        "create_acceptance",
    }


def test_audit_writer_is_append_only() -> None:
    assert _public_methods(AuditWriter) == {"append"}


def test_inner_ports_have_no_sqlalchemy_or_transaction_owner_surface() -> None:
    source = "\n".join(
        (
            inspect.getsource(OfferVersionRepository),
            inspect.getsource(CurrentOfferResolver),
            inspect.getsource(OfferAcceptanceRepository),
            inspect.getsource(AuditWriter),
        )
    ).casefold()

    for forbidden in (
        "sqlalchemy",
        "session",
        "commit",
        "rollback",
        "close",
        "audit.query",
        "audit.update",
        "audit.delete",
    ):
        assert forbidden not in source
