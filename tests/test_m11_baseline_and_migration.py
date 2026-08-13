import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime
from inspect import getsource
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.customer_activation.service as activation_service_module
import app.otp.repository as otp_repository_module
from alembic import command
from app.audit.models import AuditLog
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.customer.models import Customer
from app.customer.repository import lock_existing_own_customer_for_update
from app.customer_activation.repository import (
    SqlAlchemyCustomerDocumentReadiness,
    SqlAlchemyCustomerIdentityReadiness,
    SqlAlchemyRegistrationOfferReadiness,
)
from app.customer_document.models import CustomerDocument
from app.customer_identity.models import CustomerIdentity
from app.offers.models import OfferAcceptance
from app.otp.contracts import OtpChallengeStatus, OtpPurpose
from app.otp.crypto import OtpBrowserBindingDigest
from app.otp.models import OtpChallenge, OtpChallengeEvent
from app.otp.repository import (
    OtpChallengeInsertConflict,
    create_pending_challenge,
    create_pending_registration_challenge,
    lock_registration_candidate_set_by_browser,
)
from app.settings import Settings
from app.shop.models import Shop
from app.telegram.models import TelegramLink
from tests.m11_seed import (
    REGISTRATION_DIGEST,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M10_REVISION = "b0c1d2e3f4a5"
ORIGINAL_M11_REVISION = "c1d2e3f4a5b6"
M11_REVISION = "d2e3f4a5b6c7"
M12_REVISION = "e3f4a5b6c7d8"
M13_REVISION = "f4a5b6c7d8e"
M14_REVISION = "a5b6c7d8e9f0"
M15_REVISION = "b6c7d8e9f0a1"
M16_REVISION = "c7d8e9f0a1b2"
M17_REVISION = "d8e9f0a1b2c3"
NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
GLOBAL_REGISTRATION_LOCK_ORDER = (
    "TelegramLinkToken",
    "OtpDispatch",
    "OtpChallenge",
    "User",
    "TelegramLink",
    "Customer",
    "OfferVersion",
    "OfferAcceptance",
    "CustomerIdentity",
    "ObjectFile",
    "CustomerDocument",
    "AuthSession",
)


def test_m10_baseline_and_protected_source_pins_are_exact() -> None:
    tt_bytes = (PROJECT_ROOT / "docs/tt_nasiya_web_v1.md").read_bytes()
    git_blob = f"blob {len(tt_bytes)}\0".encode() + tt_bytes
    scope_source = (PROJECT_ROOT / "docs/m11_scope_contract.md").read_text(
        encoding="utf-8"
    )

    assert hashlib.sha1(git_blob).hexdigest() == (
        "d77c0f0f330a1330155a4aee3c46b05d97cf5561"
    )
    for pinned_evidence in (
        "b79250858a3f6a63908a288f891d5dad1126dd48",
        "30705134413",
        "2735 passed",
        "8/8",
        "17ded4cacee9f80728139feee91c67451570be66a9d76f604d0be2346f83b9f9",
        "f9a7109a4439ea889cc982210e01ae069489606d3d6103cc57e1a88c1fd1f7d5",
        "48de725166daaa07e2a0998bca1e907caedc6050cd3ad8740b8a34d3d79ce8e0",
        "08668a326d682a175cc62366b1ca7092963f02457c3cb6f876cfab08f812a526",
        "562556c2462828db8bfff2747096dbe929ded7c499626bfc13a75ee1524395c3",
        "68badd200a843148f48de8ffbfe0530502b2f7230210959a9cf6c4f99d0f94a7",
        "a2bf649887f7d7701a26cd518b8f8876dd0a85b0198a326f9327a0c02ff3d1e2",
        "seven original committed checkpoints",
        "eight total M11 implementation commits",
    ):
        assert pinned_evidence in scope_source


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert isinstance(revision, str)
    return revision


def _table_indexes(engine: Engine) -> set[tuple[str, str]]:
    inspector = inspect(engine)
    return {
        (table_name, index["name"])
        for table_name in inspector.get_table_names()
        for index in inspector.get_indexes(table_name)
        if "duplicates_constraint" not in index
    }


def _check_sql(engine: Engine, table_name: str) -> dict[str, str]:
    return {
        check["name"]: check["sqltext"]
        for check in inspect(engine).get_check_constraints(table_name)
    }


def _restore_draft_if_m11(engine: Engine) -> None:
    if "activated_at" not in {
        column["name"] for column in inspect(engine).get_columns("customers")
    }:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE customers SET onboarding_status = 'draft', "
                "activated_at = NULL WHERE onboarding_status = 'active' "
                "OR activated_at IS NOT NULL"
            )
        )


