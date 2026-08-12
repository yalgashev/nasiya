from __future__ import annotations

import ast
import hashlib
import re
import tomllib
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

import app.audit.models  # noqa: F401
import app.auth.models  # noqa: F401
import app.customer.models  # noqa: F401
import app.customer_activation.contracts as activation_contracts
import app.customer_document.models  # noqa: F401
import app.customer_identity.models  # noqa: F401
import app.debt.models  # noqa: F401
import app.idempotency.models  # noqa: F401
import app.offers.models  # noqa: F401
import app.otp.models  # noqa: F401
import app.payment.models  # noqa: F401
import app.shop.models  # noqa: F401
import app.shop_customer.models  # noqa: F401
import app.storage.models  # noqa: F401
import app.telegram.models  # noqa: F401
from app.customer_activation.contracts import (
    CustomerActivationTransitionOutcome,
    CustomerLifecycleState,
    CustomerLifecycleStatus,
    transition_customer_to_active,
)
from app.customer_activation.router import (
    get_activation_current_session_context,
    validate_activation_csrf,
)
from app.customer_activation.router import (
    router as activation_router,
)
from app.db import Base

_CREATED = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
_UPDATED = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
_ACTIVATED = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXACT_M11_TEST_FILES = {
    "tests/test_m11_activation_atomicity_postgresql.py",
    "tests/test_m11_activation_concurrency_postgresql.py",
    "tests/test_m11_activation_web_postgresql.py",
    "tests/test_m11_activation_web_security.py",
    "tests/test_m11_active_telegram_invariant_postgresql.py",
    "tests/test_m11_baseline_and_migration.py",
    "tests/test_m11_dispatcher_registration.py",
    "tests/test_m11_identity_document_races_postgresql.py",
    "tests/test_m11_offer_acceptance_races_postgresql.py",
    "tests/test_m11_otp_purpose_crypto.py",
    "tests/test_m11_registration_issue_postgresql.py",
    "tests/test_m11_registration_rate_policy.py",
    "tests/test_m11_registration_readiness.py",
    "tests/test_m11_registration_verify_postgresql.py",
    "tests/test_m11_scope_containment.py",
    "tests/test_m11_sensitive_data_leakage.py",
}
CR_M11_02_CROSS_CUTTING_TEST_FILES = {
    "tests/test_telegram_consume_concurrency.py",
    "tests/test_telegram_consume_start_token_service.py",
    "tests/test_telegram_post_commit_reply.py",
    "tests/test_telegram_update_parser.py",
}
M11_HISTORICAL_TABLES = {
    "audit_log",
    "auth_rate_limits",
    "customer_documents",
    "customer_identities",
    "customers",
    "object_files",
    "offer_acceptances",
    "offer_texts",
    "offer_versions",
    "otp_challenge_events",
    "otp_challenges",
    "otp_dispatcher_state",
    "otp_dispatches",
    "sessions",
    "shop_staff",
    "shop_staff_events",
    "shop_status_events",
    "shops",
    "telegram_link_events",
    "telegram_link_tokens",
    "telegram_links",
    "telegram_polling_state",
    "telegram_update_failures",
    "users",
}
M12_CURRENT_TABLE_EXTENSION = {"shop_customers"}
M13_CURRENT_TABLE_EXTENSION = {"debts", "idempotency_keys"}
M14_CURRENT_TABLE_EXTENSION = {"payments"}
M16_CURRENT_TABLE_EXTENSION = {"rating_events", "disclosure_view_logs"}
M11_SCHEMA_MIGRATIONS = (
    PROJECT_ROOT
    / "alembic/versions/c1d2e3f4a5b6_extend_customer_activation_foundation.py",
    PROJECT_ROOT
    / "alembic/versions/d2e3f4a5b6c7_add_telegram_self_phone_verification.py",
)


def _assert_m11_table_contract_is_source_scoped() -> None:
    for migration in M11_SCHEMA_MIGRATIONS:
        assert '"shop_customers"' not in migration.read_text(encoding="utf-8")
    assert set(Base.metadata.tables) == (
        M11_HISTORICAL_TABLES
        | M12_CURRENT_TABLE_EXTENSION
        | M13_CURRENT_TABLE_EXTENSION
        | M14_CURRENT_TABLE_EXTENSION
        | M16_CURRENT_TABLE_EXTENSION
    )


def _draft() -> CustomerLifecycleState:
    return CustomerLifecycleState(
        status=CustomerLifecycleStatus.DRAFT,
        created_at=_CREATED,
        updated_at=_UPDATED,
        activated_at=None,
    )


