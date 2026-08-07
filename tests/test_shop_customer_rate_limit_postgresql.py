from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit
from app.settings import Settings
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import DetachedShopCustomerAuthority
from app.shop_customer.rate_limit import (
    SHOP_CUSTOMER_LINK_ACTOR_SCOPE,
    SHOP_CUSTOMER_LINK_IP_SCOPE,
    SHOP_CUSTOMER_LINK_PHONE_SCOPE,
    SHOP_CUSTOMER_LINK_SHOP_SCOPE,
    record_shop_customer_link_attempt,
)
from app.telegram.client_ip import ResolvedClientIp

NOW = datetime(2026, 8, 7, 18, 0, tzinfo=UTC)
RATE_KEY = "test-rate-limit-hmac-key-for-m12-shop-customer-link"


class TrackingSession(Session):
    created: list["TrackingSession"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.close_calls = 0
        self.__class__.created.append(self)

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def _settings(engine: Engine, **overrides) -> Settings:
    values = {
        "app_environment": "testing",
        "debug": False,
        "database_url": engine.url.render_as_string(hide_password=False),
        "session_cookie_secure": False,
        "rate_limit_hmac_key": RATE_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _authority() -> DetachedShopCustomerAuthority:
    return DetachedShopCustomerAuthority(
        actor_user_id=UserId(uuid4()),
        current_shop_id=ShopId(uuid4()),
    )


def test_settings_have_exact_frozen_defaults_and_fail_closed_validation(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    assert settings.shop_customer_link_rate_limit_window_seconds == 900
    assert settings.shop_customer_link_rate_limit_actor_attempts == 30
    assert settings.shop_customer_link_rate_limit_shop_attempts == 100
    assert settings.shop_customer_link_rate_limit_phone_attempts == 5
    assert settings.shop_customer_link_rate_limit_ip_attempts == 200

    for field_name in (
        "shop_customer_link_rate_limit_window_seconds",
        "shop_customer_link_rate_limit_actor_attempts",
        "shop_customer_link_rate_limit_shop_attempts",
        "shop_customer_link_rate_limit_phone_attempts",
        "shop_customer_link_rate_limit_ip_attempts",
    ):
        with pytest.raises(ValidationError):
            _settings(m2_test_database, **{field_name: 0})
        with pytest.raises(ValidationError):
            _settings(m2_test_database, **{field_name: -1})


@pytest.mark.integration
def test_closed_tx_b_records_exact_four_hmac_only_buckets(
    m2_test_database: Engine,
) -> None:
    TrackingSession.created = []
    factory = sessionmaker(bind=m2_test_database, class_=TrackingSession)
    authority = _authority()
    submitted_phone = "+998900008888"
    ip_value = "203.0.113.88"

    result = record_shop_customer_link_attempt(
        factory,
        settings=_settings(m2_test_database),
        authority=authority,
        submitted_phone=submitted_phone,
        client_ip=ResolvedClientIp(ip_value),
        now=NOW,
    )

    assert result.allowed is True
    assert result.error_code is None
    assert TrackingSession.created
    assert all(item.close_calls == 1 for item in TrackingSession.created)
    assert all(not item.in_transaction() for item in TrackingSession.created)
    with factory() as verification:
        rows = tuple(
            verification.scalars(select(AuthRateLimit).order_by(AuthRateLimit.scope))
        )
    assert {row.scope for row in rows} == {
        SHOP_CUSTOMER_LINK_ACTOR_SCOPE,
        SHOP_CUSTOMER_LINK_SHOP_SCOPE,
        SHOP_CUSTOMER_LINK_PHONE_SCOPE,
        SHOP_CUSTOMER_LINK_IP_SCOPE,
    }
    assert all(row.attempt_count == 1 for row in rows)
    assert all(len(row.key_hash) == 64 for row in rows)
    persisted = repr([(row.scope, row.key_hash) for row in rows])
    assert submitted_phone not in persisted
    assert ip_value not in persisted
    assert str(authority.actor_user_id) not in persisted
    assert str(authority.current_shop_id) not in persisted
    assert submitted_phone not in repr(result)


@pytest.mark.integration
def test_configured_attempt_count_is_allowed_and_next_attempt_blocks_all_buckets(
    m2_test_database: Engine,
) -> None:
    factory = sessionmaker(bind=m2_test_database, class_=Session)
    settings = _settings(
        m2_test_database,
        shop_customer_link_rate_limit_phone_attempts=2,
    )
    authority = _authority()
    inputs = {
        "settings": settings,
        "authority": authority,
        "submitted_phone": "+998900007777",
        "client_ip": ResolvedClientIp("203.0.113.77"),
        "now": NOW,
    }

    first = record_shop_customer_link_attempt(factory, **inputs)
    second = record_shop_customer_link_attempt(factory, **inputs)
    third = record_shop_customer_link_attempt(factory, **inputs)

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.error_code is ErrorCode.RATE_LIMITED
    with factory() as verification:
        rows = tuple(verification.scalars(select(AuthRateLimit)))
    assert len(rows) == 4
    assert all(row.attempt_count == 3 for row in rows)


@pytest.mark.integration
def test_invalid_phone_text_still_records_actor_shop_phone_and_ip(
    m2_test_database: Engine,
) -> None:
    factory = sessionmaker(bind=m2_test_database, class_=Session)

    result = record_shop_customer_link_attempt(
        factory,
        settings=_settings(m2_test_database),
        authority=_authority(),
        submitted_phone="invalid transient form value",
        client_ip=ResolvedClientIp("203.0.113.66"),
        now=NOW,
    )

    assert result.allowed is True
    with factory() as verification:
        rows = tuple(verification.scalars(select(AuthRateLimit)))
    assert len(rows) == 4
    assert all("invalid transient" not in row.key_hash for row in rows)


@pytest.mark.integration
def test_concurrent_attempts_have_no_lost_hmac_bucket_updates(
    m2_test_database: Engine,
) -> None:
    workers = 8
    barrier = Barrier(workers)
    factory = sessionmaker(bind=m2_test_database, class_=Session)
    settings = _settings(
        m2_test_database,
        shop_customer_link_rate_limit_actor_attempts=100,
        shop_customer_link_rate_limit_shop_attempts=100,
        shop_customer_link_rate_limit_phone_attempts=100,
        shop_customer_link_rate_limit_ip_attempts=100,
    )
    authority = _authority()

    def attempt():
        barrier.wait()
        return record_shop_customer_link_attempt(
            factory,
            settings=settings,
            authority=authority,
            submitted_phone="+998900006666",
            client_ip=ResolvedClientIp("203.0.113.55"),
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = tuple(executor.map(lambda _index: attempt(), range(workers)))

    assert all(result.allowed for result in results)
    with factory() as verification:
        rows = tuple(verification.scalars(select(AuthRateLimit)))
    assert len(rows) == 4
    assert all(row.attempt_count == workers for row in rows)


def test_rate_wrapper_has_no_success_clear_or_domain_transaction() -> None:
    source = Path("app/shop_customer/rate_limit.py").read_text(encoding="utf-8")
    assert "clear_key" not in source
    assert "from app.shop_customer.models" not in source
    assert "from app.telegram.models" not in source
    assert "from app.customer.models" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