def _clear_cr_m11_02_state(engine: Engine) -> None:
    inspector = inspect(engine)
    if "telegram_link_tokens" not in set(inspector.get_table_names()):
        return
    if "pending_contact_binding_mac" in {
        column["name"] for column in inspector.get_columns("telegram_link_tokens")
    }:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE telegram_link_tokens SET "
                    "pending_contact_binding_mac = NULL, "
                    "contact_requested_at = NULL"
                )
            )
            connection.execute(
                text("UPDATE telegram_links SET phone_verified_at = NULL")
            )


def test_m11_migrations_remain_a_source_scoped_zero_table_chain() -> None:
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [M17_REVISION]
    m12_revision = scripts.get_revision(M12_REVISION)
    recovery_revision = scripts.get_revision(M11_REVISION)
    original_revision = scripts.get_revision(ORIGINAL_M11_REVISION)
    assert m12_revision is not None
    assert recovery_revision is not None
    assert original_revision is not None
    assert m12_revision.down_revision == M11_REVISION
    assert recovery_revision.down_revision == ORIGINAL_M11_REVISION
    assert original_revision.down_revision == M10_REVISION

    original_source = (
        PROJECT_ROOT
        / "alembic/versions/c1d2e3f4a5b6_extend_customer_activation_foundation.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "op.create_table",
        "sa.Enum",
        "CREATE TYPE",
        "CREATE SEQUENCE",
        "CREATE TRIGGER",
        "CREATE FUNCTION",
        "CREATE VIEW",
        "op.create_index",
    ):
        assert forbidden not in original_source

    recovery_source = (
        PROJECT_ROOT
        / "alembic/versions/d2e3f4a5b6c7_add_telegram_self_phone_verification.py"
    ).read_text(encoding="utf-8")
    assert "op.create_table" not in recovery_source
    assert recovery_source.count("op.create_index(") == 1
    assert recovery_source.count("op.add_column(") == 3


def test_global_registration_lock_order_is_static_and_workaround_free() -> None:
    otp_lock_source = getsource(
        otp_repository_module._lock_outstanding_challenge_set_for_purposes
    )
    issue_source = getsource(activation_service_module._issue_registration_otp)
    verify_source = getsource(
        activation_service_module.recheck_registration_activation_snapshot
    )
    activation_source = getsource(
        activation_service_module.verify_and_activate_registration_customer
    )
    document_source = getsource(
        SqlAlchemyCustomerDocumentReadiness.lock_current_available_document
    )
    inspected = "\n".join(
        (
            otp_lock_source,
            issue_source,
            verify_source,
            activation_source,
            document_source,
        )
    )

    assert GLOBAL_REGISTRATION_LOCK_ORDER == (
        "TelegramLinkToken",
        "OtpDispatch",
        "OtpChallenge",
        "User",
        "TelegramLink",
        "Customer",
        "OfferVersion",
        "OfferAcceptance",
        "CustomerIdentity",
        "ObjectFile",
        "CustomerDocument",
        "AuthSession",
    )
    assert otp_lock_source.index("select(OtpDispatch)") < otp_lock_source.index(
        "select(OtpChallenge)"
    )
    assert (
        issue_source.index("lock_outstanding_challenge_set_by_user")
        < (issue_source.index("session.get(User"))
        < issue_source.index("get_telegram_link_by_user_for_update")
        < (issue_source.index("lock_existing_own_customer_for_update"))
        < issue_source.index("select_current_registration_acceptance")
        < (issue_source.index("SqlAlchemyCustomerIdentityReadiness"))
        < issue_source.index("SqlAlchemyCustomerDocumentReadiness")
    )
    assert (
        verify_source.index("session.get(User")
        < verify_source.index("session.get(\n        TelegramLink")
        < verify_source.index("lock_existing_own_customer_for_update")
        < (verify_source.index("select_current_registration_acceptance"))
        < verify_source.index("SqlAlchemyCustomerIdentityReadiness")
        < (verify_source.index("SqlAlchemyCustomerDocumentReadiness"))
    )
    assert document_source.index("self._session.get(\n            ObjectFile") < (
        document_source.index("select(CustomerDocument)")
    )
    assert activation_source.index("append_audit_event") < activation_source.index(
        "SqlAlchemyCurrentSessionRotation"
    )
    assert all(
        marker not in inspected
        for marker in (
            "sleep(",
            "pg_advisory",
            "pg_try_advisory",
            "retry",
            "lock_timeout",
        )
    )