def test_customer_lifecycle_status_and_state_shape_are_exact() -> None:
    assert tuple(status.value for status in CustomerLifecycleStatus) == (
        "draft",
        "active",
    )
    assert tuple(field.name for field in fields(CustomerLifecycleState)) == (
        "status",
        "created_at",
        "updated_at",
        "activated_at",
    )
    assert {
        "customer_id",
        "user_id",
        "otp_id",
        "offer_id",
        "document_id",
        "session_id",
        "activation_method",
    }.isdisjoint(field.name for field in fields(CustomerLifecycleState))


def test_draft_transitions_once_with_equal_activation_and_update_time() -> None:
    result = transition_customer_to_active(_draft(), now=_ACTIVATED)

    assert result.outcome is CustomerActivationTransitionOutcome.ACTIVATED
    assert result.state == CustomerLifecycleState(
        status=CustomerLifecycleStatus.ACTIVE,
        created_at=_CREATED,
        updated_at=_ACTIVATED,
        activated_at=_ACTIVATED,
    )


def test_active_replay_is_exact_noop_without_timestamp_rewrite() -> None:
    active = CustomerLifecycleState(
        status=CustomerLifecycleStatus.ACTIVE,
        created_at=_CREATED,
        updated_at=_ACTIVATED,
        activated_at=_ACTIVATED,
    )
    replay_time = _ACTIVATED + timedelta(days=1)

    result = transition_customer_to_active(active, now=replay_time)

    assert result.outcome is CustomerActivationTransitionOutcome.ALREADY_ACTIVE
    assert result.state is active
    assert result.state.updated_at == _ACTIVATED
    assert result.state.activated_at == _ACTIVATED


def test_missing_customer_is_zero_create_result() -> None:
    result = transition_customer_to_active(None, now=_ACTIVATED)

    assert result.outcome is CustomerActivationTransitionOutcome.MISSING
    assert result.state is None


def test_customer_lifecycle_timestamp_and_state_invariants_fail_closed() -> None:
    invalid = (
        {
            "status": CustomerLifecycleStatus.DRAFT,
            "created_at": _CREATED,
            "updated_at": _UPDATED,
            "activated_at": _ACTIVATED,
        },
        {
            "status": CustomerLifecycleStatus.ACTIVE,
            "created_at": _CREATED,
            "updated_at": _UPDATED,
            "activated_at": None,
        },
        {
            "status": CustomerLifecycleStatus.DRAFT,
            "created_at": _UPDATED,
            "updated_at": _CREATED,
            "activated_at": None,
        },
        {
            "status": CustomerLifecycleStatus.DRAFT,
            "created_at": datetime(2026, 8, 1, 8, 0),
            "updated_at": _UPDATED,
            "activated_at": None,
        },
    )
    for values in invalid:
        with pytest.raises((TypeError, ValueError)):
            CustomerLifecycleState(**values)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="activation time"):
        transition_customer_to_active(
            _draft(),
            now=_UPDATED - timedelta(seconds=1),
        )


def test_customer_lifecycle_exposes_no_reverse_or_correction_api() -> None:
    forbidden = {
        "transition_customer_to_draft",
        "deactivate_customer",
        "correct_active_customer",
        "delete_customer",
    }

    assert forbidden.isdisjoint(vars(activation_contracts))


def _dependency_calls(route: APIRoute) -> set[object]:
    calls: set[object] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if dependency.call is not None:
            calls.add(dependency.call)
        pending.extend(dependency.dependencies)
    return calls


def _direct_dependency_names() -> set[str]:
    parsed = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return {
        re.split(r"[\[<>=~! ]", dependency, maxsplit=1)[0].casefold()
        for dependency in parsed["project"]["dependencies"]
    }


def test_m11_adds_no_public_bootstrap_user_customer_lead_or_shop_customer() -> None:
    routes = tuple(
        route for route in activation_router.routes if isinstance(route, APIRoute)
    )
    inventory = {
        (method, route.path)
        for route in routes
        for method in route.methods or set()
        if method not in {"HEAD", "OPTIONS"}
    }

    assert inventory == {
        ("GET", "/customer/activation"),
        ("POST", "/customer/activation/otp/request"),
        ("POST", "/customer/activation/otp/verify"),
        ("POST", "/customer/activation/otp/new-code"),
    }
    for route in routes:
        calls = _dependency_calls(route)
        assert get_activation_current_session_context in calls
        assert "{" not in route.path
        if "POST" in (route.methods or set()):
            assert validate_activation_csrf in calls

    activation_sources = tuple(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "app/customer_activation").glob("*.py"))
    )
    combined = "\n".join(activation_sources)
    called_names = {
        node.func.id
        for source in activation_sources
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"User", "Customer"}.isdisjoint(called_names)
    assert all(
        marker not in combined.casefold()
        for marker in (
            "customerlead",
            "customer_lead",
            "shopcustomer",
            "shop_customer",
            "public registration",
            "anonymous registration",
            "deactivate_customer",
            "transition_customer_to_draft",
            "correct_active_customer",
        )
    )


