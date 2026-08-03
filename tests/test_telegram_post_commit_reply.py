import asyncio
import json
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine

import app.telegram.update_processing as processing_module
from app.auth.models import User
from app.db import Base, create_database_session_factory
from app.settings import Settings
from app.telegram.bot_api import (
    TelegramFixedReplyMarkup,
    TelegramMessageEnvelope,
    TelegramUpdateEnvelope,
)
from app.telegram.bot_reply import render_bot_reply_markup
from app.telegram.inbound import SensitiveTelegramContactPhone, TelegramUserIdentity
from app.telegram.models import (
    TelegramLink,
    TelegramLinkEvent,
    TelegramLinkToken,
    TelegramUpdateFailure,
)
from app.telegram.polling_repository import (
    get_next_offset,
    load_or_create_polling_state,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token
from app.telegram.update_processing import (
    BotReplyIntent,
    BotReplyKey,
    TelegramReplyLanguage,
    TelegramTxBResult,
    TelegramUpdateProcessor,
    process_telegram_update_tx_a,
)
from app.telegram.worker import (
    PollingUpdateOutcome,
    ShutdownController,
    run_worker,
)

NOW = datetime(2026, 7, 28, 6, 0, tzinfo=UTC)
RAW_TOKEN = "post_commit_reply_token"
CHAT_ID = 881122
CONTACT_BINDING_HMAC_KEY = SecretStr(
    "reply-test-contact-binding-key-at-least-32-characters"
)


def run(coroutine: Awaitable[object]):
    return asyncio.run(coroutine)


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=SecretStr("reply-test-hmac-key-at-least-32-characters"),
        telegram_bot_username="Nasiya_LinkBot",
        telegram_bot_token=SecretStr("123456789:ReplyBoundarySecret"),
    )


def seed_token(engine: Engine, *, issued_at: datetime | None = None) -> None:
    token_issued_at = issued_at or datetime.now(UTC)
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        user = User(phone="+998900081001")
        session.add(user)
        session.flush()
        session.add(
            TelegramLinkToken(
                user_id=user.id,
                token_hash=hash_telegram_link_token(RawTelegramLinkToken(RAW_TOKEN)),
                created_at=token_issued_at,
                expires_at=token_issued_at + timedelta(minutes=10),
            )
        )


