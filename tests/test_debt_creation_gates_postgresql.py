from __future__ import annotations

import inspect
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.debt.service as debt_service
from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.deps import CurrentSessionStatus
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.db import create_database_session_factory
from app.debt.commands import CreateDebtRawForm, assemble_create_debt_command
from app.debt.creation_eligibility import evaluate_locked_debt_creation
from app.debt.dependencies import DebtRequestContext, DetachedDebtActorAuthority
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.offer_gate import lock_current_complete_debt_offer
from app.debt.policy import DebtCreationEligibilityDecision, GlobalHardBlockProjection
from app.debt.service import create_pending_debt_proposal
from app.debt.targeting import (
    discover_tenant_debt_target,
    lock_debt_target_before_offer,
    lock_debt_target_shop_customer_after_offer,
)
from app.debt.values import OriginalAmountUZS
from app.idempotency.contracts import IdempotencyOutcome
from app.idempotency.models import IdempotencyKey
from app.offers.content import canonicalize_offer_text, compute_offer_content_hash
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance, OfferText, OfferVersion
from app.offers.repository import SqlAlchemyCurrentOfferResolver
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import ShopCustomerId
from app.telegram.models import TelegramLink

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 8, 7, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    factory = create_database_session_factory(m2_test_database)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@dataclass(frozen=True)
class Seed:
    authority: DetachedDebtActorAuthority
    actor: User
    target: User
    shop: Shop
    customer: Customer
    link: TelegramLink
    shop_customer: ShopCustomer


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _seed_target(
    session: Session,
    *,
    list_status: ShopCustomerListStatus = ShopCustomerListStatus.NORMAL,
    credit_limit: str = "1000",
    max_open_debts: int = 3,
    role: ShopRole = ShopRole.OWNER,
) -> Seed:
    actor = User(phone=_phone(), is_active=True)
    target = User(phone=_phone(), is_active=True)
    session.add_all((actor, target))
    session.flush()
    shop = Shop(
        name="M13 target shop",
        phone=_phone(),
        status=ShopStatus.ACTIVE.value,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(shop)
    session.flush()
    session.add(
        ShopStaff(
            shop_id=shop.id,
            user_id=actor.id,
            role=role.value,
            is_active=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    customer = Customer(
        user_id=target.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        activated_at=NOW,
    )
    link = TelegramLink(
        user_id=target.id,
        telegram_chat_id=uuid4().int % 2_000_000_000,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    session.add_all((customer, link))
    session.flush()
    shop_customer = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal(credit_limit),
        max_open_debts=max_open_debts,
        list_status=list_status.value,
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(shop_customer)
    session.flush()
    return Seed(
        authority=DetachedDebtActorAuthority(
            status=CurrentSessionStatus.AUTHENTICATED,
            actor_user_id=actor.id,
            current_shop_id=shop.id,
            request_context=DebtRequestContext(is_htmx=False),
        ),
        actor=actor,
        target=target,
        shop=shop,
        customer=customer,
        link=link,
        shop_customer=shop_customer,
    )


def _create_command(
    *,
    key: str | None = None,
    amount: str = "100",
    discount: str = "0",
    due_date_value: str = "2026-08-12",
):
    canonical_key = key or str(uuid4())
    return assemble_create_debt_command(
        form=CreateDebtRawForm(
            original_amount_uzs=amount,
            discount_percent=discount,
            due_date=due_date_value,
            idempotency_key=canonical_key,
        ),
        header_idempotency_key=None,
        now=NOW,
    )


def _add_complete_offer(
    session: Session,
    *,
    actor: User,
    version_number: int = 1,
    status: OfferStatus = OfferStatus.CURRENT,
    languages: tuple[OfferLanguage, ...] = tuple(OfferLanguage),
) -> OfferVersion:
    version = OfferVersion(
        purpose=OfferPurpose.DEBT_ACCEPTANCE.value,
        version_number=version_number,
        status=status.value,
        created_by_user_id=actor.id,
        created_at=NOW - timedelta(hours=3),
        legal_review_authority="M13 legal",
        legal_reviewed_at=NOW - timedelta(hours=2),
        legal_review_reference="M13-LEGAL-1",
        approved_by_user_id=actor.id,
        approved_at=NOW - timedelta(hours=1),
        current_by_user_id=actor.id if status is OfferStatus.CURRENT else None,
        current_at=NOW if status is OfferStatus.CURRENT else None,
    )
    session.add(version)
    session.flush()
    for language in languages:
        canonical = canonicalize_offer_text(
            title=f"Debt {language.value}", body=f"Terms {language.value}"
        )
        session.add(
            OfferText(
                offer_version_id=version.id,
                language=language.value,
                title=canonical.title,
                body=canonical.body,
                content_hash=compute_offer_content_hash(canonical),
                created_at=NOW,
                updated_at=NOW,
            )
        )
    session.flush()
    return version


def _lock_complete_target(session: Session, seed: Seed):
    return _lock_complete_target_by_locator(
        session,
        authority=seed.authority,
        shop_customer_id=seed.shop_customer.id,
    )


def _lock_complete_target_by_locator(
    session: Session,
    *,
    authority: DetachedDebtActorAuthority,
    shop_customer_id,
):
    candidate = discover_tenant_debt_target(
        session,
        authority=authority,
        shop_customer_id=ShopCustomerId(shop_customer_id),
    )
    resolution = lock_debt_target_before_offer(
        session, authority=authority, candidate=candidate
    )
    assert resolution.error is None
    assert resolution.locked_before_offer is not None
    offer = lock_current_complete_debt_offer(
        session, locked_target=resolution.locked_before_offer
    )
    assert offer.error is None
    locked = lock_debt_target_shop_customer_after_offer(
        session,
        locked_before_offer=resolution.locked_before_offer,
        locked_offer=offer.locked_offer,
    )
    assert locked is not None
    return locked


def _add_debt(
    session: Session,
    *,
    seed: Seed,
    amount: str,
    status: DebtStatus,
) -> None:
    session.add(
        Debt(
            shop_customer_id=seed.shop_customer.id,
            created_by_user_id=seed.actor.id,
            original_amount_uzs=Decimal(amount),
            discount_basis_points=0,
            discounted_amount_uzs=Decimal(amount),
            due_date=date(2026, 8, 20),
            pending_expires_at=NOW + timedelta(hours=72),
            status=status.value,
            revision=1,
            accepted_at=NOW if status is DebtStatus.ACTIVE else None,
            rejected_at=NOW if status is DebtStatus.REJECTED else None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()


def test_target_discovery_is_tenant_scoped_and_outcomes_are_redacted(
    db_session: Session,
) -> None:
    seed = _seed_target(db_session)
    other = _seed_target(db_session)

    cross_tenant = discover_tenant_debt_target(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(other.shop_customer.id),
    )
    missing = lock_debt_target_before_offer(
        db_session, authority=seed.authority, candidate=cross_tenant
    )

    assert cross_tenant is None
    assert missing.error is ErrorCode.SHOP_CUSTOMER_UNAVAILABLE
    rendered = repr(missing)
    assert str(seed.target.id) not in rendered
    assert str(seed.shop_customer.id) not in rendered
    assert (
        "decrypt"
        not in inspect.getsource(
            __import__("app.debt.targeting", fromlist=["*"])
        ).casefold()
    )


@pytest.mark.parametrize("invalid_state", ["target", "telegram", "customer"])
def test_live_target_chain_fails_closed_with_one_generic_unavailable_code(
    db_session: Session, invalid_state: str
) -> None:
    seed = _seed_target(db_session)
    if invalid_state == "target":
        seed.target.is_active = False
    elif invalid_state == "telegram":
        seed.link.phone_verified_at = None
    else:
        seed.customer.onboarding_status = "draft"
        seed.customer.activated_at = None
    db_session.flush()
    candidate = discover_tenant_debt_target(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
    )

    result = lock_debt_target_before_offer(
        db_session, authority=seed.authority, candidate=candidate
    )

    assert result.error is ErrorCode.SHOP_CUSTOMER_UNAVAILABLE
    assert result.locked_before_offer is None


def test_offer_gate_requires_exact_current_complete_debt_offer(
    db_session: Session,
) -> None:
    seed = _seed_target(db_session)
    candidate = discover_tenant_debt_target(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
    )
    target = lock_debt_target_before_offer(
        db_session, authority=seed.authority, candidate=candidate
    )
    assert target.locked_before_offer is not None

    zero_side_effects = (
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(OfferAcceptance)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )
    missing = lock_current_complete_debt_offer(
        db_session, locked_target=target.locked_before_offer
    )
    assert missing.error is ErrorCode.OFFER_UNAVAILABLE
    draft = OfferVersion(
        purpose=OfferPurpose.DEBT_ACCEPTANCE.value,
        version_number=1,
        status=OfferStatus.DRAFT.value,
        created_by_user_id=seed.actor.id,
        created_at=NOW,
    )
    db_session.add(draft)
    db_session.flush()
    unavailable_draft = lock_current_complete_debt_offer(
        db_session, locked_target=target.locked_before_offer
    )
    assert unavailable_draft.error is ErrorCode.OFFER_UNAVAILABLE
    current = _add_complete_offer(
        db_session,
        actor=seed.actor,
        version_number=2,
        languages=(OfferLanguage.UZ_LATN,),
    )
    incomplete = lock_current_complete_debt_offer(
        db_session, locked_target=target.locked_before_offer
    )
    assert incomplete.error is ErrorCode.OFFER_UNAVAILABLE
    for language in (OfferLanguage.UZ_CYRL, OfferLanguage.RU):
        canonical = canonicalize_offer_text(
            title=f"Debt {language.value}", body=f"Terms {language.value}"
        )
        db_session.add(
            OfferText(
                offer_version_id=current.id,
                language=language.value,
                title=canonical.title,
                body=canonical.body,
                content_hash=compute_offer_content_hash(canonical),
                created_at=NOW,
                updated_at=NOW,
            )
        )
    db_session.flush()
    available = lock_current_complete_debt_offer(
        db_session, locked_target=target.locked_before_offer
    )

    assert available.error is None
    assert available.locked_offer is not None
    assert available.locked_offer.version.id == current.id
    assert "language" not in repr(available.locked_offer).casefold()
    source = inspect.getsource(
        SqlAlchemyCurrentOfferResolver.lock_current_version_with_all_texts
    )
    assert ".with_for_update(read=True" in source
    assert "OfferStatus.CURRENT.value" in source
    assert zero_side_effects == (
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(OfferAcceptance)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )


def test_locked_policy_and_pending_active_original_exposure_apply_exact_limits(
    db_session: Session,
) -> None:
    seed = _seed_target(db_session, credit_limit="1000", max_open_debts=3)
    _add_complete_offer(db_session, actor=seed.actor)
    _add_debt(db_session, seed=seed, amount="400", status=DebtStatus.PENDING)
    _add_debt(db_session, seed=seed, amount="300", status=DebtStatus.ACTIVE)
    _add_debt(db_session, seed=seed, amount="900", status=DebtStatus.REJECTED)
    locked = _lock_complete_target(db_session, seed)
    before = (
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )

    boundary = evaluate_locked_debt_creation(
        db_session,
        locked_target=locked,
        original_amount=OriginalAmountUZS(Decimal("300")),
        as_of_business_date=date(2026, 8, 10),
    )
    exceeded = evaluate_locked_debt_creation(
        db_session,
        locked_target=locked,
        original_amount=OriginalAmountUZS(Decimal("301")),
        as_of_business_date=date(2026, 8, 10),
    )

    assert boundary.decision is DebtCreationEligibilityDecision.ALLOWED
    assert boundary.error is None
    assert exceeded.error is ErrorCode.CREDIT_LIMIT_EXCEEDED
    assert before == (
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )


def test_blacklist_and_strict_max_fail_without_policy_mutation(
    db_session: Session,
) -> None:
    blacklisted = _seed_target(
        db_session, list_status=ShopCustomerListStatus.BLACKLISTED
    )
    _add_complete_offer(db_session, actor=blacklisted.actor)
    locked = _lock_complete_target(db_session, blacklisted)
    before = (
        blacklisted.shop_customer.credit_limit_uzs,
        blacklisted.shop_customer.max_open_debts,
        blacklisted.shop_customer.list_status,
        blacklisted.shop_customer.revision,
    )

    denied = evaluate_locked_debt_creation(
        db_session,
        locked_target=locked,
        original_amount=OriginalAmountUZS(Decimal("1")),
        as_of_business_date=date(2026, 8, 10),
    )

    assert denied.error is ErrorCode.CUSTOMER_BLACKLISTED
    assert before == (
        blacklisted.shop_customer.credit_limit_uzs,
        blacklisted.shop_customer.max_open_debts,
        blacklisted.shop_customer.list_status,
        blacklisted.shop_customer.revision,
    )


def test_locked_customer_hard_block_port_denies_without_m13_block_rows(
    db_session: Session,
) -> None:
    class FutureHardBlockProjection:
        def __init__(self) -> None:
            self.customer_ids = []

        def read_global_hard_block(self, *, customer_id, as_of_business_date):
            self.customer_ids.append(customer_id)
            return GlobalHardBlockProjection(is_blocked=True)

    seed = _seed_target(db_session)
    _add_complete_offer(db_session, actor=seed.actor)
    future_projection = FutureHardBlockProjection()
    before = (
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )

    result = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
        command=_create_command(),
        global_hard_block_reader=future_projection,
    )

    assert result.error is ErrorCode.CUSTOMER_RATING_BLOCKED
    assert future_projection.customer_ids == [seed.customer.id]
    assert before == (
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )


@pytest.mark.parametrize("mutation", ["staff_revoke", "target_status", "relink"])
def test_live_chain_mutations_serialize_after_target_resolution(
    db_session: Session,
    m2_test_database: Engine,
    mutation: str,
) -> None:
    seed = _seed_target(db_session)
    authority = seed.authority
    shop_customer_id = seed.shop_customer.id
    target_id = seed.target.id
    staff_id = next(
        staff.id
        for staff in db_session.query(ShopStaff).filter_by(shop_id=seed.shop.id)
    )
    link_id = seed.link.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    locked = Event()
    release = Event()

    def resolve_then_hold() -> ErrorCode | None:
        with factory.begin() as session:
            candidate = discover_tenant_debt_target(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(shop_customer_id),
            )
            result = lock_debt_target_before_offer(
                session, authority=authority, candidate=candidate
            )
            locked.set()
            assert release.wait(timeout=5)
            return result.error

    def mutate_live_chain() -> None:
        with factory.begin() as session:
            if mutation == "staff_revoke":
                row = session.get(ShopStaff, staff_id)
                assert row is not None
                row.is_active = False
                row.revoked_at = NOW + timedelta(seconds=1)
            elif mutation == "target_status":
                row = session.get(User, target_id)
                assert row is not None
                row.is_active = False
            else:
                row = session.get(TelegramLink, link_id)
                assert row is not None
                row.linked_at = NOW + timedelta(seconds=1)
                row.phone_verified_at = None
            session.flush()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resolver_future = executor.submit(resolve_then_hold)
        assert locked.wait(timeout=5)
        mutation_future = executor.submit(mutate_live_chain)
        completed, _ = wait((mutation_future,), timeout=0.2)
        assert not completed
        release.set()
        assert resolver_future.result(timeout=5) is None
        mutation_future.result(timeout=5)

    with factory.begin() as session:
        candidate = discover_tenant_debt_target(
            session,
            authority=authority,
            shop_customer_id=ShopCustomerId(shop_customer_id),
        )
        after = lock_debt_target_before_offer(
            session, authority=authority, candidate=candidate
        )
    expected = (
        ErrorCode.FORBIDDEN
        if mutation == "staff_revoke"
        else ErrorCode.SHOP_CUSTOMER_UNAVAILABLE
    )
    assert after.error is expected


def test_offer_switch_serializes_after_exact_current_gate(
    db_session: Session, m2_test_database: Engine
) -> None:
    seed = _seed_target(db_session)
    old = _add_complete_offer(db_session, actor=seed.actor)
    new = _add_complete_offer(
        db_session,
        actor=seed.actor,
        version_number=2,
        status=OfferStatus.APPROVED,
    )
    authority = seed.authority
    shop_customer_id = seed.shop_customer.id
    old_id = old.id
    new_id = new.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    gated = Event()
    release = Event()

    def gate_then_hold():
        with factory.begin() as session:
            candidate = discover_tenant_debt_target(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(shop_customer_id),
            )
            target = lock_debt_target_before_offer(
                session, authority=authority, candidate=candidate
            )
            assert target.locked_before_offer is not None
            result = lock_current_complete_debt_offer(
                session, locked_target=target.locked_before_offer
            )
            gated.set()
            assert release.wait(timeout=5)
            assert result.locked_offer is not None
            return result.locked_offer.version.id

    def switch_offer() -> None:
        with factory.begin() as session:
            previous = session.get(OfferVersion, old_id)
            replacement = session.get(OfferVersion, new_id)
            assert previous is not None and replacement is not None
            previous.status = OfferStatus.APPROVED.value
            session.flush()
            replacement.status = OfferStatus.CURRENT.value
            replacement.current_by_user_id = authority.actor_user_id
            replacement.current_at = NOW + timedelta(seconds=1)
            session.flush()

    with ThreadPoolExecutor(max_workers=2) as executor:
        gate_future = executor.submit(gate_then_hold)
        assert gated.wait(timeout=5)
        switch_future = executor.submit(switch_offer)
        completed, _ = wait((switch_future,), timeout=0.2)
        assert not completed
        release.set()
        assert gate_future.result(timeout=5) == old_id
        switch_future.result(timeout=5)

    with factory.begin() as session:
        current = SqlAlchemyCurrentOfferResolver(
            session
        ).lock_current_version_with_all_texts(purpose=OfferPurpose.DEBT_ACCEPTANCE)
        assert current is not None
        assert current[0].id == new_id


def test_policy_update_serializes_and_each_reader_sees_one_complete_policy(
    db_session: Session, m2_test_database: Engine
) -> None:
    seed = _seed_target(db_session, credit_limit="1000", max_open_debts=3)
    _add_complete_offer(db_session, actor=seed.actor)
    authority = seed.authority
    shop_customer_id = seed.shop_customer.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    evaluated = Event()
    release = Event()

    def evaluate_then_hold() -> ErrorCode | None:
        with factory.begin() as session:
            target = _lock_complete_target_by_locator(
                session,
                authority=authority,
                shop_customer_id=shop_customer_id,
            )
            result = evaluate_locked_debt_creation(
                session,
                locked_target=target,
                original_amount=OriginalAmountUZS(Decimal("500")),
                as_of_business_date=date(2026, 8, 10),
            )
            evaluated.set()
            assert release.wait(timeout=5)
            return result.error

    def update_policy() -> None:
        with factory.begin() as session:
            row = session.get(ShopCustomer, shop_customer_id)
            assert row is not None
            row.credit_limit_uzs = Decimal("100")
            row.max_open_debts = 1
            row.revision += 1
            session.flush()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reader_future = executor.submit(evaluate_then_hold)
        assert evaluated.wait(timeout=5)
        update_future = executor.submit(update_policy)
        completed, _ = wait((update_future,), timeout=0.2)
        assert not completed
        release.set()
        assert reader_future.result(timeout=5) is None
        update_future.result(timeout=5)

    with factory.begin() as session:
        target = _lock_complete_target_by_locator(
            session,
            authority=authority,
            shop_customer_id=shop_customer_id,
        )
        after = evaluate_locked_debt_creation(
            session,
            locked_target=target,
            original_amount=OriginalAmountUZS(Decimal("500")),
            as_of_business_date=date(2026, 8, 10),
        )
        assert after.error is ErrorCode.CREDIT_LIMIT_EXCEEDED


def test_parallel_distinct_create_attempts_serialize_on_open_count(
    db_session: Session, m2_test_database: Engine
) -> None:
    seed = _seed_target(db_session, credit_limit="10000", max_open_debts=1)
    _add_complete_offer(db_session, actor=seed.actor)
    authority = seed.authority
    actor_id = seed.actor.id
    shop_customer_id = seed.shop_customer.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    start = Event()

    def create_once() -> ErrorCode | None:
        assert start.wait(timeout=5)
        with factory.begin() as session:
            target = _lock_complete_target_by_locator(
                session,
                authority=authority,
                shop_customer_id=shop_customer_id,
            )
            gate = evaluate_locked_debt_creation(
                session,
                locked_target=target,
                original_amount=OriginalAmountUZS(Decimal("100")),
                as_of_business_date=date(2026, 8, 10),
            )
            if gate.error is None:
                session.add(
                    Debt(
                        shop_customer_id=shop_customer_id,
                        created_by_user_id=actor_id,
                        original_amount_uzs=Decimal("100"),
                        discount_basis_points=0,
                        discounted_amount_uzs=Decimal("100"),
                        due_date=date(2026, 8, 20),
                        pending_expires_at=NOW + timedelta(hours=72),
                        status=DebtStatus.PENDING.value,
                        revision=1,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                session.flush()
            return gate.error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(create_once), executor.submit(create_once))
        start.set()
        outcomes = [future.result(timeout=10) for future in futures]

    assert outcomes.count(None) == 1
    assert outcomes.count(ErrorCode.MAX_OPEN_DEBTS) == 1
    with factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(Debt)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_create_coordinator_writes_one_atomic_unit_and_replay_is_zero_write(
    db_session: Session,
) -> None:
    seed = _seed_target(db_session, credit_limit="10000", max_open_debts=3)
    _add_complete_offer(db_session, actor=seed.actor)
    raw_key = str(uuid4())
    command = _create_command(key=raw_key, amount="1000", discount="1.25")

    created = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
        command=command,
    )
    assert created.outcome is IdempotencyOutcome.NEW
    assert created.error is None
    assert created.debt_id is not None
    counts_after_create = (
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == AuditEventType.DEBT_CREATED.value)
        ),
    )
    replay = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
        command=command,
    )
    conflict = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
        command=_create_command(key=raw_key, amount="1001", discount="1.25"),
    )

    assert counts_after_create == (1, 1, 1)
    assert replay.outcome is IdempotencyOutcome.REPLAY
    assert replay.debt_id == created.debt_id
    assert conflict.error is ErrorCode.IDEMPOTENCY_CONFLICT
    assert counts_after_create == (
        db_session.scalar(select(func.count()).select_from(IdempotencyKey)),
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == AuditEventType.DEBT_CREATED.value)
        ),
    )
    key_row = db_session.scalar(select(IdempotencyKey))
    debt_row = db_session.scalar(select(Debt))
    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.event_type == AuditEventType.DEBT_CREATED.value)
    )
    assert key_row is not None and raw_key not in repr(key_row)
    assert debt_row is not None and debt_row.id == created.debt_id.as_uuid()
    assert debt_row.discount_basis_points == 125
    assert debt_row.discounted_amount_uzs == Decimal("988")
    assert audit_row is not None and audit_row.object_id == debt_row.id