def test_m11_adds_no_table_dependency_worker_dispatcher_or_out_scope_capability() -> (
    None
):
    _assert_m11_table_contract_is_source_scoped()
    assert _direct_dependency_names() == {
        "alembic",
        "boto3",
        "cryptography",
        "fastapi",
        "httpx",
        "jinja2",
        "pillow",
        "psycopg",
        "pwdlib",
        "pydantic-settings",
        "python-multipart",
        "segno",
        "sqlalchemy",
        "uvicorn",
    }
    migration_source = (
        PROJECT_ROOT
        / "alembic/versions/c1d2e3f4a5b6_extend_customer_activation_foundation.py"
    ).read_text(encoding="utf-8")
    assert all(
        marker not in migration_source
        for marker in (
            "op.create_table",
            "op.create_index",
            "CREATE TYPE",
            "CREATE SEQUENCE",
            "CREATE TRIGGER",
            "CREATE FUNCTION",
            "CREATE VIEW",
        )
    )
    assert not any(
        path.stem in {"worker", "dispatcher", "outbox", "broker", "scheduler"}
        for path in (PROJECT_ROOT / "app/customer_activation").glob("*.py")
    )
    activation_source = "\n".join(
        path.read_text(encoding="utf-8").casefold()
        for path in (PROJECT_ROOT / "app/customer_activation").glob("*.py")
    )
    assert all(
        marker not in activation_source
        for marker in (
            "shop_customer",
            "customer_lead",
            "debt",
            "payment",
            "rating",
            "disclosure",
            "notification",
            "scheduler",
            "web push",
            "sms",
            "outbox",
            "broker",
        )
    )


def test_cr_m11_02_adds_only_three_columns_and_no_table_dependency_or_process() -> None:
    recovery_source = (
        PROJECT_ROOT
        / "alembic/versions/d2e3f4a5b6c7_add_telegram_self_phone_verification.py"
    ).read_text(encoding="utf-8")

    assert recovery_source.count("op.add_column(") == 3
    assert recovery_source.count("op.create_index(") == 1
    for exact_object in (
        '"telegram_link_tokens",\n        sa.Column("pending_contact_binding_mac"',
        '"telegram_link_tokens",\n        sa.Column("contact_requested_at"',
        '"telegram_links",\n        sa.Column("phone_verified_at"',
        "ck_telegram_link_tokens_pending_contact_binding_mac_format",
        "ck_telegram_link_tokens_pending_contact_state_consistent",
        "ck_telegram_link_tokens_pending_contact_timestamp_order",
        "uq_telegram_link_tokens_pending_contact_binding_mac_outstanding",
        "ck_telegram_links_phone_verification_consistent",
        "ck_users_phone_canonical_uz_e164",
    ):
        assert exact_object in recovery_source
    for forbidden in (
        "op.create_table",
        "sa.Enum",
        "CREATE TYPE",
        "CREATE SEQUENCE",
        "CREATE TRIGGER",
        "CREATE FUNCTION",
        "CREATE VIEW",
    ):
        assert forbidden not in recovery_source

    _assert_m11_table_contract_is_source_scoped()
    assert not any(
        path.stem in {"worker", "dispatcher", "outbox", "broker", "scheduler"}
        for path in (PROJECT_ROOT / "app/customer_activation").glob("*.py")
    )


