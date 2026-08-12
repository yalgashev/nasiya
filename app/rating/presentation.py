"""Exact route inventory and localized safe risk-band presentation labels."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from app.debt.presentation import DebtWebLanguage
from app.rating.contracts import RiskBandDisclosureProjection
from app.rating.enums import RiskBand, RiskBandDisclosurePurpose
from app.rating.values import DisclosureViewId
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS",
    "RISK_BAND_WEB_COPY",
    "DisclosurePostActionContext",
    "RiskBandDisclosurePageContext",
    "RiskBandDisclosureRouteContract",
    "disclosure_snapshot_path",
    "get_risk_band_web_copy",
)


@dataclass(frozen=True, slots=True)
class RiskBandDisclosureRouteContract:
    name: str
    method: str
    path: str
    form_fields: tuple[str, ...]
    cache_control: str = "no-store"
    same_origin_only: bool = True


RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS: Final = (
    RiskBandDisclosureRouteContract(
        name="shop_risk_band_disclosure_create",
        method="POST",
        path="/shop/customers/{shop_customer_id}/risk-band-disclosures",
        form_fields=("purpose", "idempotency_key", "csrf_token"),
    ),
    RiskBandDisclosureRouteContract(
        name="shop_risk_band_disclosure_view",
        method="GET",
        path="/shop/risk-band-disclosures/{disclosure_view_id}",
        form_fields=(),
    ),
)


@dataclass(frozen=True, slots=True, repr=False)
class DisclosurePostActionContext:
    shop_customer_id: ShopCustomerId = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Disclosure POST action ShopCustomer is invalid")

    def same_origin_post_path(self) -> str:
        return (
            f"/shop/customers/{self.shop_customer_id.as_uuid()}/risk-band-disclosures"
        )

    def __repr__(self) -> str:
        return "DisclosurePostActionContext(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RiskBandDisclosurePageContext:
    """Safe snapshot plus the sole server-side refresh capability."""

    projection: RiskBandDisclosureProjection
    action: DisclosurePostActionContext | None = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.projection, RiskBandDisclosureProjection):
            raise ValueError("Disclosure page projection is invalid")
        if self.action is not None and not isinstance(
            self.action, DisclosurePostActionContext
        ):
            raise ValueError("Disclosure page action is invalid")

    def __repr__(self) -> str:
        return "RiskBandDisclosurePageContext(<safe>, action=<redacted>)"


def disclosure_snapshot_path(disclosure_view_id: DisclosureViewId) -> str:
    if not isinstance(disclosure_view_id, DisclosureViewId):
        raise ValueError("Disclosure snapshot locator is invalid")
    return f"/shop/risk-band-disclosures/{disclosure_view_id.as_path_segment()}"


_UZ_LATN_COPY = MappingProxyType(
    {
        "band_new": "Yangi",
        "band_green": "Yashil",
        "band_yellow": "Sariq",
        "band_red": "Qizil",
        "band_blocked": "Bloklangan",
        "purpose_debt_proposal_review": "Qarz taklifini ko‘rib chiqish",
        "purpose_credit_limit_review": "Kredit limitini ko‘rib chiqish",
        "purpose_existing_debt_review": "Mavjud qarzni ko‘rib chiqish",
        "viewed_at": "Ko‘rish vaqti",
        "historical_notice": "Band ko‘rish vaqtida olingan.",
        "new_view": "Yangi ko‘rishni yaratish",
        "page_title": "Risk bandi ko‘rinishi",
        "purpose_label": "Ko‘rish maqsadi",
        "band_label": "Risk bandi",
        "generic_error": "Risk bandini ko‘rish hozir mavjud emas.",
    }
)
_RU_COPY = MappingProxyType(
    {
        "band_new": "Новый",
        "band_green": "Зелёный",
        "band_yellow": "Жёлтый",
        "band_red": "Красный",
        "band_blocked": "Заблокирован",
        "purpose_debt_proposal_review": "Проверка предложения долга",
        "purpose_credit_limit_review": "Проверка кредитного лимита",
        "purpose_existing_debt_review": "Проверка существующего долга",
        "viewed_at": "Время просмотра",
        "historical_notice": "Рейтинг зафиксирован на момент просмотра.",
        "new_view": "Создать новый просмотр",
        "page_title": "Просмотр группы риска",
        "purpose_label": "Цель просмотра",
        "band_label": "Группа риска",
        "generic_error": "Просмотр группы риска сейчас недоступен.",
    }
)
RISK_BAND_WEB_COPY: Final[Mapping[DebtWebLanguage, Mapping[str, str]]] = (
    MappingProxyType(
        {
            DebtWebLanguage.UZ_LATN: _UZ_LATN_COPY,
            DebtWebLanguage.RU: _RU_COPY,
        }
    )
)


def get_risk_band_web_copy(language: DebtWebLanguage) -> Mapping[str, str]:
    if not isinstance(language, DebtWebLanguage):
        raise ValueError("Risk-band web language is invalid")
    copy = RISK_BAND_WEB_COPY[language]
    expected_keys = {
        *(f"band_{band.value}" for band in RiskBand),
        *(f"purpose_{purpose.value}" for purpose in RiskBandDisclosurePurpose),
        "viewed_at",
        "historical_notice",
        "new_view",
        "page_title",
        "purpose_label",
        "band_label",
        "generic_error",
    }
    if set(copy) != expected_keys:
        raise RuntimeError("Risk-band web copy is incomplete")
    return copy