@pytest.mark.integration
def test_worker_sends_contact_request_only_after_pending_binding_commit(
    m2_test_database: Engine,
) -> None:
    seed_token(m2_test_database)
    shutdown = ShutdownController()
    methods = []
    sent_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent_payload
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "getMe":
            result = {
                "id": 123456789,
                "is_bot": True,
                "username": "Nasiya_LinkBot",
            }
        elif method == "getWebhookInfo":
            result = {"url": ""}
        elif method == "getUpdates":
            result = [
                {
                    "update_id": 101,
                    "message": {
                        "from": {"id": CHAT_ID, "language_code": "ru-RU"},
                        "chat": {"id": CHAT_ID, "type": "private"},
                        "text": f"/start {RAW_TOKEN}",
                    },
                }
            ]
        else:
            session_factory = create_database_session_factory(m2_test_database)
            with session_factory() as session:
                assert get_next_offset(session) == 102
                assert (
                    session.scalar(select(func.count()).select_from(TelegramLink)) == 0
                )
                assert (
                    session.scalar(select(func.count()).select_from(TelegramLinkEvent))
                    == 0
                )
                token = session.scalar(select(TelegramLinkToken))
                assert token is not None
                assert token.pending_contact_binding_mac is not None
                assert token.contact_requested_at is not None
                assert token.consumed_at is None
            sent_payload = json.loads(request.content)
            shutdown.request()
            result = {"message_id": 501}
        return httpx.Response(200, json={"ok": True, "result": result})

    run(
        run_worker(
            make_settings(m2_test_database),
            shutdown=shutdown,
            transport=httpx.MockTransport(handler),
            processor=None,
        )
    )

    assert methods == ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"]
    assert sent_payload["chat_id"] == CHAT_ID
    assert "контакт" in sent_payload["text"]
    assert sent_payload["reply_markup"] == {
        "keyboard": [
            [{"text": "Отправить мой контакт Telegram", "request_contact": True}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
    assert RAW_TOKEN not in sent_payload["text"]


@pytest.mark.integration
def test_contact_request_reply_sends_without_open_session(
    m2_test_database: Engine,
) -> None:
    seed_token(m2_test_database, issued_at=NOW)
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
    delivered: list[BotReplyIntent] = []

    async def delivery(intent: BotReplyIntent | None) -> None:
        assert intent is not None
        assert m2_test_database.pool.checkedout() == 0  # type: ignore[attr-defined]
        with session_factory() as observer:
            assert get_next_offset(observer) == 142
            token = observer.scalar(select(TelegramLinkToken))
            assert token is not None
            assert token.pending_contact_binding_mac is not None
            assert token.contact_requested_at == NOW
            assert token.consumed_at is None
            assert observer.scalar(select(func.count()).select_from(TelegramLink)) == 0
            assert (
                observer.scalar(select(func.count()).select_from(TelegramLinkEvent))
                == 0
            )
        delivered.append(intent)

    processor = TelegramUpdateProcessor(
        session_factory,
        rate_limit_hmac_key=CONTACT_BINDING_HMAC_KEY,
        now_factory=lambda: NOW,
        reply_delivery=delivery,
    )
    result = run(
        processor(
            TelegramUpdateEnvelope(
                update_id=141,
                message=TelegramMessageEnvelope(
                    chat_id=CHAT_ID,
                    chat_type="private",
                    text=f"/start {RAW_TOKEN}",
                    structurally_valid=True,
                    sender_identity=TelegramUserIdentity(CHAT_ID),
                ),
            )
        )
    )

    assert result is PollingUpdateOutcome.TERMINAL
    assert len(delivered) == 1
    assert delivered[0].reply_key is BotReplyKey.CONTACT_REQUIRED


@pytest.mark.integration
def test_contact_request_and_success_replies_send_without_open_session(
    m2_test_database: Engine,
) -> None:
    seed_token(m2_test_database, issued_at=NOW)
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
    delivered: list[BotReplyIntent] = []

    async def delivery(intent: BotReplyIntent | None) -> None:
        assert intent is not None
        assert m2_test_database.pool.checkedout() == 0  # type: ignore[attr-defined]
        with session_factory() as observer:
            token = observer.scalar(select(TelegramLinkToken))
            assert token is not None
            if intent.reply_key is BotReplyKey.CONTACT_REQUIRED:
                assert get_next_offset(observer) == 152
                assert token.pending_contact_binding_mac is not None
                assert token.contact_requested_at == NOW
                assert token.consumed_at is None
                assert (
                    observer.scalar(select(func.count()).select_from(TelegramLink)) == 0
                )
            else:
                assert intent.reply_key is BotReplyKey.CONTACT_VERIFIED
                assert (
                    render_bot_reply_markup(intent)
                    is TelegramFixedReplyMarkup.REMOVE_KEYBOARD
                )
                assert get_next_offset(observer) == 153
                link = observer.scalar(select(TelegramLink))
                assert token.consumed_at == NOW + timedelta(seconds=1)
                assert token.pending_contact_binding_mac is None
                assert token.contact_requested_at is None
                assert link is not None
                assert link.phone_verified_at == link.linked_at
                assert link.phone_verified_at == NOW + timedelta(seconds=1)
                assert (
                    observer.scalar(select(func.count()).select_from(TelegramLinkEvent))
                    == 1
                )
        delivered.append(intent)

    now_values = iter((NOW, NOW + timedelta(seconds=1)))
    processor = TelegramUpdateProcessor(
        session_factory,
        rate_limit_hmac_key=CONTACT_BINDING_HMAC_KEY,
        now_factory=lambda: next(now_values),
        reply_delivery=delivery,
    )
    start_result = run(
        processor(
            TelegramUpdateEnvelope(
                update_id=151,
                message=TelegramMessageEnvelope(
                    chat_id=CHAT_ID,
                    chat_type="private",
                    text=f"/start {RAW_TOKEN}",
                    structurally_valid=True,
                    sender_identity=TelegramUserIdentity(CHAT_ID),
                ),
            )
        )
    )
    assert start_result is PollingUpdateOutcome.TERMINAL
    assert tuple(intent.reply_key for intent in delivered) == (
        BotReplyKey.CONTACT_REQUIRED,
    )

    result = run(
        processor(
            TelegramUpdateEnvelope(
                update_id=152,
                message=TelegramMessageEnvelope(
                    chat_id=CHAT_ID,
                    chat_type="private",
                    text=None,
                    structurally_valid=True,
                    sender_identity=TelegramUserIdentity(CHAT_ID),
                    contact_present=True,
                    contact_identity=TelegramUserIdentity(CHAT_ID),
                    contact_phone=SensitiveTelegramContactPhone("+998900081001"),
                ),
            )
        )
    )

    assert result is PollingUpdateOutcome.TERMINAL
    assert tuple(intent.reply_key for intent in delivered) == (
        BotReplyKey.CONTACT_REQUIRED,
        BotReplyKey.CONTACT_VERIFIED,
    )


@pytest.mark.integration
def test_reply_failure_cannot_rollback_or_poison_committed_tx_a(
    m2_test_database: Engine,
) -> None:
    shutdown = ShutdownController()
    get_updates_calls = 0
    send_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_updates_calls, send_calls
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            result = {
                "id": 123456789,
                "is_bot": True,
                "username": "Nasiya_LinkBot",
            }
        elif method == "getWebhookInfo":
            result = {"url": ""}
        elif method == "getUpdates":
            get_updates_calls += 1
            if get_updates_calls == 1:
                result = [
                    {
                        "update_id": 111,
                        "message": {
                            "from": {"id": CHAT_ID},
                            "chat": {"id": CHAT_ID, "type": "private"},
                            "text": "/start unknown_token",
                        },
                    }
                ]
            else:
                shutdown.request()
                result = []
        else:
            send_calls += 1
            raise httpx.ReadTimeout("unknown delivery state", request=request)
        return httpx.Response(200, json={"ok": True, "result": result})

    run(
        run_worker(
            make_settings(m2_test_database),
            shutdown=shutdown,
            transport=httpx.MockTransport(handler),
            processor=None,
        )
    )

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        assert get_next_offset(session) == 112
        assert session.get(TelegramUpdateFailure, 111) is None
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 0
    assert send_calls == 1


@pytest.mark.integration
def test_commit_or_tx_a_failure_never_invokes_reply_delivery(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    deliveries: list[BotReplyIntent | None] = []

    def fail_tx_a(*args, **kwargs):
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(
        processing_module,
        "process_telegram_update_tx_a",
        fail_tx_a,
    )
    monkeypatch.setattr(
        processing_module,
        "record_poison_failure_tx_b",
        lambda *args, **kwargs: TelegramTxBResult(
            attempt_count=1,
            quarantined=False,
        ),
    )

    async def delivery(intent: BotReplyIntent | None) -> None:
        deliveries.append(intent)

    async def sleeper(_seconds: float) -> bool:
        return False

    processor = TelegramUpdateProcessor(
        session_factory,
        rate_limit_hmac_key=CONTACT_BINDING_HMAC_KEY,
        now_factory=lambda: NOW,
        sleeper=sleeper,
        reply_delivery=delivery,
    )
    result = run(processor(processing_module.TelegramUpdateEnvelope(update_id=121)))

    assert result is PollingUpdateOutcome.RETRY
    assert deliveries == []


@pytest.mark.integration
def test_bot_reply_intent_materializes_only_after_database_commit(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
    call_order = []
    real_intent_type = processing_module.BotReplyIntent

    def observe_commit(_connection) -> None:
        call_order.append("commit")

    def observe_intent(*args, **kwargs):
        call_order.append("intent")
        return real_intent_type(*args, **kwargs)

    event.listen(m2_test_database, "commit", observe_commit)
    monkeypatch.setattr(processing_module, "BotReplyIntent", observe_intent)
    try:
        result = process_telegram_update_tx_a(
            session_factory,
            update=TelegramUpdateEnvelope(
                update_id=131,
                message=TelegramMessageEnvelope(
                    chat_id=CHAT_ID,
                    chat_type="private",
                    text="/start unknown_token",
                    structurally_valid=True,
                    language_code="uz",
                    sender_identity=TelegramUserIdentity(CHAT_ID),
                ),
            ),
            rate_limit_hmac_key=CONTACT_BINDING_HMAC_KEY,
            now=NOW,
        )
    finally:
        event.remove(m2_test_database, "commit", observe_commit)

    assert call_order == ["commit", "intent"]
    assert result.reply_intent is not None
    assert result.reply_intent.reply_key is BotReplyKey.LINK_FAILED
    assert result.reply_intent.language is TelegramReplyLanguage.UZ_LATN


def test_no_generic_reply_delivery_persistence_is_registered() -> None:
    forbidden_markers = ("outbox", "notification", "delivery", "bot_reply")

    assert not any(
        any(marker in table_name for marker in forbidden_markers)
        for table_name in Base.metadata.tables
    )


def test_scope_contract_declares_web_status_canonical_after_reply_crash() -> None:
    project_root = Path(__file__).resolve().parents[1]
    contract = (
        (project_root / "docs" / "m6_scope_contract.md")
        .read_text(encoding="utf-8")
        .casefold()
    )

    assert "web status is canonical" in contract
    assert "post-commit reply" in contract
