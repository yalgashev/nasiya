import logging
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.auth.models import AuthRateLimit, User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.bot import TelegramBotUsername
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramChatAlreadyLinkedError,
    TelegramLinkLifecycleInternalError,
    TelegramLinkTokenConsumeError,
    TelegramLinkTokenIssueError,
    TelegramLinkTokenIssueInternalError,
    consume_start_token,
    issue_link_token,
)
from app.telegram.token import (
    RawTelegramLinkToken,
    build_telegram_start_link,
    hash_telegram_link_token,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_RATE_LIMIT_HMAC_KEY = "pytest-only-telegram-leakage-audit-hmac-key"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        telegram_bot_username="LeakAuditBot",
    )


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def table_text(session: Session, query: str) -> str:
    rows = session.execute(text(query)).all()
    return "|".join(str(value) for row in rows for value in row)


def persisted_text_by_table(session: Session) -> dict[str, str]:
    return {
        "users": table_text(
            session,
            "SELECT id::text, phone, password_hash, is_active::text, "
            "created_at::text, updated_at::text FROM users",
        ),
        "sessions": table_text(
            session,
            "SELECT id::text, user_id::text, token_hash, csrf_secret, "
            "user_agent, created_at::text, last_seen_at::text, expires_at::text, "
            "revoked_at::text FROM sessions",
        ),
        "auth_rate_limits": table_text(
            session,
            "SELECT scope, key_hash, window_started_at::text, "
            "attempt_count::text, updated_at::text FROM auth_rate_limits",
        ),
        "customers": table_text(
            session,
            "SELECT id::text, user_id::text, onboarding_status, created_at::text, "
            "updated_at::text FROM customers",
        ),
        "telegram_links": table_text(
            session,
            "SELECT id::text, user_id::text, telegram_chat_id::text, "
            "linked_at::text, unlinked_at::text, updated_at::text "
            "FROM telegram_links",
        ),
        "telegram_link_tokens": table_text(
            session,
            "SELECT id::text, user_id::text, token_hash, created_at::text, "
            "expires_at::text, consumed_at::text, invalidated_at::text "
            "FROM telegram_link_tokens",
        ),
        "telegram_link_events": table_text(
            session,
            "SELECT id::text, user_id::text, action, occurred_at::text "
            "FROM telegram_link_events",
        ),
    }


def assert_values_absent(values: tuple[str, ...], *texts: str) -> None:
    if any(value and value in text_value for value in values for text_value in texts):
        pytest.fail("sensitive Telegram value leaked", pytrace=False)


def test_m4_persistence_stores_only_approved_sensitive_fields(
    caplog,
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    phone = "+998900012070"
    raw_ip = "203.0.113.170"
    raw_token_value = "pytest_m4_70_raw_link_token"
    chat_id = 981_701
    now = datetime(2026, 7, 25, 17, 0, tzinfo=UTC)
    user = add_user(db_session, phone)
    client_ip = ResolvedClientIp(raw_ip)
    chat_identity = VerifiedPrivateTelegramChatIdentity(chat_id)

    with caplog.at_level(logging.DEBUG):
        issued = issue_link_token(
            db_session,
            settings,
            user,
            client_ip,
            now,
            token_generator=lambda _byte_count: raw_token_value,
        )
        start_link = build_telegram_start_link(
            settings.telegram_bot_username,
            issued.raw_token,
        )
        consumed = consume_start_token(
            db_session,
            issued.raw_token,
            chat_identity,
            now,
        )

    full_deep_link = start_link.as_delivery_url()
    raw_token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token_value))
    persisted_by_table = persisted_text_by_table(db_session)
    all_persisted_text = "|".join(persisted_by_table.values())
    non_link_persisted_text = "|".join(
        value
        for table_name, value in persisted_by_table.items()
        if table_name != "telegram_links"
    )

    assert consumed.link.telegram_chat_id == chat_id
    assert consumed.event is not None
    assert consumed.event.action == "linked"
    assert count_table(db_session, AuthRateLimit) == 3
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 1
    assert count_table(db_session, Customer) == 0

    assert raw_token_hash in persisted_by_table["telegram_link_tokens"]
    assert str(chat_id) in persisted_by_table["telegram_links"]
    assert str(chat_id) not in non_link_persisted_text
    assert_values_absent(
        (
            raw_token_value,
            full_deep_link,
            "?start=",
            "https://t.me",
            raw_ip,
            "leakauditbot",
        ),
        all_persisted_text,
    )
    assert_values_absent(
        (
            phone,
            raw_ip,
            str(user.id),
            str(chat_id),
            raw_token_value,
            full_deep_link,
        ),
        persisted_by_table["auth_rate_limits"],
    )
    assert_values_absent(
        (phone, raw_ip, raw_token_value, full_deep_link, str(chat_id)),
        persisted_by_table["telegram_link_tokens"],
        persisted_by_table["telegram_link_events"],
    )
    assert caplog.text == ""


