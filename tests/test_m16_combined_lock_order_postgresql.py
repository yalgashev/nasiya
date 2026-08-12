from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

import app.debt.targeting as debt_targeting
import app.rating.disclosure_service as disclosure_service
from app.audit.models import AuditLog
from app.auth.deps import CurrentSessionStatus
from app.auth.models import User
from app.db import create_database_session_factory
from app.debt.dependencies import DebtRequestContext, DetachedDebtActorAuthority
from app.debt.models import Debt
from app.debt.service import create_pending_debt_proposal
from app.debt.values import ShopCustomerId
from app.rating.enums import RiskBand
from app.rating.models import DisclosureViewLog, RatingEvent
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from tests.test_debt_creation_gates_postgresql import (
    _add_complete_offer,
    _create_command,
    _seed_target,
)
from tests.test_m16_disclosure_services_postgresql import Seed, _actor, _command

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 12, 8, tzinfo=UTC)


def test_cross_shop_disclosure_and_create_serialize_at_customer_forward_lock(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disclosure is complete before a cross-Shop M13 create may continue."""

    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000", max_open_debts=3)
        _add_complete_offer(session, actor=seed.actor)
        other_actor = User(phone=f"+998{uuid4().int % 1_000_000_000:09d}")
        other_shop = Shop(
            name="M16 cross-Shop forward barrier",
            phone=f"+998{uuid4().int % 1_000_000_000:09d}",
            status=ShopStatus.ACTIVE.value,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add_all((other_actor, other_shop))
        session.flush()
        session.add(
            ShopStaff(
                shop_id=other_shop.id,
                user_id=other_actor.id,
                role=ShopRole.OWNER.value,
                is_active=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        other_relation = ShopCustomer(
            shop_id=other_shop.id,
            customer_id=seed.customer.id,
            credit_limit_uzs=Decimal("1000"),
            max_open_debts=3,
            list_status="normal",
            revision=1,
            created_by_user_id=other_actor.id,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(other_relation)
        session.flush()
        disclosure_seed = Seed(
            seed.actor.id,
            seed.shop.id,
            seed.shop_customer.id,
        )
        create_authority = DetachedDebtActorAuthority(
            status=CurrentSessionStatus.AUTHENTICATED,
            actor_user_id=other_actor.id,
            current_shop_id=other_shop.id,
            request_context=DebtRequestContext(is_htmx=False),
        )
        relation_id = other_relation.id

    disclosure_staged = Barrier(2)
    create_attempted_customer = Barrier(2)
    release_disclosure = Barrier(2)
    original_insert = disclosure_service.insert_disclosure_view_locked
    original_customer_lock = debt_targeting.lock_active_customer_for_target_user

    def pause_disclosure(*args, **kwargs):
        result = original_insert(*args, **kwargs)
        disclosure_staged.wait()
        release_disclosure.wait()
        return result

    def observe_create_customer_lock(*args, **kwargs):
        create_attempted_customer.wait()
        return original_customer_lock(*args, **kwargs)

    monkeypatch.setattr(
        disclosure_service,
        "insert_disclosure_view_locked",
        pause_disclosure,
    )
    monkeypatch.setattr(
        debt_targeting,
        "lock_active_customer_for_target_user",
        observe_create_customer_lock,
    )

    def disclose():
        with factory.begin() as session:
            return disclosure_service.record_risk_band_disclosure(
                session,
                actor=_actor(disclosure_seed),
                command=_command(disclosure_seed),
                disclosure_clock=lambda: NOW,
            )

    def create():
        with factory.begin() as session:
            return create_pending_debt_proposal(
                session,
                authority=create_authority,
                shop_customer_id=ShopCustomerId(relation_id),
                command=_create_command(amount="100"),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        disclosure_future = executor.submit(disclose)
        disclosure_staged.wait()
        create_future = executor.submit(create)
        create_attempted_customer.wait()
        assert not create_future.done()
        release_disclosure.wait()
        disclosed = disclosure_future.result()
        created = create_future.result()

    assert created.error is None and created.debt_id is not None
    with factory() as session:
        snapshot = session.get_one(
            DisclosureViewLog,
            disclosed.disclosure_view_id.as_uuid(),
        )
        assert snapshot.band == RiskBand.NEW.value
        assert session.scalar(select(func.count()).select_from(Debt)) == 1
        assert session.scalar(select(func.count()).select_from(RatingEvent)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2