@pytest.mark.parametrize("role", tuple(ShopRole))
def test_every_active_shop_role_can_create_pending_proposal(
    db_session: Session, role: ShopRole
) -> None:
    seed = _seed_target(db_session, role=role)
    _add_complete_offer(db_session, actor=seed.actor)

    result = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
        command=_create_command(),
    )

    assert result.outcome is IdempotencyOutcome.NEW
    assert result.error is None


def test_create_customer_lock_midnight_wait_uses_post_lock_business_date(
    db_session: Session, m2_test_database: Engine
) -> None:
    seed = _seed_target(db_session, credit_limit="10000", max_open_debts=3)
    _add_complete_offer(db_session, actor=seed.actor)
    _add_debt(db_session, seed=seed, amount="100", status=DebtStatus.ACTIVE)
    existing = db_session.scalar(select(Debt))
    assert existing is not None
    existing.created_at = datetime(2026, 8, 1, tzinfo=UTC)
    existing.pending_expires_at = datetime(2026, 8, 4, tzinfo=UTC)
    existing.accepted_at = datetime(2026, 8, 4, tzinfo=UTC)
    existing.updated_at = existing.accepted_at
    existing.due_date = date(2026, 8, 9)
    authority = seed.authority
    customer_id = seed.customer.id
    shop_customer_id = seed.shop_customer.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    lock_held = Event()
    release = Event()
    clock_called = Event()

    def hold_customer() -> None:
        with factory.begin() as session:
            session.scalar(
                select(Customer).where(Customer.id == customer_id).with_for_update()
            )
            lock_held.set()
            assert release.wait(timeout=10)

    def create() -> ErrorCode | None:
        assert lock_held.wait(timeout=10)

        def clock() -> datetime:
            clock_called.set()
            return datetime(2026, 8, 9, 19, tzinfo=UTC)

        with factory.begin() as session:
            return create_pending_debt_proposal(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(shop_customer_id),
                command=_create_command(),
                hard_block_clock=clock,
            ).error

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_customer)
        creator = pool.submit(create)
        assert lock_held.wait(timeout=10)
        assert not clock_called.is_set()
        release.set()
        holder.result(timeout=10)
        assert creator.result(timeout=10) is ErrorCode.CUSTOMER_RATING_BLOCKED

    assert clock_called.is_set()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Debt)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0