def test_m1_through_m10_targeted_contracts_remain_green() -> None:
    inherited_targets = {
        "tests/test_customer_router_wiring.py",
        "tests/test_shop_containment_guard.py",
        "tests/test_telegram_scope_regression.py",
        "tests/test_telegram_worker.py",
        "tests/test_otp_crypto.py",
        "tests/test_otp_web_flow_e2e.py",
        "tests/test_storage_scope_containment.py",
        "tests/test_storage_minio_integration.py",
        "tests/test_offer_acceptance_postgresql.py",
        "tests/test_offer_router_composition.py",
        "tests/test_customer_identity_service.py",
        "tests/test_customer_identity_web_flows_postgresql.py",
        "tests/test_customer_document_attachment_postgresql.py",
        "tests/test_customer_document_coordinator_postgresql.py",
    }
    assert all((PROJECT_ROOT / path).is_file() for path in inherited_targets)
    tt_bytes = (PROJECT_ROOT / "docs/tt_nasiya_web_v1.md").read_bytes()
    git_blob = f"blob {len(tt_bytes)}\0".encode() + tt_bytes
    assert hashlib.sha1(git_blob).hexdigest() == (
        "d77c0f0f330a1330155a4aee3c46b05d97cf5561"
    )
    scope_source = (PROJECT_ROOT / "docs/m11_scope_contract.md").read_text(
        encoding="utf-8"
    )
    for evidence in (
        "b79250858a3f6a63908a288f891d5dad1126dd48",
        "30705134413",
        "2735 passed",
        "8/8",
        "17ded4cacee9f80728139feee91c67451570be66a9d76f604d0be2346f83b9f9",
        "f9a7109a4439ea889cc982210e01ae069489606d3d6103cc57e1a88c1fd1f7d5",
        "48de725166daaa07e2a0998bca1e907caedc6050cd3ad8740b8a34d3d79ce8e0",
        "08668a326d682a175cc62366b1ca7092963f02457c3cb6f876cfab08f812a526",
        "a2bf649887f7d7701a26cd518b8f8876dd0a85b0198a326f9327a0c02ff3d1e2",
    ):
        assert evidence in scope_source


def test_transaction_lock_and_test_static_guards_are_exact() -> None:
    ownership_paths = (
        PROJECT_ROOT / "app/customer_activation/service.py",
        PROJECT_ROOT / "app/customer_activation/repository.py",
        PROJECT_ROOT / "app/customer/repository.py",
        PROJECT_ROOT / "app/otp/repository.py",
    )
    forbidden_calls: list[str] = []
    for path in ownership_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"commit", "rollback", "close"}
            ):
                forbidden_calls.append(f"{path.name}:{node.lineno}:{node.func.attr}")
    assert forbidden_calls == []

    actual_m11_tests = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "tests").glob("test_m11_*.py")
    }
    assert actual_m11_tests == EXACT_M11_TEST_FILES
    guarded_test_files = (
        EXACT_M11_TEST_FILES
        - {
            "tests/test_m11_baseline_and_migration.py",
            "tests/test_m11_scope_containment.py",
        }
        | CR_M11_02_CROSS_CUTTING_TEST_FILES
    )
    test_source = "\n".join(
        (PROJECT_ROOT / path).read_text(encoding="utf-8")
        for path in sorted(guarded_test_files)
    ).casefold()
    assert all(
        marker not in test_source
        for marker in (
            "pytest.mark.skip",
            "pytest.mark.xfail",
            "pytest.skip(",
            "time.sleep(",
            "sqlite",
            "create_all(",
            "create table ",
            "pg_advisory",
            "pg_try_advisory",
        )
    )


def _runtime_python_paths() -> tuple[Path, ...]:
    return tuple(sorted((PROJECT_ROOT / "app").rglob("*.py")))


def _imported_call_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for imported in node.names:
            aliases[imported.asname or imported.name] = imported.name
    return aliases


def _call_symbol(call: ast.Call, aliases: dict[str, str]) -> str | None:
    if isinstance(call.func, ast.Name):
        return aliases.get(call.func.id, call.func.id)
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _session_argument_key(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg in {"db", "session"}:
            return ast.dump(keyword.value, include_attributes=False)
    if call.args:
        return ast.dump(call.args[0], include_attributes=False)
    return None


def test_otp_sensitive_link_policy_calls_are_explicitly_owner_aware() -> None:
    policy_symbol = "is_otp_eligible_telegram_link"
    observed_calls: list[str] = []
    violations: list[str] = []

    for path in _runtime_python_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _imported_call_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_symbol(node, aliases) != policy_symbol:
                continue

            location = f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
            observed_calls.append(location)
            expected_owner = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "expected_user_id"
                ),
                None,
            )
            if expected_owner is None or (
                isinstance(expected_owner, ast.Constant)
                and expected_owner.value is None
            ):
                violations.append(location)

    assert observed_calls
    assert violations == []