def test_m4_schema_has_no_profile_update_json_or_broad_metadata_persistence() -> None:
    assert set(TelegramLink.__table__.columns.keys()) == {
        "id",
        "user_id",
        "telegram_chat_id",
        "linked_at",
        "unlinked_at",
        "updated_at",
    }
    assert set(TelegramLinkToken.__table__.columns.keys()) == {
        "id",
        "user_id",
        "token_hash",
        "created_at",
        "expires_at",
        "consumed_at",
        "invalidated_at",
    }
    assert set(TelegramLinkEvent.__table__.columns.keys()) == {
        "id",
        "user_id",
        "action",
        "occurred_at",
    }
    assert set(AuthRateLimit.__table__.columns.keys()) == {
        "scope",
        "key_hash",
        "window_started_at",
        "attempt_count",
        "updated_at",
    }

    forbidden_columns = {
        "raw_token",
        "token",
        "deep_link",
        "full_deep_link",
        "phone",
        "ip",
        "client_ip",
        "username",
        "first_name",
        "last_name",
        "profile",
        "message",
        "update",
        "update_json",
        "metadata",
        "telegram_chat_id",
    }
    assert forbidden_columns.isdisjoint(TelegramLinkToken.__table__.columns.keys())
    assert forbidden_columns.isdisjoint(TelegramLinkEvent.__table__.columns.keys())
    assert forbidden_columns.isdisjoint(AuthRateLimit.__table__.columns.keys())
    assert {"username", "profile", "update_json", "metadata"}.isdisjoint(
        TelegramLink.__table__.columns.keys()
    )