@pytest.mark.parametrize(
    ("invalid_state", "expected_error"),
    (
        ("actor_inactive", ErrorCode.FORBIDDEN),
        ("staff_revoked", ErrorCode.FORBIDDEN),
        ("wrong_tenant", ErrorCode.SHOP_CUSTOMER_UNAVAILABLE),
        ("platform_admin_only", ErrorCode.FORBIDDEN),
        ("shop_suspended", ErrorCode.FORBIDDEN),
        ("telegram_drift", ErrorCode.SHOP_CUSTOMER_UNAVAILABLE),
        ("customer_drift", ErrorCode.SHOP_CUSTOMER_UNAVAILABLE),
    ),
)
def test_create_authority_and_live_drift_failures_are_zero_write(
    db_session: Session,
    invalid_state: str,
    expected_error: ErrorCode,
) -> None:
    seed = _seed_target(db_session)
    _add_complete_offer(db_session, actor=seed.actor)
    target_id = seed.shop_customer.id
    if invalid_state == "actor_inactive":
        seed.actor.is_active = False
    elif invalid_state in {"staff_revoked", "platform_admin_only"}:
        staff = db_session.scalar(
            select(ShopStaff).where(ShopStaff.shop_id == seed.shop.id)
        )
        assert staff is not None
        staff.is_active = False
        staff.revoked_at = NOW
        if invalid_state == "platform_admin_only":
            seed.actor.is_platform_admin = True
    elif invalid_state == "wrong_tenant":
        other = _seed_target(db_session)
        target_id = other.shop_customer.id
    elif invalid_state == "shop_suspended":
        seed.shop.status = ShopStatus.SUSPENDED.value
    elif invalid_state == "telegram_drift":
        seed.link.phone_verified_at = None
    else:
        seed.customer.onboarding_status = "draft"
        seed.customer.activated_at = None
    db_session.flush()

    result = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(target_id),
        command=_create_command(),
    )

    assert result.error is expected_error
    assert db_session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
    assert db_session.scalar(select(func.count()).select_from(Debt)) == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == AuditEventType.DEBT_CREATED.value)
        )
        == 0
    )


