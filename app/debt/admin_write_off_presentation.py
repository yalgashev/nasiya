"""Safe localized projections for the bounded admin write-off pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.customer.models import Customer
from app.debt.contracts import WriteOffReason
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.payment_progress import DebtWebPaymentProgressReader
from app.debt.presentation import DebtWebLanguage
from app.debt.repository import WrittenOffCandidateLocator
from app.debt.values import DebtId, DebtRevision
from app.debt.write_off_targeting import (
    discover_admin_write_off_target,
    list_admin_write_off_candidates,
    read_admin_completed_write_off,
)
from app.offers.authorization import PlatformAdminActor
from app.shop.repository import list_shops_by_ids
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer

__all__ = (
    "ADMIN_WRITE_OFF_COPY",
    "AdminWriteOffCompletedView",
    "AdminWriteOffFreshView",
    "AdminWriteOffPageCopy",
    "AdminWriteOffSummary",
    "present_admin_write_off_candidates",
    "present_admin_write_off_completed",
    "present_admin_write_off_fresh",
)


@dataclass(frozen=True, slots=True)
class AdminWriteOffPageCopy:
    page_title: str
    candidates_heading: str
    form_heading: str
    completed_heading: str
    navigation_label: str
    offers_link: str
    back_link: str
    empty: str
    view_action: str
    shop_label: str
    customer_label: str
    due_date_label: str
    overdue_date_label: str
    remaining_label: str
    status_label: str
    reason_label: str
    confirmation_label: str
    submit_label: str
    error_heading: str
    generic_error: str
    status_labels: Mapping[DebtStatus, str]
    reason_labels: Mapping[WriteOffReason, str]


_UZ_REASON_LABELS: Final = MappingProxyType(
    {
        WriteOffReason.COLLECTION_EXHAUSTED: "Undirish imkoniyatlari tugagan",
        WriteOffReason.CUSTOMER_UNREACHABLE: "Mijoz bilan aloqa o‘rnatilmadi",
        WriteOffReason.INSOLVENCY_OR_DECEASED: ("To‘lovga qodir emas yoki vafot etgan"),
        WriteOffReason.LEGAL_OR_COMPLIANCE: "Huquqiy yoki muvofiqlik sababi",
        WriteOffReason.FRAUD_OR_ABUSE: "Firibgarlik yoki suiiste’mol",
    }
)
_RU_REASON_LABELS: Final = MappingProxyType(
    {
        WriteOffReason.COLLECTION_EXHAUSTED: "Возможности взыскания исчерпаны",
        WriteOffReason.CUSTOMER_UNREACHABLE: "Не удалось связаться с клиентом",
        WriteOffReason.INSOLVENCY_OR_DECEASED: (
            "Неплатёжеспособность или смерть клиента"
        ),
        WriteOffReason.LEGAL_OR_COMPLIANCE: "Юридическая причина или комплаенс",
        WriteOffReason.FRAUD_OR_ABUSE: "Мошенничество или злоупотребление",
    }
)

ADMIN_WRITE_OFF_COPY: Final = MappingProxyType(
    {
        DebtWebLanguage.UZ_LATN: AdminWriteOffPageCopy(
            page_title="Qarzni undirishdan chiqarish",
            candidates_heading="Undirishdan chiqarish uchun qarzlar",
            form_heading="Qarzni undirishdan chiqarish",
            completed_heading="Qarz undirishdan chiqarilgan",
            navigation_label="Admin navigatsiyasi",
            offers_link="Offerlar",
            back_link="Ro‘yxatga qaytish",
            empty="Mos qarzlar yo‘q.",
            view_action="Ko‘rish",
            shop_label="Do‘kon",
            customer_label="Mijoz telefoni",
            due_date_label="To‘lov sanasi",
            overdue_date_label="Muddati o‘tgan sana",
            remaining_label="Asl summa bo‘yicha qoldiq",
            status_label="Holat",
            reason_label="Undirishdan chiqarish sababi",
            confirmation_label=(
                "Bu qaytarib bo‘lmaydigan amal ekanini tushunaman va tasdiqlayman."
            ),
            submit_label="Undirishdan chiqarishni tasdiqlash",
            error_heading="Xato:",
            generic_error="Amalni bajarib bo‘lmadi. Ma’lumotlarni qayta tekshiring.",
            status_labels=MappingProxyType(
                {
                    DebtStatus.OVERDUE: "Muddati o‘tgan",
                    DebtStatus.WRITTEN_OFF: "Undirishdan chiqarilgan",
                    DebtStatus.WRITTEN_OFF_SETTLED: "Undirish qarzi yopilgan",
                }
            ),
            reason_labels=_UZ_REASON_LABELS,
        ),
        DebtWebLanguage.RU: AdminWriteOffPageCopy(
            page_title="Списание долга для взыскания",
            candidates_heading="Долги для списания",
            form_heading="Списание долга для взыскания",
            completed_heading="Долг списан для взыскания",
            navigation_label="Навигация администратора",
            offers_link="Оферты",
            back_link="Вернуться к списку",
            empty="Подходящих долгов нет.",
            view_action="Открыть",
            shop_label="Магазин",
            customer_label="Телефон клиента",
            due_date_label="Дата платежа",
            overdue_date_label="Дата просрочки",
            remaining_label="Остаток по исходной сумме",
            status_label="Статус",
            reason_label="Причина списания",
            confirmation_label=(
                "Я понимаю, что это необратимое действие, и подтверждаю его."
            ),
            submit_label="Подтвердить списание",
            error_heading="Ошибка:",
            generic_error="Не удалось выполнить действие. Проверьте данные ещё раз.",
            status_labels=MappingProxyType(
                {
                    DebtStatus.OVERDUE: "Срок оплаты истёк",
                    DebtStatus.WRITTEN_OFF: "Списан для взыскания",
                    DebtStatus.WRITTEN_OFF_SETTLED: "Списанный долг погашен",
                }
            ),
            reason_labels=_RU_REASON_LABELS,
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class AdminWriteOffSummary:
    debt_id: DebtId = field(repr=False)
    shop_name: str
    masked_phone: str = field(repr=False)
    due_date: date
    overdue_at: datetime
    remaining_original_uzs: Decimal = field(repr=False)
    status: DebtStatus

    def __post_init__(self) -> None:
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Write-off summary Debt is invalid")
        if not isinstance(self.shop_name, str) or not self.shop_name.strip():
            raise ValueError("Write-off summary Shop is invalid")
        if not isinstance(self.masked_phone, str) or "*" not in self.masked_phone:
            raise ValueError("Write-off summary phone is invalid")
        if not isinstance(self.due_date, date):
            raise ValueError("Write-off summary due date is invalid")
        if self.overdue_at.tzinfo is None or self.overdue_at.utcoffset() is None:
            raise ValueError("Write-off summary overdue time is invalid")
        if (
            not isinstance(self.remaining_original_uzs, Decimal)
            or self.remaining_original_uzs < 0
            or self.remaining_original_uzs
            != self.remaining_original_uzs.to_integral_value()
        ):
            raise ValueError("Write-off summary remaining amount is invalid")
        if self.status not in {
            DebtStatus.OVERDUE,
            DebtStatus.WRITTEN_OFF,
            DebtStatus.WRITTEN_OFF_SETTLED,
        }:
            raise ValueError("Write-off summary status is invalid")

    @property
    def detail_path(self) -> str:
        return f"/admin/debts/{self.debt_id.as_uuid()}/write-off"

    def __repr__(self) -> str:
        return "AdminWriteOffSummary(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AdminWriteOffFreshView:
    summary: AdminWriteOffSummary = field(repr=False)
    revision: DebtRevision = field(repr=False)

    def __repr__(self) -> str:
        return "AdminWriteOffFreshView(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AdminWriteOffCompletedView:
    summary: AdminWriteOffSummary = field(repr=False)
    reason: WriteOffReason = field(repr=False)

    def __repr__(self) -> str:
        return "AdminWriteOffCompletedView(<redacted>)"


def present_admin_write_off_candidates(
    session: Session,
    *,
    actor: PlatformAdminActor,
    progress_reader: DebtWebPaymentProgressReader,
    server_now: datetime,
) -> tuple[AdminWriteOffSummary, ...]:
    candidates = list_admin_write_off_candidates(session, actor=actor)
    summaries = _read_summaries(
        session,
        debt_ids=tuple(candidate.debt_id for candidate in candidates),
        statuses=(DebtStatus.OVERDUE,),
        progress_reader=progress_reader,
        server_now=server_now,
    )
    return tuple(
        summaries[candidate.debt_id.as_uuid()]
        for candidate in candidates
        if candidate.debt_id.as_uuid() in summaries
        and _same_source_time(summaries[candidate.debt_id.as_uuid()], candidate)
    )


def present_admin_write_off_fresh(
    session: Session,
    *,
    actor: PlatformAdminActor,
    debt_id: DebtId,
    progress_reader: DebtWebPaymentProgressReader,
    server_now: datetime,
) -> AdminWriteOffFreshView | None:
    target = discover_admin_write_off_target(session, actor=actor, debt_id=debt_id)
    if target is None:
        return None
    summary = _read_summaries(
        session,
        debt_ids=(debt_id,),
        statuses=(DebtStatus.OVERDUE,),
        progress_reader=progress_reader,
        server_now=server_now,
    ).get(debt_id.as_uuid())
    if summary is None:
        return None
    return AdminWriteOffFreshView(
        summary=summary,
        revision=target.revision,
    )


def present_admin_write_off_completed(
    session: Session,
    *,
    actor: PlatformAdminActor,
    debt_id: DebtId,
    progress_reader: DebtWebPaymentProgressReader,
    server_now: datetime,
) -> AdminWriteOffCompletedView | None:
    completed = read_admin_completed_write_off(session, actor=actor, debt_id=debt_id)
    if completed is None:
        return None
    summary = _read_summaries(
        session,
        debt_ids=(debt_id,),
        statuses=(DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF_SETTLED),
        actor_user_id=actor.user_id,
        progress_reader=progress_reader,
        server_now=server_now,
    ).get(debt_id.as_uuid())
    if summary is None or summary.status is not completed.status:
        return None
    return AdminWriteOffCompletedView(
        summary=summary,
        reason=completed.reason,
    )


def _read_summaries(
    session: Session,
    *,
    debt_ids: Sequence[DebtId],
    statuses: tuple[DebtStatus, ...],
    actor_user_id: UUID | None = None,
    progress_reader: DebtWebPaymentProgressReader,
    server_now: datetime,
) -> dict[UUID, AdminWriteOffSummary]:
    if not debt_ids:
        return {}
    statement = (
        select(
            Debt,
            ShopCustomer.shop_id,
            User.phone.label("customer_phone"),
        )
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .join(User, User.id == Customer.user_id)
        .where(
            Debt.id.in_(tuple(debt_id.as_uuid() for debt_id in debt_ids)),
            Debt.status.in_(tuple(status.value for status in statuses)),
            Debt.overdue_at.is_not(None),
        )
    )
    if actor_user_id is not None:
        statement = statement.where(Debt.written_off_actor_user_id == actor_user_id)
    rows = session.execute(statement).all()
    debts = tuple(row[0] for row in rows)
    progress_by_debt_id = progress_reader.list_payment_progress_for_debts(
        session,
        debts=debts,
        server_now=server_now,
    )
    shops = {
        shop.id: shop.name
        for shop in list_shops_by_ids(
            session,
            shop_ids={ShopId(row.shop_id) for row in rows},
        )
    }
    result: dict[UUID, AdminWriteOffSummary] = {}
    for row in rows:
        debt = row[0]
        progress = progress_by_debt_id.get(debt.id)
        shop_name = shops.get(row.shop_id)
        if debt.overdue_at is None or progress is None or shop_name is None:
            continue
        try:
            result[debt.id] = AdminWriteOffSummary(
                debt_id=DebtId(debt.id),
                shop_name=shop_name,
                masked_phone=mask_phone_for_display(row.customer_phone),
                due_date=debt.due_date,
                overdue_at=debt.overdue_at,
                remaining_original_uzs=Decimal(progress.remaining_due_uzs),
                status=DebtStatus(debt.status),
            )
        except (TypeError, ValueError):
            continue
    return result


def _same_source_time(
    summary: AdminWriteOffSummary, candidate: WrittenOffCandidateLocator
) -> bool:
    return summary.overdue_at == candidate.overdue_at
