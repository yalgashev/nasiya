from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.debt.customer_accept_service as accept_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.customer_accept_service import (
    AcceptCustomerDebtCommand,
    CustomerDebtAcceptOutcome,
    accept_own_customer_debt,
)
from app.debt.customer_authority import resolve_own_customer_debt_authority
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.policy import GlobalHardBlockProjection
from app.debt.values import DebtId, DebtRevision
from app.offers.enums import OfferLanguage, OfferPurpose
from app.offers.models import OfferAcceptance, OfferText, OfferVersion
from app.shop.enums import ShopStatus
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.models import ShopCustomer
from tests.test_customer_debt_read_postgresql import (
    NOW,
    _add_current_complete_debt_offer,
    _seed_owned_debt,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _offer_text_id(session: Session, language: OfferLanguage) -> UUID:
    value = session.scalar(
        select(OfferText.id)
        .join(OfferVersion, OfferVersion.id == OfferText.offer_version_id)
        .where(
            OfferText.language == language.value,
            OfferVersion.purpose == OfferPurpose.DEBT_ACCEPTANCE.value,
        )
    )
    assert value is not None
    return value


def _command(
    *,
    debt_id,
    offer_text_id,
    revision: int = 1,
    language: OfferLanguage = OfferLanguage.RU,
    now=NOW + timedelta(hours=1),
) -> AcceptCustomerDebtCommand:
    return AcceptCustomerDebtCommand(
        debt_id=DebtId(debt_id),
        expected_revision=DebtRevision(revision),
        language=language,
        displayed_offer_text_id=offer_text_id,
        now=now,
        raw_user_agent="  Browser\tM13\x00  ",
    )


def test_accept_writes_exact_atomic_snapshot_and_replay_is_a_noop(
    db_session: Session,
) -> None:
    seed = _seed_owned_debt(db_session)
    _add_current_complete_debt_offer(db_session, actor=seed.user)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    offer_text_id = _offer_text_id(db_session, OfferLanguage.RU)
    command = _command(debt_id=seed.debt.id, offer_text_id=offer_text_id)

    accepted = accept_own_customer_debt(
        db_session,
        authority=authority,
        command=command,
    )
    counts = (
        db_session.scalar(select(func.count()).select_from(OfferAcceptance)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )
    replay = accept_own_customer_debt(
        db_session,
        authority=authority,
        command=command,
    )

    assert accepted.outcome is CustomerDebtAcceptOutcome.ACCEPTED
    assert replay.outcome is CustomerDebtAcceptOutcome.REPLAY
    assert (
        counts
        == (
            db_session.scalar(select(func.count()).select_from(OfferAcceptance)),
            db_session.scalar(select(func.count()).select_from(AuditLog)),
        )
        == (1, 1)
    )
    db_session.refresh(seed.debt)
    assert seed.debt.status == DebtStatus.ACTIVE.value
    assert seed.debt.revision == 2
    assert seed.debt.accepted_at == command.now
    acceptance = db_session.scalar(select(OfferAcceptance))
    assert acceptance is not None
    assert acceptance.debt_id == seed.debt.id
    assert acceptance.user_id == seed.user.id
    assert acceptance.offer_text_id == offer_text_id
    assert acceptance.purpose == OfferPurpose.DEBT_ACCEPTANCE.value
    assert acceptance.language == OfferLanguage.RU.value
    assert acceptance.accepted_at == command.now
    assert acceptance.user_agent == "Browser M13"
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.DEBT_ACCEPTED.value
        )
    )
    assert audit is not None
    assert audit.payload == {
        "offer_version_number": 1,
        "language": OfferLanguage.RU.value,
        "content_hash": acceptance.content_hash,
    }
    assert "Browser M13" not in repr(accepted)
    assert str(seed.debt.id) not in repr(replay)


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        ("blacklist", ErrorCode.CUSTOMER_BLACKLISTED),
        ("hard_block", ErrorCode.CUSTOMER_RATING_BLOCKED),
        ("stale_offer", ErrorCode.OFFER_CHANGED),
        ("stale_revision", ErrorCode.DEBT_NOT_PENDING),
        ("expired", ErrorCode.DEBT_EXPIRED),
        ("shop_suspended", ErrorCode.SHOP_SUSPENDED),
        ("user_inactive", ErrorCode.DEBT_UNAVAILABLE),
        ("telegram_drift", ErrorCode.DEBT_UNAVAILABLE),
        ("customer_draft", ErrorCode.DEBT_UNAVAILABLE),
        ("incomplete_offer", ErrorCode.OFFER_UNAVAILABLE),
    ),
)
def test_accept_live_policy_offer_and_time_failures_are_zero_write(
    db_session: Session,
    failure: str,
    expected: ErrorCode,
) -> None:
    seed = _seed_owned_debt(db_session)
    languages = (
        (OfferLanguage.RU,) if failure == "incomplete_offer" else tuple(OfferLanguage)
    )
    _add_current_complete_debt_offer(
        db_session,
        actor=seed.user,
        languages=languages,
    )
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    offer_text_id = _offer_text_id(db_session, OfferLanguage.RU)
    revision = 2 if failure == "stale_revision" else 1
    now = (
        NOW + timedelta(hours=72) if failure == "expired" else NOW + timedelta(hours=1)
    )
    if failure == "blacklist":
        shop_customer = db_session.get(ShopCustomer, seed.debt.shop_customer_id)
        assert shop_customer is not None
        shop_customer.list_status = ShopCustomerListStatus.BLACKLISTED.value
    elif failure == "shop_suspended":
        seed.shop.status = ShopStatus.SUSPENDED.value
    elif failure == "user_inactive":
        seed.user.is_active = False
    elif failure == "telegram_drift":
        seed.link.phone_verified_at = None
    elif failure == "customer_draft":
        seed.customer.onboarding_status = "draft"
        seed.customer.activated_at = None
    db_session.flush()
    if failure == "stale_offer":
        offer_text_id = uuid4()

    class HardBlock:
        def read_global_hard_block(self, *, customer_id):
            return GlobalHardBlockProjection(is_blocked=failure == "hard_block")

    result = accept_own_customer_debt(
        db_session,
        authority=authority,
        command=_command(
            debt_id=seed.debt.id,
            offer_text_id=offer_text_id,
            revision=revision,
            now=now,
        ),
        global_hard_block_reader=HardBlock(),
    )

    assert result.error is expected
    assert db_session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
    expected_audits = 1 if failure == "expired" else 0
    assert (
        db_session.scalar(select(func.count()).select_from(AuditLog)) == expected_audits
    )
    db_session.refresh(seed.debt)
    if failure == "expired":
        assert seed.debt.status == DebtStatus.EXPIRED.value
        assert seed.debt.revision == 2
        assert seed.debt.expired_at == now
    else:
        assert seed.debt.status == DebtStatus.PENDING.value
        assert seed.debt.revision == 1