@pytest.mark.integration
def test_original_m11_and_recovery_migration_walk_is_deterministic(
    m2_test_database: Engine,
) -> None:
    config = _config()
    user_id = uuid4()
    customer_id = uuid4()
    challenge_id = uuid4()
    audit_id = uuid4()
    try:
        command.downgrade(config, "base")
        assert set(inspect(m2_test_database).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M11_REVISION

        command.downgrade(config, ORIGINAL_M11_REVISION)
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, phone, password_hash, is_active, is_platform_admin, "
                    "created_at, updated_at) "
                    "VALUES (:id, :phone, NULL, true, false, :now, :now)"
                ),
                {
                    "id": user_id,
                    "phone": "+997900001301",
                    "now": NOW,
                },
            )
        with pytest.raises(
            RuntimeError,
            match="CR-M11-02 upgrade blocked: noncanonical user phone exists",
        ):
            command.upgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == ORIGINAL_M11_REVISION
        assert "pending_contact_binding_mac" not in {
            column["name"]
            for column in inspect(m2_test_database).get_columns("telegram_link_tokens")
        }
        with m2_test_database.begin() as connection:
            connection.execute(
                text("DELETE FROM users WHERE id = :id"),
                {"id": user_id},
            )
        command.upgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M11_REVISION

        command.downgrade(config, M10_REVISION)
        m10_inspector = inspect(m2_test_database)
        m10_tables = set(m10_inspector.get_table_names())
        m10_indexes = _table_indexes(m2_test_database)
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, phone, password_hash, is_active, is_platform_admin, "
                    "created_at, updated_at) "
                    "VALUES (:id, :phone, NULL, true, false, :now, :now)"
                ),
                {"id": user_id, "phone": "+998900001301", "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO customers "
                    "(id, user_id, onboarding_status, created_at, updated_at) "
                    "VALUES (:id, :user_id, 'draft', :now, :now)"
                ),
                {"id": customer_id, "user_id": user_id, "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO otp_challenges "
                    "(id, user_id, purpose, telegram_link_id, "
                    "telegram_linked_at, browser_binding_digest, code_mac, "
                    "status, failed_attempts, created_at, activated_at, "
                    "expires_at, consumed_at, terminal_at, updated_at) "
                    "VALUES (:id, NULL, 'LOGIN', NULL, NULL, :binding, NULL, "
                    "'PENDING_DISPATCH', 0, :now, NULL, NULL, NULL, NULL, :now)"
                ),
                {"id": challenge_id, "binding": "a" * 64, "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, occurred_at, event_type, actor_kind, actor_user_id, "
                    "object_type, object_id, payload) "
                    "VALUES (:id, :now, 'customer.identity_saved', 'USER', "
                    ":user_id, 'customer_identity', :object_id, "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "id": audit_id,
                    "now": NOW,
                    "user_id": user_id,
                    "object_id": customer_id,
                    "payload": json.dumps(
                        {
                            "revision": 1,
                            "created_or_updated": "created",
                            "document_type": "ID_CARD",
                        }
                    ),
                },
            )

        command.upgrade(config, M11_REVISION)
        m11_inspector = inspect(m2_test_database)
        assert _current_revision(m2_test_database) == M11_REVISION
        assert set(m11_inspector.get_table_names()) == m10_tables
        assert _table_indexes(m2_test_database) == m10_indexes | {
            (
                "telegram_link_tokens",
                "uq_telegram_link_tokens_pending_contact_binding_mac_outstanding",
            )
        }
        customer_columns = {
            column["name"] for column in m11_inspector.get_columns("customers")
        }
        assert customer_columns == {
            "id",
            "user_id",
            "onboarding_status",
            "created_at",
            "updated_at",
            "activated_at",
        }
        challenge_columns = {
            column["name"] for column in m11_inspector.get_columns("otp_challenges")
        }
        assert {
            "customer_id",
            "registration_offer_acceptance_id",
            "customer_identity_revision",
            "customer_document_id",
        } <= challenge_columns
        assert {
            "pending_contact_binding_mac",
            "contact_requested_at",
        } <= {
            column["name"]
            for column in m11_inspector.get_columns("telegram_link_tokens")
        }
        assert "phone_verified_at" in {
            column["name"] for column in m11_inspector.get_columns("telegram_links")
        }
        assert {
            foreign_key["name"]: foreign_key["options"]["ondelete"]
            for foreign_key in m11_inspector.get_foreign_keys("otp_challenges")
            if foreign_key["name"].startswith("fk_otp_challenges_customer")
            or foreign_key["name"].startswith(
                "fk_otp_challenges_registration_acceptance"
            )
        } == {
            "fk_otp_challenges_customer_id_customers_id": "RESTRICT",
            "fk_otp_challenges_registration_acceptance_offer_acceptances": ("RESTRICT"),
            "fk_otp_challenges_customer_document_id_customer_documents": "RESTRICT",
        }
        with m2_test_database.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT onboarding_status, activated_at FROM customers "
                    "WHERE id = :id"
                ),
                {"id": customer_id},
            ).one() == ("draft", None)
            assert (
                connection.execute(
                    text(
                        "SELECT pending_contact_binding_mac, contact_requested_at "
                        "FROM telegram_link_tokens"
                    )
                ).all()
                == []
            )
            assert (
                connection.execute(
                    text("SELECT phone_verified_at FROM telegram_links")
                ).all()
                == []
            )
            assert connection.execute(
                text(
                    "SELECT customer_id, registration_offer_acceptance_id, "
                    "customer_identity_revision, customer_document_id "
                    "FROM otp_challenges WHERE id = :id"
                ),
                {"id": challenge_id},
            ).one() == (None, None, None, None)
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM audit_log WHERE id = :id"),
                    {"id": audit_id},
                )
                == 1
            )

        customer_checks = _check_sql(m2_test_database, "customers")
        assert {
            "ck_customers_onboarding_status_allowed",
            "ck_customers_activation_state_consistent",
            "ck_customers_timestamp_order",
        } <= set(customer_checks)
        otp_checks = _check_sql(m2_test_database, "otp_challenges")
        assert "ck_otp_challenges_purpose_allowed" in otp_checks
        assert "ck_otp_challenges_registration_context_matches_purpose" in otp_checks
        assert "REGISTRATION" in otp_checks["ck_otp_challenges_purpose_allowed"]
        event_checks = _check_sql(m2_test_database, "otp_challenge_events")
        assert (
            "INVALIDATED_BY_REGISTRATION_STATE_CHANGE"
            in event_checks["ck_otp_challenge_events_action_allowed"]
        )
        audit_checks = _check_sql(m2_test_database, "audit_log")
        assert "customer.activated" in audit_checks["ck_audit_log_event_type_allowed"]
        assert (
            "TELEGRAM_REGISTRATION_OTP"
            in audit_checks["ck_audit_log_payload_exact_shape"]
        )
        assert {
            "ck_telegram_link_tokens_pending_contact_binding_mac_format",
            "ck_telegram_link_tokens_pending_contact_state_consistent",
            "ck_telegram_link_tokens_pending_contact_timestamp_order",
        } <= set(_check_sql(m2_test_database, "telegram_link_tokens"))
        assert "ck_telegram_links_phone_verification_consistent" in _check_sql(
            m2_test_database, "telegram_links"
        )
        assert "ck_users_phone_canonical_uz_e164" in _check_sql(
            m2_test_database, "users"
        )

        command.downgrade(config, M10_REVISION)
        downgraded = inspect(m2_test_database)
        assert _current_revision(m2_test_database) == M10_REVISION
        assert "activated_at" not in {
            column["name"] for column in downgraded.get_columns("customers")
        }
        assert {
            "customer_id",
            "registration_offer_acceptance_id",
            "customer_identity_revision",
            "customer_document_id",
        }.isdisjoint(
            {column["name"] for column in downgraded.get_columns("otp_challenges")}
        )
        assert {
            "pending_contact_binding_mac",
            "contact_requested_at",
        }.isdisjoint(
            {
                column["name"]
                for column in downgraded.get_columns("telegram_link_tokens")
            }
        )
        assert "phone_verified_at" not in {
            column["name"] for column in downgraded.get_columns("telegram_links")
        }
        assert (
            "customer.activated"
            not in _check_sql(m2_test_database, "audit_log")[
                "ck_audit_log_event_type_allowed"
            ]
        )
        assert (
            "INVALIDATED_BY_REGISTRATION_STATE_CHANGE"
            not in _check_sql(m2_test_database, "otp_challenge_events")[
                "ck_otp_challenge_events_action_allowed"
            ]
        )
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM customers WHERE id = :id"),
                    {"id": customer_id},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM otp_challenges WHERE id = :id"),
                    {"id": challenge_id},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM audit_log WHERE id = :id"),
                    {"id": audit_id},
                )
                == 1
            )

        command.upgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M11_REVISION
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE customers SET onboarding_status = 'active', "
                    "activated_at = :now, updated_at = :now WHERE id = :id"
                ),
                {"id": customer_id, "now": NOW},
            )

        with pytest.raises(
            RuntimeError,
            match="M11 downgrade blocked: active customer state exists",
        ):
            command.downgrade(config, M10_REVISION)

        assert _current_revision(m2_test_database) == M11_REVISION
        with m2_test_database.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT onboarding_status, activated_at FROM customers "
                    "WHERE id = :id"
                ),
                {"id": customer_id},
            ).one() == ("active", NOW)
    finally:
        _clear_cr_m11_02_state(m2_test_database)
        _restore_draft_if_m11(m2_test_database)
        command.upgrade(config, "head")


