from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.db import Base
from app.otp.models import (
    OtpChallenge,
    OtpChallengeEvent,
    OtpDispatch,
    OtpDispatcherState,
)

FORBIDDEN_OTP_COLUMNS = {
    "raw_otp",
    "otp",
    "code",
    "raw_code",
    "phone",
    "ip",
    "client_ip",
    "telegram_chat_id",
    "chat_id",
    "message",
    "message_text",
    "payload",
    "session_cookie",
    "cookie",
    "bot_token",
    "secret",
    "token",
    "metadata",
    "json",
    "outbox_id",
    "job_id",
    "scheduler_id",
    "worker_id",
    "deleted_at",
}


def check_constraints(model) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def unique_constraints(model) -> dict[str, set[str]]:
    return {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def indexes(model) -> dict[str, Index]:
    return {
        index.name: index
        for index in model.__table__.indexes
        if isinstance(index, Index)
    }


def single_foreign_key(model, column_name: str) -> ForeignKey:
    return next(iter(model.__table__.columns[column_name].foreign_keys))


def test_otp_tables_are_registered_with_exact_columns() -> None:
    assert Base.metadata.tables["otp_challenges"] is OtpChallenge.__table__
    assert Base.metadata.tables["otp_dispatches"] is OtpDispatch.__table__
    assert Base.metadata.tables["otp_challenge_events"] is OtpChallengeEvent.__table__
    assert Base.metadata.tables["otp_dispatcher_state"] is OtpDispatcherState.__table__

    assert set(OtpChallenge.__table__.columns.keys()) == {
        "id",
        "user_id",
        "purpose",
        "telegram_link_id",
        "telegram_linked_at",
        "browser_binding_digest",
        "code_mac",
        "status",
        "failed_attempts",
        "created_at",
        "activated_at",
        "expires_at",
        "consumed_at",
        "terminal_at",
        "updated_at",
    }
    assert set(OtpDispatch.__table__.columns.keys()) == {
        "id",
        "challenge_id",
        "status",
        "locale",
        "claimed_at",
        "prepared_at",
        "sent_at",
        "terminal_at",
        "failure_code",
        "created_at",
        "updated_at",
    }
    assert set(OtpChallengeEvent.__table__.columns.keys()) == {
        "id",
        "challenge_id",
        "user_id",
        "action",
        "occurred_at",
        "safe_code",
    }
    assert set(OtpDispatcherState.__table__.columns.keys()) == {
        "id",
        "heartbeat_at",
        "ready_at",
        "updated_at",
    }


def test_otp_primary_key_and_foreign_key_contracts() -> None:
    for model in (OtpChallenge, OtpDispatch, OtpChallengeEvent):
        id_column = model.__table__.columns["id"]
        assert isinstance(id_column.type, PostgresUUID)
        assert id_column.primary_key is True
        assert id_column.nullable is False

    user_fk = single_foreign_key(OtpChallenge, "user_id")
    assert user_fk.constraint.name == "fk_otp_challenges_user_id_users_id"
    assert user_fk.target_fullname == "users.id"
    assert user_fk.ondelete == "RESTRICT"

    link_fk = single_foreign_key(OtpChallenge, "telegram_link_id")
    assert link_fk.constraint.name == (
        "fk_otp_challenges_telegram_link_id_telegram_links_id"
    )
    assert link_fk.target_fullname == "telegram_links.id"
    assert link_fk.ondelete == "RESTRICT"

    dispatch_fk = single_foreign_key(OtpDispatch, "challenge_id")
    assert dispatch_fk.constraint.name == (
        "fk_otp_dispatches_challenge_id_otp_challenges_id"
    )
    assert dispatch_fk.target_fullname == "otp_challenges.id"
    assert dispatch_fk.ondelete == "RESTRICT"

    event_user_fk = single_foreign_key(OtpChallengeEvent, "user_id")
    assert event_user_fk.constraint.name == "fk_otp_challenge_events_user_id_users_id"
    assert event_user_fk.target_fullname == "users.id"
    assert event_user_fk.ondelete == "RESTRICT"
    assert not OtpChallengeEvent.__table__.columns["challenge_id"].foreign_keys


def test_otp_challenge_types_checks_and_indexes_match_contract() -> None:
    columns = OtpChallenge.__table__.columns
    constraints = check_constraints(OtpChallenge)

    assert isinstance(columns["purpose"].type, String)
    assert columns["purpose"].type.length == 16
    assert isinstance(columns["browser_binding_digest"].type, String)
    assert columns["browser_binding_digest"].type.length == 64
    assert isinstance(columns["code_mac"].type, String)
    assert columns["code_mac"].type.length == 64
    assert isinstance(columns["status"].type, String)
    assert columns["status"].type.length == 32
    assert isinstance(columns["failed_attempts"].type, Integer)

    assert constraints == {
        "ck_otp_challenges_purpose_login": "purpose = 'LOGIN'",
        "ck_otp_challenges_browser_binding_digest_hmac_sha256_hex": (
            "browser_binding_digest ~ '^[0-9a-f]{64}$'"
        ),
        "ck_otp_challenges_code_mac_hmac_sha256_hex": (
            "code_mac IS NULL OR code_mac ~ '^[0-9a-f]{64}$'"
        ),
        "ck_otp_challenges_status_allowed": (
            "status IN ('PENDING_DISPATCH', 'ACTIVE', 'CONSUMED', 'SUPERSEDED', "
            "'EXPIRED', 'BURNED', 'INVALIDATED')"
        ),
        "ck_otp_challenges_failed_attempts_cap": ("failed_attempts BETWEEN 0 AND 10"),
        "ck_otp_challenges_real_identity_consistent": (
            "(user_id IS NULL AND telegram_link_id IS NULL "
            "AND telegram_linked_at IS NULL) OR (user_id IS NOT NULL "
            "AND telegram_link_id IS NOT NULL AND telegram_linked_at IS NOT NULL)"
        ),
        "ck_otp_challenges_pending_dispatch_state": (
            "status != 'PENDING_DISPATCH' OR (code_mac IS NULL "
            "AND activated_at IS NULL AND expires_at IS NULL "
            "AND consumed_at IS NULL AND terminal_at IS NULL)"
        ),
        "ck_otp_challenges_active_state": (
            "status != 'ACTIVE' OR (user_id IS NOT NULL "
            "AND telegram_link_id IS NOT NULL AND telegram_linked_at IS NOT NULL "
            "AND code_mac IS NOT NULL AND activated_at IS NOT NULL "
            "AND expires_at IS NOT NULL AND expires_at > activated_at "
            "AND consumed_at IS NULL AND terminal_at IS NULL)"
        ),
        "ck_otp_challenges_terminal_state": (
            "(status IN ('PENDING_DISPATCH', 'ACTIVE') AND terminal_at IS NULL "
            "AND consumed_at IS NULL) OR (status = 'CONSUMED' "
            "AND terminal_at IS NOT NULL AND consumed_at IS NOT NULL) "
            "OR (status IN ('SUPERSEDED', 'EXPIRED', 'BURNED', 'INVALIDATED') "
            "AND terminal_at IS NOT NULL AND consumed_at IS NULL)"
        ),
        "ck_otp_challenges_timestamp_order": (
            "updated_at >= created_at "
            "AND (activated_at IS NULL OR activated_at >= created_at) "
            "AND (expires_at IS NULL OR activated_at IS NOT NULL) "
            "AND (consumed_at IS NULL OR activated_at IS NOT NULL) "
            "AND (terminal_at IS NULL OR terminal_at >= created_at)"
        ),
    }

    challenge_indexes = indexes(OtpChallenge)
    user_outstanding = challenge_indexes[
        "uq_otp_challenges_one_outstanding_per_user_purpose"
    ]
    browser_outstanding = challenge_indexes[
        "uq_otp_challenges_one_outstanding_per_browser_purpose"
    ]
    assert user_outstanding.unique is True
    assert [column.name for column in user_outstanding.columns] == [
        "user_id",
        "purpose",
    ]
    assert str(user_outstanding.dialect_options["postgresql"]["where"]) == (
        "status IN ('PENDING_DISPATCH', 'ACTIVE') AND user_id IS NOT NULL"
    )
    assert browser_outstanding.unique is True
    assert [column.name for column in browser_outstanding.columns] == [
        "browser_binding_digest",
        "purpose",
    ]
    assert str(browser_outstanding.dialect_options["postgresql"]["where"]) == (
        "status IN ('PENDING_DISPATCH', 'ACTIVE')"
    )
    assert [
        column.name
        for column in challenge_indexes["ix_otp_challenges_terminal_at"].columns
    ] == ["terminal_at"]


def test_otp_dispatch_types_checks_unique_and_indexes_match_contract() -> None:
    columns = OtpDispatch.__table__.columns
    constraints = check_constraints(OtpDispatch)

    assert unique_constraints(OtpDispatch)["uq_otp_dispatches_challenge_id"] == {
        "challenge_id"
    }
    assert isinstance(columns["status"].type, String)
    assert columns["status"].type.length == 32
    assert isinstance(columns["locale"].type, String)
    assert columns["locale"].type.length == 16
    assert isinstance(columns["failure_code"].type, String)
    assert columns["failure_code"].type.length == 64
    assert constraints == {
        "ck_otp_dispatches_status_allowed": (
            "status IN ('PENDING', 'PREPARED', 'SENT', 'FAILED', 'UNKNOWN', "
            "'CANCELLED')"
        ),
        "ck_otp_dispatches_locale_allowed": "locale IN ('uz-Latn', 'ru')",
        "ck_otp_dispatches_failure_code_format": (
            "failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'"
        ),
        "ck_otp_dispatches_state_consistent": (
            "(status = 'PENDING' AND prepared_at IS NULL AND sent_at IS NULL "
            "AND terminal_at IS NULL AND failure_code IS NULL) OR "
            "(status = 'PREPARED' AND claimed_at IS NOT NULL "
            "AND prepared_at IS NOT NULL AND sent_at IS NULL "
            "AND terminal_at IS NULL AND failure_code IS NULL) OR "
            "(status = 'SENT' AND prepared_at IS NOT NULL AND sent_at IS NOT NULL "
            "AND terminal_at IS NOT NULL AND failure_code IS NULL) OR "
            "(status IN ('FAILED', 'UNKNOWN') AND prepared_at IS NOT NULL "
            "AND sent_at IS NULL AND terminal_at IS NOT NULL "
            "AND failure_code IS NOT NULL) OR "
            "(status = 'CANCELLED' AND terminal_at IS NOT NULL "
            "AND sent_at IS NULL AND failure_code IS NULL)"
        ),
        "ck_otp_dispatches_timestamp_order": (
            "updated_at >= created_at "
            "AND (claimed_at IS NULL OR claimed_at >= created_at) "
            "AND (prepared_at IS NULL OR claimed_at IS NOT NULL) "
            "AND (sent_at IS NULL OR prepared_at IS NOT NULL) "
            "AND (terminal_at IS NULL OR terminal_at >= created_at)"
        ),
    }

    dispatch_indexes = indexes(OtpDispatch)
    assert [
        column.name
        for column in dispatch_indexes["ix_otp_dispatches_status_created_at"].columns
    ] == [
        "status",
        "created_at",
    ]
    assert [
        column.name
        for column in dispatch_indexes["ix_otp_dispatches_terminal_at"].columns
    ] == ["terminal_at"]


def test_otp_event_types_checks_and_indexes_match_contract() -> None:
    columns = OtpChallengeEvent.__table__.columns
    constraints = check_constraints(OtpChallengeEvent)

    assert isinstance(columns["challenge_id"].type, PostgresUUID)
    assert columns["challenge_id"].nullable is False
    assert isinstance(columns["action"].type, String)
    assert columns["action"].type.length == 40
    assert isinstance(columns["safe_code"].type, String)
    assert columns["safe_code"].type.length == 64
    assert constraints == {
        "ck_otp_challenge_events_action_allowed": (
            "action IN ('ISSUED', 'DISPATCH_PREPARED', 'DISPATCH_RESULT', "
            "'VERIFY_FAILED', 'CONSUMED', 'SUPERSEDED', 'EXPIRED', 'BURNED', "
            "'INVALIDATED_BY_LINK_CHANGE')"
        ),
        "ck_otp_challenge_events_safe_code_format": (
            "safe_code IS NULL OR safe_code ~ '^[A-Z][A-Z0-9_]{0,63}$'"
        ),
    }

    event_indexes = indexes(OtpChallengeEvent)
    assert [
        column.name
        for column in event_indexes[
            "ix_otp_challenge_events_challenge_id_occurred_at"
        ].columns
    ] == [
        "challenge_id",
        "occurred_at",
    ]
    assert [
        column.name
        for column in event_indexes["ix_otp_challenge_events_occurred_at"].columns
    ] == ["occurred_at"]


def test_otp_dispatcher_state_types_and_checks_match_contract() -> None:
    columns = OtpDispatcherState.__table__.columns
    constraints = check_constraints(OtpDispatcherState)

    assert isinstance(columns["id"].type, SmallInteger)
    assert columns["id"].primary_key is True
    assert constraints == {
        "ck_otp_dispatcher_state_singleton": "id = 1",
        "ck_otp_dispatcher_state_ready_requires_heartbeat": (
            "ready_at IS NULL OR heartbeat_at IS NOT NULL"
        ),
        "ck_otp_dispatcher_state_heartbeat_not_before_ready": (
            "heartbeat_at IS NULL OR ready_at IS NULL OR heartbeat_at >= ready_at"
        ),
    }


def test_otp_timestamps_are_timezone_aware() -> None:
    timestamp_columns = {
        OtpChallenge: (
            "telegram_linked_at",
            "created_at",
            "activated_at",
            "expires_at",
            "consumed_at",
            "terminal_at",
            "updated_at",
        ),
        OtpDispatch: (
            "claimed_at",
            "prepared_at",
            "sent_at",
            "terminal_at",
            "created_at",
            "updated_at",
        ),
        OtpChallengeEvent: ("occurred_at",),
        OtpDispatcherState: ("heartbeat_at", "ready_at", "updated_at"),
    }

    for model, names in timestamp_columns.items():
        for name in names:
            column = model.__table__.columns[name]
            assert isinstance(column.type, DateTime)
            assert column.type.timezone is True


def test_otp_models_have_no_forbidden_raw_or_generic_columns() -> None:
    for model in (OtpChallenge, OtpDispatch, OtpChallengeEvent, OtpDispatcherState):
        assert FORBIDDEN_OTP_COLUMNS.isdisjoint(model.__table__.columns)