def test_different_keys_observe_new_open_count_and_failed_key_is_not_consumed(
    db_session: Session,
) -> None:
    seed = _seed_target(db_session, credit_limit="10000", max_open_debts=1)
    _add_complete_offer(db_session, actor=seed.actor)

    first = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
        command=_create_command(),
    )
    second = create_pending_debt_proposal(
        db_session,
        authority=seed.authority,
        shop_customer_id=ShopCustomerId(seed.shop_customer.id),
        command=_create_command(),
    )

    assert first.outcome is IdempotencyOutcome.NEW
    assert second.error is ErrorCode.MAX_OPEN_DEBTS
    assert db_session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
    assert db_session.scalar(select(func.count()).select_from(Debt)) == 1


def test_parallel_same_key_converges_to_one_key_debt_and_audit(
    db_session: Session, m2_test_database: Engine
) -> None:
    seed = _seed_target(db_session, credit_limit="10000", max_open_debts=1)
    _add_complete_offer(db_session, actor=seed.actor)
    authority = seed.authority
    shop_customer_id = seed.shop_customer.id
    command = _create_command()
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    start = Event()

    def create_once():
        assert start.wait(timeout=5)
        with factory.begin() as session:
            return create_pending_debt_proposal(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(shop_customer_id),
                command=command,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(create_once), executor.submit(create_once))
        start.set()
        results = [future.result(timeout=10) for future in futures]

    assert {result.outcome for result in results} == {
        IdempotencyOutcome.NEW,
        IdempotencyOutcome.REPLAY,
    }
    assert results[0].debt_id == results[1].debt_id
    with factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
        assert session.scalar(select(func.count()).select_from(Debt)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == AuditEventType.DEBT_CREATED.value)
            )
            == 1
        )