def test_m4_wrappers_logs_and_domain_errors_do_not_expose_raw_values(caplog) -> None:
    raw_token_value = "pytest_m4_70_log_token"
    chat_id = 981_702
    raw_ip = "203.0.113.171"
    bot_username = TelegramBotUsername("LeakAuditBot")
    raw_token = RawTelegramLinkToken(raw_token_value)
    start_link = build_telegram_start_link(bot_username, raw_token)
    client_ip = ResolvedClientIp(raw_ip)
    chat_identity = VerifiedPrivateTelegramChatIdentity(chat_id)
    full_deep_link = start_link.as_delivery_url()
    raw_db_details = (
        "IntegrityError SELECT * FROM telegram_link_tokens "
        "uq_telegram_links_active_chat_id token_hash "
        f"{raw_token_value} {chat_id} {raw_ip} {full_deep_link}"
    )
    public_body = get_public_error_body(
        ErrorCode.LINK_TOKEN_INVALID,
        internal_detail=raw_db_details,
    )
    errors = (
        TelegramLinkTokenConsumeError(),
        TelegramChatAlreadyLinkedError(),
        TelegramLinkTokenIssueError(ErrorCode.RATE_LIMITED, public_error=public_body),
        TelegramLinkTokenIssueInternalError("Telegram link token issue failed"),
        TelegramLinkLifecycleInternalError("Telegram link lifecycle transition failed"),
    )
    logger = logging.getLogger("tests.telegram_sensitive_data_leakage_audit")

    with caplog.at_level(logging.INFO):
        logger.info(
            "objects %s %r %s %r %s %r %s %r",
            raw_token,
            raw_token,
            start_link,
            start_link,
            client_ip,
            client_ip,
            chat_identity,
            chat_identity,
        )
        logger.info(
            "fstring objects %s",
            f"{raw_token} {start_link} {client_ip} {chat_identity}",
        )
        for error in errors:
            logger.info("domain error %s %r", error, error)

    error_text = " ".join(
        f"{error!r} {error} {getattr(error, 'public_error', {})}" for error in errors
    )
    invalid_input_messages: list[str] = []
    for invalid_factory in (
        lambda: RawTelegramLinkToken("raw token with spaces"),
        lambda: ResolvedClientIp(f"{raw_ip}:443"),
        lambda: VerifiedPrivateTelegramChatIdentity(0),
        lambda: TelegramBotUsername(full_deep_link),
    ):
        with pytest.raises(ValueError) as exc_info:
            invalid_factory()
        invalid_input_messages.append(str(exc_info.value))

    assert_values_absent(
        (
            raw_token_value,
            str(chat_id),
            raw_ip,
            full_deep_link,
            "IntegrityError",
            "SELECT *",
            "telegram_link_tokens",
            "uq_telegram_links_active_chat_id",
            "token_hash",
        ),
        caplog.text,
        error_text,
        " ".join(invalid_input_messages),
    )


def test_auth_customer_html_and_m4_sources_have_no_identifier_dump_paths() -> None:
    template_paths = tuple(
        sorted((PROJECT_ROOT / "app" / "templates" / "auth").glob("*.html"))
    ) + tuple(sorted((PROJECT_ROOT / "app" / "templates" / "customer").glob("*.html")))
    forbidden_template_snippets = (
        "telegram",
        "t.me",
        "?start=",
        "RawTelegramLinkToken",
        "ResolvedClientIp",
        "VerifiedPrivateTelegramChatIdentity",
        "__dict__",
        "vars(",
        "locals(",
        "globals(",
        "context_dump",
        "debug_context",
        "{% debug",
        "|tojson",
        "|safe",
        "pprint",
    )
    for path in template_paths:
        template_text = path.read_text(encoding="utf-8")
        for snippet in forbidden_template_snippets:
            assert snippet not in template_text

    persistence_paths = (
        PROJECT_ROOT / "app" / "telegram" / "models.py",
        PROJECT_ROOT / "app" / "telegram" / "repository.py",
        PROJECT_ROOT / "app" / "telegram" / "events.py",
        PROJECT_ROOT / "app" / "telegram" / "service.py",
    )
    forbidden_persistence_snippets = (
        "username",
        "first_name",
        "last_name",
        "profile",
        "update_json",
        "metadata",
        "message_json",
        "logging",
        "logger",
        "__dict__",
        "vars(",
        "locals(",
        "globals(",
        "context_dump",
        "debug_context",
        "pprint",
    )
    for path in persistence_paths:
        source_text = path.read_text(encoding="utf-8").casefold()
        for snippet in forbidden_persistence_snippets:
            assert snippet.casefold() not in source_text


def test_no_tracked_fixture_snapshot_or_log_files_hold_m4_like_secrets() -> None:
    ignored_parts = {".git", ".venv", ".pytest_cache", "__pycache__"}
    risky_files = []
    for path in PROJECT_ROOT.rglob("*"):
        if any(part in ignored_parts for part in path.parts) or not path.is_file():
            continue
        name = path.name.casefold()
        if (
            path.suffix.casefold() in {".log", ".snap"}
            or "fixture" in name
            or "snapshot" in name
        ):
            risky_files.append(path.relative_to(PROJECT_ROOT).as_posix())

    assert risky_files == []


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0