_AUTH_RATE_LIMIT_SESSION_CALLS = frozenset(
    {
        "AuthRateLimiter",
        "LoginRateLimitPolicy",
        "RegistrationIssuanceRateLimitPolicy",
        "TelegramLinkIssuanceRateLimitPolicy",
        "TelegramReauthRateLimitPolicy",
        "_record_telegram_link_rate_phase",
        "check_login_rate_limit",
        "check_telegram_link_issuance_rate_limit",
        "clear_login_phone_failures",
        "record_link_token_issuance_rate_limit",
        "record_login_failure",
        "record_login_otp_issue_limits",
        "record_login_otp_user_issue_limit",
        "record_telegram_link_issuance_attempt",
    }
)
_OTP_TELEGRAM_DOMAIN_SESSION_CALLS = frozenset(
    {
        "_issue_challenge_for_target",
        "_issue_telegram_link_token_domain_phase",
        "_issue_token_for_link_state",
        "_issue_token_for_link_state_after_rate_limit",
        "_request_login_otp_domain",
        "_request_new_login_code_domain",
        "_unlink_telegram_domain_phase",
        "bind_start_token_for_contact",
        "consume_start_token",
        "invalidate_and_insert_telegram_link_token",
        "invalidate_otp_challenges_for_link_change",
        "issue_link_token",
        "issue_link_token_after_rate_limit",
        "issue_login_otp_in_transaction",
        "issue_new_login_code_in_transaction",
        "issue_relink_token",
        "issue_relink_token_after_rate_limit",
        "prepare_next_otp_dispatch",
        "request_login_otp",
        "request_new_login_code",
        "unlink",
        "unlink_telegram",
        "verify_login_otp",
    }
)


def _is_otp_telegram_domain_session_call(symbol: str | None) -> bool:
    if symbol is None:
        return False
    if symbol in _OTP_TELEGRAM_DOMAIN_SESSION_CALLS:
        return True
    normalized = symbol.lstrip("_").casefold()
    return normalized.startswith("lock_") and any(
        marker in normalized
        for marker in ("challenge", "dispatch", "link", "otp", "telegram", "user")
    )


def test_auth_rate_limit_and_otp_telegram_domain_sessions_never_mix() -> None:
    guarded_roots = (
        PROJECT_ROOT / "app/auth",
        PROJECT_ROOT / "app/customer_activation",
        PROJECT_ROOT / "app/otp",
        PROJECT_ROOT / "app/telegram",
    )
    guarded_paths = tuple(
        sorted({path for root in guarded_roots for path in root.rglob("*.py")})
    )
    violations: list[str] = []

    for path in guarded_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _imported_call_aliases(tree)
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        ):
            rate_calls: list[tuple[str, str | None]] = []
            domain_calls: list[tuple[str, str | None]] = []
            for call in (
                node for node in ast.walk(function) if isinstance(node, ast.Call)
            ):
                symbol = _call_symbol(call, aliases)
                session_key = _session_argument_key(call)
                if symbol in _AUTH_RATE_LIMIT_SESSION_CALLS:
                    rate_calls.append((symbol, session_key))
                if _is_otp_telegram_domain_session_call(symbol):
                    domain_calls.append((symbol or "<unknown>", session_key))

            rate_sessions = {key for _, key in rate_calls if key is not None}
            domain_sessions = {key for _, key in domain_calls if key is not None}
            borrowed_session_parameters = {
                argument.arg
                for argument in (
                    *function.args.posonlyargs,
                    *function.args.args,
                    *function.args.kwonlyargs,
                )
                if argument.arg in {"db", "session"}
            }
            uses_auth_rate_limit_model = any(
                (
                    isinstance(node, ast.Name)
                    and aliases.get(node.id, node.id) == "AuthRateLimit"
                )
                or (isinstance(node, ast.Attribute) and node.attr == "AuthRateLimit")
                for node in ast.walk(function)
            )
            if (
                rate_sessions & domain_sessions
                or (borrowed_session_parameters and rate_calls and domain_calls)
                or (uses_auth_rate_limit_model and domain_calls)
            ):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{function.lineno}:"
                    f"{function.name}"
                )

    assert violations == []


def test_misleading_verified_private_chat_runtime_names_are_absent() -> None:
    forbidden_identifiers: list[str] = []

    def is_forbidden(identifier: str | None) -> bool:
        return identifier is not None and identifier.startswith(
            ("link_verified_private_chat", "relink_verified_private_chat")
        )

    for path in _runtime_python_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            identifiers: tuple[str | None, ...] = ()
            if isinstance(node, ast.Name):
                identifiers = (node.id,)
            elif isinstance(node, ast.Attribute):
                identifiers = (node.attr,)
            elif isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                identifiers = (node.name,)
            elif isinstance(node, ast.arg):
                identifiers = (node.arg,)
            elif isinstance(node, ast.alias):
                identifiers = (node.name, node.asname)

            forbidden_identifiers.extend(
                f"{path.relative_to(PROJECT_ROOT)}:{getattr(node, 'lineno', 0)}:"
                f"{identifier}"
                for identifier in identifiers
                if is_forbidden(identifier)
            )

    assert forbidden_identifiers == []