@pytest.mark.parametrize("fault", ("acceptance", "update", "audit"))
def test_acceptance_update_and_audit_faults_roll_back_the_whole_unit(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    seed = _seed_owned_debt(db_session)
    _add_current_complete_debt_offer(db_session, actor=seed.user)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    debt_id = seed.debt.id
    offer_text_id = _offer_text_id(db_session, OfferLanguage.RU)
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)

    def fail(*args, **kwargs):
        raise RuntimeError(f"{fault} fault")

    if fault == "acceptance":
        monkeypatch.setattr(accept_service, "append_locked_debt_acceptance", fail)
    elif fault == "update":
        monkeypatch.setattr(accept_service, "update_locked_debt", fail)
    else:
        monkeypatch.setattr(accept_service, "append_audit_event", fail)

    with pytest.raises(RuntimeError, match="fault"):
        with factory.begin() as session:
            accept_own_customer_debt(
                session,
                authority=authority,
                command=_command(
                    debt_id=debt_id,
                    offer_text_id=offer_text_id,
                ),
            )

    with factory.begin() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == DebtStatus.PENDING.value
        assert debt.revision == 1
        assert session.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_parallel_exact_accepts_converge_to_accept_and_replay(
    db_session: Session, m2_test_database: Engine
) -> None:
    seed = _seed_owned_debt(db_session)
    _add_current_complete_debt_offer(db_session, actor=seed.user)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    debt_id = seed.debt.id
    offer_text_id = _offer_text_id(db_session, OfferLanguage.RU)
    command = _command(debt_id=debt_id, offer_text_id=offer_text_id)
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    start = Event()

    def accept_once():
        assert start.wait(timeout=5)
        with factory.begin() as session:
            return accept_own_customer_debt(
                session,
                authority=authority,
                command=command,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(accept_once), executor.submit(accept_once))
        start.set()
        results = [future.result(timeout=10) for future in futures]

    assert {result.outcome for result in results} == {
        CustomerDebtAcceptOutcome.ACCEPTED,
        CustomerDebtAcceptOutcome.REPLAY,
    }
    with factory.begin() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None and debt.status == DebtStatus.ACTIVE.value
        assert debt.revision == 2
        assert session.scalar(select(func.count()).select_from(OfferAcceptance)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
