from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage
from app.idempotency.contracts import (
    IdempotencyEndpoint,
    IdempotencyOutcome,
    IdempotencyResultType,
)
from app.rating.contracts import RiskBandDisclosureProjection
from app.rating.disclosure import (
    DISCLOSURE_AUDIT_EVENT_TYPE,
    DISCLOSURE_AUDIT_OBJECT_TYPE,
    DisclosureMutationResult,
    RiskBandDisclosureAuditPayload,
    RiskBandDisclosureCommand,
    RiskBandDisclosureRawForm,
    assemble_risk_band_disclosure_command,
    create_risk_band_disclosure_request_hash_v1,
)
from app.rating.enums import RiskBand, RiskBandDisclosurePurpose
from app.rating.presentation import (
    RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS,
    RISK_BAND_WEB_COPY,
    DisclosurePostActionContext,
    disclosure_snapshot_path,
    get_risk_band_web_copy,
)
from app.rating.values import DisclosureViewId
from app.shop.values import ShopId, UserId
from app.shop_customer.values import ShopCustomerId

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _command_parts():
    return {
        "actor_user_id": UserId(UUID(int=1)),
        "current_shop_id": ShopId(UUID(int=2)),
        "shop_customer_id": ShopCustomerId(UUID(int=3)),
        "purpose": RiskBandDisclosurePurpose.DEBT_PROPOSAL_REVIEW,
    }


def test_v1_hash_is_deterministic_and_binds_exact_authority_and_purpose() -> None:
    parts = _command_parts()
    baseline = create_risk_band_disclosure_request_hash_v1(**parts)

    assert baseline == create_risk_band_disclosure_request_hash_v1(**parts)
    assert baseline != create_risk_band_disclosure_request_hash_v1(
        **(parts | {"actor_user_id": UserId(UUID(int=4))})
    )
    assert baseline != create_risk_band_disclosure_request_hash_v1(
        **(parts | {"current_shop_id": ShopId(UUID(int=4))})
    )
    assert baseline != create_risk_band_disclosure_request_hash_v1(
        **(parts | {"shop_customer_id": ShopCustomerId(UUID(int=4))})
    )
    assert baseline != create_risk_band_disclosure_request_hash_v1(
        **(
            parts
            | {
                "purpose": RiskBandDisclosurePurpose.CREDIT_LIMIT_REVIEW,
            }
        )
    )
    assert baseline.value not in repr(baseline)


def test_raw_form_assembles_one_redacted_server_authority_command() -> None:
    parts = _command_parts()
    raw_key = "00000000-0000-0000-0000-000000000004"
    raw = RiskBandDisclosureRawForm(
        purpose=parts["purpose"].value,
        idempotency_key=raw_key,
    )
    assembly = assemble_risk_band_disclosure_command(
        raw=raw,
        actor_user_id=parts["actor_user_id"],
        current_shop_id=parts["current_shop_id"],
        shop_customer_id=parts["shop_customer_id"],
    )

    assert assembly.error is None
    assert isinstance(assembly.command, RiskBandDisclosureCommand)
    assert assembly.command.purpose is parts["purpose"]
    assert assembly.command.request_hash == (
        create_risk_band_disclosure_request_hash_v1(**parts)
    )
    assert {field.name for field in fields(raw)} == {
        "purpose",
        "idempotency_key",
    }
    assert {field.name for field in fields(assembly.command)} == {
        "actor_user_id",
        "current_shop_id",
        "shop_customer_id",
        "purpose",
        "idempotency_key",
        "request_hash",
    }
    for representation in (repr(raw), repr(assembly), repr(assembly.command)):
        assert raw_key not in representation
        assert str(parts["actor_user_id"]) not in representation
        assert str(parts["current_shop_id"]) not in representation


@pytest.mark.parametrize(
    ("purpose", "key"),
    (
        (None, "00000000-0000-0000-0000-000000000004"),
        ("", "00000000-0000-0000-0000-000000000004"),
        ("override", "00000000-0000-0000-0000-000000000004"),
        ("debt_proposal_review", None),
        ("debt_proposal_review", ""),
        ("debt_proposal_review", "NOT-A-UUID"),
    ),
)
def test_missing_or_invalid_purpose_and_key_never_assemble_a_mutation(
    purpose: str | None,
    key: str | None,
) -> None:
    parts = _command_parts()
    result = assemble_risk_band_disclosure_command(
        raw=RiskBandDisclosureRawForm(
            purpose=purpose,
            idempotency_key=key,
        ),
        actor_user_id=parts["actor_user_id"],
        current_shop_id=parts["current_shop_id"],
        shop_customer_id=parts["shop_customer_id"],
    )

    assert result.error is ErrorCode.VALIDATION_ERROR
    assert result.command is None