def test_parallel_same_raw_key_is_actor_scoped_across_distinct_tenants(
    db_session: Session, m2_test_database: Engine
) -> None:
    first = _seed_target(db_session)
    second = _seed_target(db_session)
    _add_complete_offer(db_session, actor=first.actor)
    raw_key = str(uuid4())
    command = _create_command(key=raw_key)
    inputs = (
        (first.authority, first.shop_customer.id),
        (second.authority, second.shop_customer.id),
    )
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)
    start = Event()

    def create_once(authority, shop_customer_id):
        assert start.wait(timeout=5)
        with factory.begin() as session:
            return create_pending_debt_proposal(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(shop_customer_id),
                command=command,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(create_once, *item) for item in inputs)
        start.set()
        results = [future.result(timeout=10) for future in futures]

    assert all(result.outcome is IdempotencyOutcome.NEW for result in results)
    assert results[0].debt_id != results[1].debt_id
    with factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 2
        assert session.scalar(select(func.count()).select_from(Debt)) == 2


@pytest.mark.parametrize("scope", ("customer", "shop"))
def test_same_actor_key_conflicts_for_a_different_customer_or_shop(
    db_session: Session, scope: str
) -> None:
    first = _seed_target(db_session)
    _add_complete_offer(db_session, actor=first.actor)
    second_shop = first.shop
    second_authority = first.authority
    if scope == "shop":
        second_shop = Shop(
            name="M13 second actor shop",
            phone=_phone(),
            status=ShopStatus.ACTIVE.value,
            created_at=NOW,
            updated_at=NOW,
        )
        db_session.add(second_shop)
        db_session.flush()
        db_session.add(
            ShopStaff(
                shop_id=second_shop.id,
                user_id=first.actor.id,
                role=ShopRole.OWNER.value,
                is_active=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        second_authority = DetachedDebtActorAuthority(
            status=CurrentSessionStatus.AUTHENTICATED,
            actor_user_id=first.actor.id,
            current_shop_id=second_shop.id,
            request_context=DebtRequestContext(is_htmx=False),
        )
    second_target = User(phone=_phone(), is_active=True)
    db_session.add(second_target)
    db_session.flush()
    second_customer = Customer(
        user_id=second_target.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        activated_at=NOW,
    )
    second_link = TelegramLink(
        user_id=second_target.id,
        telegram_chat_id=uuid4().int % 2_000_000_000,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    db_session.add_all((second_customer, second_link))
    db_session.flush()
    second_shop_customer = ShopCustomer(
        shop_id=second_shop.id,
        customer_id=second_customer.id,
        credit_limit_uzs=Decimal("1000"),
        max_open_debts=3,
        list_status=ShopCustomerListStatus.NORMAL.value,
        revision=1,
        created_by_user_id=first.actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(second_shop_customer)
    db_session.flush()
    command = _create_command()

    created = create_pending_debt_proposal(
        db_session,
        authority=first.authority,
        shop_customer_id=ShopCustomerId(first.shop_customer.id),
        command=command,
    )
    conflict = create_pending_debt_proposal(
        db_session,
        authority=second_authority,
        shop_customer_id=ShopCustomerId(second_shop_customer.id),
        command=command,
    )

    assert created.outcome is IdempotencyOutcome.NEW
    assert conflict.error is ErrorCode.IDEMPOTENCY_CONFLICT
    assert db_session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
    assert db_session.scalar(select(func.count()).select_from(Debt)) == 1
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.parametrize("fault", ("debt_flush", "audit", "commit_boundary"))
def test_create_faults_roll_back_key_debt_and_audit(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    seed = _seed_target(db_session)
    _add_complete_offer(db_session, actor=seed.actor)
    authority = seed.authority
    shop_customer_id = seed.shop_customer.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)

    if fault == "debt_flush":
        monkeypatch.setattr(
            debt_service,
            "insert_debt",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("flush fault")),
        )
    elif fault == "audit":
        monkeypatch.setattr(
            debt_service,
            "append_audit_event",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit fault")),
        )

    with pytest.raises(RuntimeError):
        with factory.begin() as session:
            result = create_pending_debt_proposal(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(shop_customer_id),
                command=_create_command(),
            )
            if fault == "commit_boundary":
                assert result.outcome is IdempotencyOutcome.NEW
                raise RuntimeError("commit boundary fault")

    with factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
        assert session.scalar(select(func.count()).select_from(Debt)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_unexpected_idempotency_integrity_error_propagates_and_rolls_back(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _seed_target(db_session)
    _add_complete_offer(db_session, actor=seed.actor)
    authority = seed.authority
    shop_customer_id = seed.shop_customer.id
    db_session.commit()
    factory = create_database_session_factory(m2_test_database)

    def unexpected_unique(*args, **kwargs):
        raise IntegrityError("unexpected unique", {}, RuntimeError("unexpected"))

    monkeypatch.setattr(debt_service, "insert_or_resolve_key", unexpected_unique)
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            create_pending_debt_proposal(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(shop_customer_id),
                command=_create_command(),
            )

    with factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
        assert session.scalar(select(func.count()).select_from(Debt)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
