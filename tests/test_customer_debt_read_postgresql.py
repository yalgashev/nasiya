from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.debt.customer_authority import (
    CustomerDebtAuthority,
    resolve_own_customer_debt_authority,
)
from app.debt.customer_read_service import (
    CustomerDebtDetailProjection,
    CustomerDebtLegalOfferProjection,
    get_own_customer_debt_detail,
    list_own_customer_debts,
)
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.values import DebtId
from app.offers.content import canonicalize_offer_text, compute_offer_content_hash
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferText, OfferVersion
from app.shop.enums import ShopStatus
from app.shop.models import Shop
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.models import ShopCustomer
from app.telegram.models import TelegramLink

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 8, 7, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    from app.db import create_database_session_factory

    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@dataclass(frozen=True)
class Seed:
    user: User
    customer: Customer
    link: TelegramLink
    shop: Shop
    debt: Debt


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _seed_owned_debt(session: Session, *, shop_name: str = "Own debt shop") -> Seed:
    user = User(phone=_phone(), is_active=True)
    session.add(user)
    session.flush()
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        activated_at=NOW,
    )
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=uuid4().int % 2_000_000_000,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    shop = Shop(
        name=shop_name,
        phone=_phone(),
        status=ShopStatus.ACTIVE.value,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all((customer, link, shop))
    session.flush()
    shop_customer = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000"),
        max_open_debts=3,
        list_status=ShopCustomerListStatus.NORMAL.value,
        revision=1,
        created_by_user_id=user.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(shop_customer)
    session.flush()
    debt = Debt(
        shop_customer_id=shop_customer.id,
        created_by_user_id=user.id,
        original_amount_uzs=Decimal("100"),
        discount_basis_points=500,
        discounted_amount_uzs=Decimal("95"),
        due_date=date(2026, 8, 12),
        pending_expires_at=NOW + timedelta(hours=72),
        status=DebtStatus.PENDING.value,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(debt)
    session.flush()
    return Seed(user=user, customer=customer, link=link, shop=shop, debt=debt)


def _add_current_complete_debt_offer(
    session: Session,
    *,
    actor: User,
    languages: tuple[OfferLanguage, ...] = tuple(OfferLanguage),
) -> OfferVersion:
    version = OfferVersion(
        purpose=OfferPurpose.DEBT_ACCEPTANCE.value,
        version_number=1,
        status=OfferStatus.CURRENT.value,
        created_by_user_id=actor.id,
        created_at=NOW - timedelta(hours=3),
        legal_review_authority="M13 legal",
        legal_reviewed_at=NOW - timedelta(hours=2),
        legal_review_reference="M13-LEGAL-1",
        approved_by_user_id=actor.id,
        approved_at=NOW - timedelta(hours=1),
        current_by_user_id=actor.id,
        current_at=NOW,
    )
    session.add(version)
    session.flush()
    for language in languages:
        canonical = canonicalize_offer_text(
            title=f"Legal title {language.value}",
            body=f"Legal body {language.value}",
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


def test_own_customer_debt_authority_is_immutable_and_redacted() -> None:
    authority = CustomerDebtAuthority(
        user_id=UUID("11111111-1111-4111-8111-111111111111"),
        customer_id=UUID("22222222-2222-4222-8222-222222222222"),
    )

    rendered = repr(authority)
    assert "11111111" not in rendered
    assert "22222222" not in rendered
    assert tuple(field.name for field in fields(authority)) == (
        "user_id",
        "customer_id",
    )


def test_own_customer_read_projects_only_safe_current_legal_content_without_writes(
    db_session: Session,
) -> None:
    seed = _seed_owned_debt(db_session, shop_name="Customer-visible shop")
    _add_current_complete_debt_offer(db_session, actor=seed.user)
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    before = (
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )

    items = list_own_customer_debts(db_session, authority=authority)
    result = get_own_customer_debt_detail(
        db_session,
        authority=authority,
        debt_id=DebtId(seed.debt.id),
        language=OfferLanguage.RU,
    )

    assert authority is not None
    assert len(items) == 1
    assert items[0].shop_name == "Customer-visible shop"
    assert items[0].discounted_amount.value == Decimal("95")
    assert result.error is None
    assert result.detail is not None
    assert result.detail.legal_offer == CustomerDebtLegalOfferProjection(
        language=OfferLanguage.RU,
        title="Legal title RU",
        body="Legal body RU",
    )
    assert result.detail.decision_reason is None
    exposed = {field.name for field in fields(CustomerDebtDetailProjection)}
    assert {
        "id",
        "customer_id",
        "shop_customer_id",
        "user_id",
        "content_hash",
        "user_agent",
        "policy",
        "staff",
    }.isdisjoint(exposed)
    assert tuple(field.name for field in fields(CustomerDebtLegalOfferProjection)) == (
        "language",
        "title",
        "body",
    )
    assert str(seed.debt.id) not in repr(result)
    assert before == (
        db_session.scalar(select(func.count()).select_from(Debt)),
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    )
    assert not db_session.new
    assert not db_session.dirty


def test_foreign_customer_debt_locator_is_generic_unavailable_and_never_listed(
    db_session: Session,
) -> None:
    owner = _seed_owned_debt(db_session, shop_name="Owner shop")
    other = _seed_owned_debt(db_session, shop_name="Other shop")
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=other.user
    )

    result = get_own_customer_debt_detail(
        db_session,
        authority=authority,
        debt_id=DebtId(owner.debt.id),
        language=OfferLanguage.UZ_LATN,
    )
    items = list_own_customer_debts(db_session, authority=authority)

    assert result.error is ErrorCode.DEBT_UNAVAILABLE
    assert result.detail is None
    assert len(items) == 1
    assert items[0].shop_name == "Other shop"
    assert str(owner.debt.id) not in repr(result)
    assert str(owner.customer.id) not in repr(result)


def test_pending_detail_exposes_legal_text_only_from_a_current_complete_version(
    db_session: Session,
) -> None:
    seed = _seed_owned_debt(db_session)
    version = _add_current_complete_debt_offer(
        db_session,
        actor=seed.user,
        languages=(OfferLanguage.UZ_LATN,),
    )
    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    before = db_session.scalar(select(func.count()).select_from(OfferText))

    incomplete = get_own_customer_debt_detail(
        db_session,
        authority=authority,
        debt_id=DebtId(seed.debt.id),
        language=OfferLanguage.UZ_LATN,
    )
    assert incomplete.detail is not None
    assert incomplete.detail.legal_offer is None

    for language in (OfferLanguage.UZ_CYRL, OfferLanguage.RU):
        canonical = canonicalize_offer_text(
            title=f"Legal title {language.value}",
            body=f"Legal body {language.value}",
        )
        db_session.add(
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
    db_session.flush()

    complete = get_own_customer_debt_detail(
        db_session,
        authority=authority,
        debt_id=DebtId(seed.debt.id),
        language=OfferLanguage.UZ_LATN,
    )
    assert complete.detail is not None
    assert complete.detail.legal_offer is not None
    assert complete.detail.legal_offer.title == "Legal title UZ_LATN"
    assert before + 2 == db_session.scalar(select(func.count()).select_from(OfferText))


def test_own_visible_terminal_reason_is_not_available_to_another_customer(
    db_session: Session,
) -> None:
    owner = _seed_owned_debt(db_session)
    other = _seed_owned_debt(db_session)
    owner.debt.status = DebtStatus.REJECTED.value
    owner.debt.rejected_at = NOW + timedelta(minutes=1)
    owner.debt.rejection_reason = "Customer-visible reason"
    owner.debt.updated_at = NOW + timedelta(minutes=1)
    db_session.flush()
    owner_authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=owner.user
    )
    other_authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=other.user
    )

    owned = get_own_customer_debt_detail(
        db_session,
        authority=owner_authority,
        debt_id=DebtId(owner.debt.id),
        language=OfferLanguage.UZ_LATN,
    )
    foreign = get_own_customer_debt_detail(
        db_session,
        authority=other_authority,
        debt_id=DebtId(owner.debt.id),
        language=OfferLanguage.UZ_LATN,
    )

    assert owned.detail is not None
    assert owned.detail.decision_reason == "Customer-visible reason"
    assert owned.detail.legal_offer is None
    assert foreign.error is ErrorCode.DEBT_UNAVAILABLE
    assert "Customer-visible reason" not in repr(foreign)


@pytest.mark.parametrize("state", ("inactive", "draft", "unverified", "missing"))
def test_inactive_or_non_live_own_customer_chain_has_only_safe_outcomes(
    db_session: Session, state: str
) -> None:
    seed = _seed_owned_debt(db_session)
    if state == "inactive":
        seed.user.is_active = False
    elif state == "draft":
        seed.customer.onboarding_status = "draft"
        seed.customer.activated_at = None
    elif state == "unverified":
        seed.link.phone_verified_at = None
    else:
        db_session.delete(seed.link)
    db_session.flush()

    authority = resolve_own_customer_debt_authority(
        db_session, authenticated_user=seed.user
    )
    result = get_own_customer_debt_detail(
        db_session,
        authority=authority,
        debt_id=DebtId(seed.debt.id),
        language=OfferLanguage.UZ_LATN,
    )

    assert authority is None
    assert list_own_customer_debts(db_session, authority=authority) == ()
    assert result.error is ErrorCode.DEBT_UNAVAILABLE
    assert result.detail is None