@pytest.mark.integration
def test_recovery_downgrade_fails_closed_with_pending_or_verified_state(
    m2_test_database: Engine,
) -> None:
    config = _config()
    user_id = uuid4()
    token_id = uuid4()
    link_id = uuid4()
    try:
        command.downgrade(config, M11_REVISION)
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, phone, password_hash, is_active, is_platform_admin, "
                    "created_at, updated_at) "
                    "VALUES (:id, :phone, NULL, true, false, :now, :now)"
                ),
                {"id": user_id, "phone": "+998900001390", "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO telegram_link_tokens "
                    "(id, user_id, token_hash, created_at, expires_at, "
                    "consumed_at, invalidated_at, pending_contact_binding_mac, "
                    "contact_requested_at) VALUES ("
                    ":id, :user_id, :token_hash, :now, :expires_at, NULL, NULL, "
                    ":binding_mac, :now)"
                ),
                {
                    "id": token_id,
                    "user_id": user_id,
                    "token_hash": "e" * 64,
                    "now": NOW,
                    "expires_at": NOW.replace(hour=11),
                    "binding_mac": "f" * 64,
                },
            )

        with pytest.raises(
            RuntimeError,
            match=(
                "CR-M11-02 downgrade blocked: pending or verified Telegram state exists"
            ),
        ):
            command.downgrade(config, ORIGINAL_M11_REVISION)

        assert _current_revision(m2_test_database) == M11_REVISION
        assert "pending_contact_binding_mac" in {
            column["name"]
            for column in inspect(m2_test_database).get_columns("telegram_link_tokens")
        }
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT pending_contact_binding_mac IS NOT NULL "
                        "AND contact_requested_at IS NOT NULL "
                        "FROM telegram_link_tokens WHERE id = :id"
                    ),
                    {"id": token_id},
                )
                is True
            )

        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE telegram_link_tokens SET "
                    "pending_contact_binding_mac = NULL, "
                    "contact_requested_at = NULL WHERE id = :id"
                ),
                {"id": token_id},
            )
        command.downgrade(config, ORIGINAL_M11_REVISION)
        assert _current_revision(m2_test_database) == ORIGINAL_M11_REVISION
        command.upgrade(config, M11_REVISION)

        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO telegram_links "
                    "(id, user_id, telegram_chat_id, linked_at, unlinked_at, "
                    "phone_verified_at, updated_at) VALUES ("
                    ":id, :user_id, :chat_id, :now, NULL, :now, :now)"
                ),
                {
                    "id": link_id,
                    "user_id": user_id,
                    "chat_id": 1_001_390,
                    "now": NOW,
                },
            )

        with pytest.raises(
            RuntimeError,
            match=(
                "CR-M11-02 downgrade blocked: pending or verified Telegram state exists"
            ),
        ):
            command.downgrade(config, ORIGINAL_M11_REVISION)

        assert _current_revision(m2_test_database) == M11_REVISION
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT phone_verified_at = linked_at "
                        "FROM telegram_links WHERE id = :id"
                    ),
                    {"id": link_id},
                )
                is True
            )

        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE telegram_links SET phone_verified_at = NULL WHERE id = :id"
                ),
                {"id": link_id},
            )
        command.downgrade(config, ORIGINAL_M11_REVISION)
        assert _current_revision(m2_test_database) == ORIGINAL_M11_REVISION
        command.upgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M11_REVISION
    finally:
        _clear_cr_m11_02_state(m2_test_database)
        command.upgrade(config, "head")