def test_mutation_result_and_action_context_keep_locators_path_only() -> None:
    view_id = DisclosureViewId(UUID(int=9))
    result = DisclosureMutationResult(
        outcome=IdempotencyOutcome.NEW,
        disclosure_view_id=view_id,
    )
    replay = replace(result, outcome=IdempotencyOutcome.REPLAY)
    action = DisclosurePostActionContext(shop_customer_id=ShopCustomerId(UUID(int=10)))

    assert disclosure_snapshot_path(view_id) == (
        "/shop/risk-band-disclosures/00000000-0000-0000-0000-000000000009"
    )
    assert action.same_origin_post_path() == (
        "/shop/customers/00000000-0000-0000-0000-00000000000a/risk-band-disclosures"
    )
    assert replay.outcome is IdempotencyOutcome.REPLAY
    assert view_id.as_path_segment() not in repr(result)
    assert "00000000-0000-0000-0000-00000000000a" not in repr(action)
    with pytest.raises(ValueError, match="outcome"):
        DisclosureMutationResult(
            outcome=IdempotencyOutcome.CONFLICT,
            disclosure_view_id=view_id,
        )


def test_safe_get_projection_and_audit_payload_are_exact_closed_strings() -> None:
    projection = RiskBandDisclosureProjection(
        band=RiskBand.BLOCKED,
        purpose=RiskBandDisclosurePurpose.EXISTING_DEBT_REVIEW,
        viewed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    payload = RiskBandDisclosureAuditPayload(
        purpose=projection.purpose,
        band=projection.band,
    )
    metadata = payload.as_candidate_metadata()

    assert tuple(field.name for field in fields(projection)) == (
        "band",
        "purpose",
        "viewed_at",
    )
    assert metadata == {
        "purpose": "existing_debt_review",
        "band": "blocked",
    }
    assert all(isinstance(value, str) for value in metadata.values())
    with pytest.raises(TypeError):
        metadata["score"] = "60"  # type: ignore[index]
    assert DISCLOSURE_AUDIT_EVENT_TYPE == "disclosure.risk_band_viewed"
    assert DISCLOSURE_AUDIT_OBJECT_TYPE == "disclosure_view"
    assert "blocked" not in repr(payload)


def test_idempotency_contract_wires_exact_disclosure_pair_for_persistence() -> None:
    assert (
        IdempotencyEndpoint.SHOP_RISK_BAND_DISCLOSURES_CREATE.value
        == "shop.risk_band_disclosures.create"
    )
    assert IdempotencyResultType.DISCLOSURE_VIEW.value == "disclosure_view"

    model_source = (PROJECT_ROOT / "app/idempotency/models.py").read_text(
        encoding="utf-8"
    )
    repository_source = (PROJECT_ROOT / "app/idempotency/repository.py").read_text(
        encoding="utf-8"
    )
    assert model_source.count("SHOP_RISK_BAND_DISCLOSURES_CREATE") == 1
    assert repository_source.count("SHOP_RISK_BAND_DISCLOSURES_CREATE") == 1
    assert model_source.count("DISCLOSURE_VIEW") == 1
    assert repository_source.count("DISCLOSURE_VIEW") == 1


def test_exact_two_no_store_same_origin_ssr_route_contracts() -> None:
    assert tuple(
        (route.name, route.method, route.path, route.form_fields)
        for route in RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS
    ) == (
        (
            "shop_risk_band_disclosure_create",
            "POST",
            "/shop/customers/{shop_customer_id}/risk-band-disclosures",
            ("purpose", "idempotency_key", "csrf_token"),
        ),
        (
            "shop_risk_band_disclosure_view",
            "GET",
            "/shop/risk-band-disclosures/{disclosure_view_id}",
            (),
        ),
    )
    assert all(
        route.cache_control == "no-store" and route.same_origin_only
        for route in RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS
    )
    paths = "\n".join(route.path for route in RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS)
    for forbidden in ("/customer/", "/admin", "/api", "/fragment"):
        assert forbidden not in paths


def test_uz_ru_labels_cover_only_closed_band_and_purpose_vocabulary() -> None:
    expected_keys = {
        *(f"band_{band.value}" for band in RiskBand),
        *(f"purpose_{purpose.value}" for purpose in RiskBandDisclosurePurpose),
        "viewed_at",
        "historical_notice",
        "new_view",
        "page_title",
        "purpose_label",
        "band_label",
        "generic_error",
    }

    assert set(RISK_BAND_WEB_COPY) == set(DebtWebLanguage)
    for language in DebtWebLanguage:
        copy = get_risk_band_web_copy(language)
        assert set(copy) == expected_keys
        assert all(value.strip() for value in copy.values())
        with pytest.raises(TypeError):
            copy["band_new"] = "tampered"  # type: ignore[index]

    presentation_source = (PROJECT_ROOT / "app/rating/presentation.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("score", "delta", "75", "50", "49"):
        assert forbidden not in presentation_source


def test_route_checkpoint_keeps_m16_router_and_exact_linear_m17_child() -> None:
    assert (PROJECT_ROOT / "app/rating/models.py").is_file()
    assert (PROJECT_ROOT / "app/rating/router.py").is_file()
    matching = {
        path.name
        for path in (PROJECT_ROOT / "alembic/versions").glob("*.py")
        if "c7d8e9f0a1b2" in path.read_text(encoding="utf-8")
    }
    assert matching == {
        "c7d8e9f0a1b2_add_rating_and_disclosure_persistence.py",
        "d8e9f0a1b2c3_add_written_off_debt_persistence.py",
    }
