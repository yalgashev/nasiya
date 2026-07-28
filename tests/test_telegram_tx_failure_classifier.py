import asyncio

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.telegram.update_processing import (
    TelegramFailureClass,
    classify_telegram_tx_failure,
)


class SqlstateError(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        super().__init__("sensitive database detail")


@pytest.mark.parametrize(
    "sqlstate",
    [
        "40001",
        "40P01",
        "55P03",
        "08006",
        "57P01",
        "57P02",
        "57P03",
        "53300",
    ],
)
def test_exact_sqlstate_allowlist_is_transient(sqlstate: str) -> None:
    assert classify_telegram_tx_failure(SqlstateError(sqlstate)) is (
        TelegramFailureClass.TRANSIENT
    )


def test_statement_timeout_is_context_sensitive() -> None:
    error = SqlstateError("57014")

    assert classify_telegram_tx_failure(error) is TelegramFailureClass.POISON
    assert (
        classify_telegram_tx_failure(
            error,
            known_statement_timeout=True,
        )
        is TelegramFailureClass.TRANSIENT
    )


def test_pool_timeout_and_connection_invalidation_are_transient() -> None:
    pool_timeout = SQLAlchemyTimeoutError("pool detail")
    invalidated = DBAPIError(
        "statement",
        {},
        SqlstateError("99999"),
        connection_invalidated=True,
    )

    assert classify_telegram_tx_failure(pool_timeout) is (
        TelegramFailureClass.TRANSIENT
    )
    assert classify_telegram_tx_failure(invalidated) is (TelegramFailureClass.TRANSIENT)
    ordinary_dbapi_error = DBAPIError(
        "statement",
        {},
        SqlstateError("99999"),
        connection_invalidated=False,
    )
    assert classify_telegram_tx_failure(ordinary_dbapi_error) is (
        TelegramFailureClass.POISON
    )


def test_wrapped_sqlstate_is_found_but_unknown_defaults_to_poison() -> None:
    try:
        raise SqlstateError("40P01")
    except SqlstateError:
        try:
            raise RuntimeError("safe wrapper") from None
        except RuntimeError as wrapped:
            assert classify_telegram_tx_failure(wrapped) is (
                TelegramFailureClass.TRANSIENT
            )

    assert classify_telegram_tx_failure(RuntimeError("unknown")) is (
        TelegramFailureClass.POISON
    )
    assert classify_telegram_tx_failure(SqlstateError("23505")) is (
        TelegramFailureClass.POISON
    )


def test_controlled_cancellation_is_outside_abc_policy() -> None:
    assert classify_telegram_tx_failure(asyncio.CancelledError()) is (
        TelegramFailureClass.CONTROLLED_CANCELLATION
    )
    assert (
        classify_telegram_tx_failure(
            RuntimeError("shutdown"),
            controlled_cancellation=True,
        )
        is TelegramFailureClass.CONTROLLED_CANCELLATION
    )