@pytest.mark.integration
def test_m11_constraints_restrict_invalid_state_and_preserve_login(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001302",
        )

        def expect_integrity(action: object) -> None:
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    action()  # type: ignore[operator]
                    session.flush()
            assert session.scalar(select(1)) == 1

        login = create_pending_challenge(
            session,
            browser_binding_digest=OtpBrowserBindingDigest("8" * 64),
            purpose=OtpPurpose.LOGIN,
            now=NOW,
        )
        session.flush()
        assert login.purpose == OtpPurpose.LOGIN.value
        assert (
            login.customer_id,
            login.registration_offer_acceptance_id,
            login.customer_identity_revision,
            login.customer_document_id,
        ) == (None, None, None, None)

        def add_registration_without_context() -> None:
            session.add(
                OtpChallenge(
                    user_id=snapshot.user_id,
                    purpose=OtpPurpose.REGISTRATION.value,
                    telegram_link_id=snapshot.telegram_link_id,
                    telegram_linked_at=snapshot.telegram_linked_at,
                    browser_binding_digest="7" * 64,
                    status=OtpChallengeStatus.PENDING_DISPATCH.value,
                    failed_attempts=0,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

        expect_integrity(add_registration_without_context)

        customer = session.get(Customer, snapshot.customer_id)
        assert customer is not None

        def activate_without_timestamp() -> None:
            customer.onboarding_status = "active"
            customer.activated_at = None

        expect_integrity(activate_without_timestamp)
        session.refresh(customer)

        registration = create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        session.flush()

        def append_invalid_event() -> None:
            session.add(
                OtpChallengeEvent(
                    challenge_id=registration.id,
                    user_id=snapshot.user_id,
                    action="INVALID_M11_ACTION",
                    occurred_at=NOW,
                )
            )

        expect_integrity(append_invalid_event)

        def append_invalid_audit() -> None:
            session.add(
                AuditLog(
                    occurred_at=NOW,
                    event_type="customer.activated",
                    actor_kind="USER",
                    actor_user_id=snapshot.user_id,
                    object_type="customer",
                    object_id=snapshot.customer_id,
                    payload={"method": "INVALID"},
                )
            )

        expect_integrity(append_invalid_audit)

        for model, identifier in (
            (OfferAcceptance, snapshot.registration_offer_acceptance_id),
            (CustomerDocument, snapshot.customer_document_id),
            (Customer, snapshot.customer_id),
        ):
            expect_integrity(
                lambda model=model, identifier=identifier: session.execute(
                    delete(model).where(model.id == identifier)
                )
            )

        conflicting_snapshot = type(snapshot)(
            user_id=snapshot.user_id,
            customer_id=snapshot.customer_id,
            telegram_link_id=snapshot.telegram_link_id,
            telegram_linked_at=snapshot.telegram_linked_at,
            registration_offer_acceptance_id=(
                snapshot.registration_offer_acceptance_id
            ),
            customer_identity_revision=snapshot.customer_identity_revision,
            customer_document_id=snapshot.customer_document_id,
            browser_binding_digest=OtpBrowserBindingDigest("6" * 64),
        )
        with pytest.raises(OtpChallengeInsertConflict):
            create_pending_registration_challenge(
                session,
                snapshot=conflicting_snapshot,
                now=NOW,
            )
        assert session.scalar(select(func.count()).select_from(OtpChallenge)) == 2


@pytest.mark.integration
def test_parallel_forward_challenge_customer_session_locks_do_not_deadlock(
    m2_test_database: Engine,
) -> None:
    assert Shop.__tablename__ == "shops"
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone="+998900001303",
        )
        create_pending_registration_challenge(
            session,
            snapshot=snapshot,
            now=NOW,
        )
        current = create_authenticated_session(
            session,
            snapshot.user_id,
            "synthetic-browser",
            NOW,
            settings=Settings(),
        )
        session.flush()
        current_session_id = current.session.id

    start = Barrier(2)

    def lock_forward() -> tuple[bool, ...]:
        with Session(m2_test_database) as session, session.begin():
            start.wait(timeout=5)
            locked_otp = lock_registration_candidate_set_by_browser(
                session,
                browser_binding_digest=REGISTRATION_DIGEST,
            )
            user = session.get(User, snapshot.user_id, with_for_update=True)
            link = session.get(
                TelegramLink,
                snapshot.telegram_link_id,
                with_for_update=True,
            )
            customer = lock_existing_own_customer_for_update(
                session,
                actor_user_id=snapshot.user_id,
            )
            acceptance_id = SqlAlchemyRegistrationOfferReadiness(
                session
            ).lock_earliest_exact_current_acceptance(
                actor_user_id=snapshot.user_id,
            )
            revision = SqlAlchemyCustomerIdentityReadiness(
                session,
                crypto_config=synthetic_identity_crypto_config(),
            ).lock_complete_identity_revision(customer_id=snapshot.customer_id)
            document_id = SqlAlchemyCustomerDocumentReadiness(
                session
            ).lock_current_available_document(customer_id=snapshot.customer_id)
            auth_session = session.get(
                AuthSession,
                current_session_id,
                with_for_update=True,
            )
            return (
                len(locked_otp.challenges) == 1,
                user is not None,
                link is not None,
                customer is not None,
                acceptance_id == snapshot.registration_offer_acceptance_id,
                revision == snapshot.customer_identity_revision,
                document_id == snapshot.customer_document_id,
                auth_session is not None,
            )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(lock_forward) for _ in range(2)]
        completed, pending = wait(futures, timeout=10)
        assert not pending
        assert len(completed) == 2
        assert all(all(result) for result in (future.result() for future in futures))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(OtpChallenge)) == 1
        customer = session.get(Customer, snapshot.customer_id)
        current_session = session.get(AuthSession, current_session_id)
        assert customer is not None and customer.onboarding_status == "draft"
        assert current_session is not None and current_session.revoked_at is None
        assert session.scalar(select(func.count()).select_from(CustomerIdentity)) == 1
