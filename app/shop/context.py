from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth.models import Session as AuthSession
from app.auth.sessions import clear_session_active_shop_id, set_session_active_shop_id
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop.repository import get_active_staff, get_shop, list_user_active_staff
from app.shop.values import ShopId, ShopStaffId, UserId


@dataclass(frozen=True)
class CurrentShopContext:
    shop: Shop | None
    staff_id: ShopStaffId | None
    role: ShopRole | None
    status: ShopStatus | None

    @property
    def is_selected(self) -> bool:
        return self.shop is not None


def resolve_current_shop(
    session: Session,
    *,
    auth_session: AuthSession,
    user_id: UserId,
) -> CurrentShopContext:
    if auth_session.active_shop_id is not None:
        shop_id = ShopId(auth_session.active_shop_id)
        staff = get_active_staff(session, shop_id=shop_id, user_id=user_id)
        if staff is None:
            clear_session_active_shop_id(session, auth_session)
            return _unselected_context()

        shop = get_shop(session, shop_id=shop_id)
        if shop is None:
            clear_session_active_shop_id(session, auth_session)
            return _unselected_context()
        return _selected_context(shop=shop, staff=staff)

    memberships = list_user_active_staff(session, user_id=user_id)
    if len(memberships) != 1:
        return _unselected_context()

    staff, shop = memberships[0]
    set_session_active_shop_id(session, auth_session, shop_id=shop.id)
    return _selected_context(shop=shop, staff=staff)


def _selected_context(*, shop: Shop, staff: ShopStaff) -> CurrentShopContext:
    return CurrentShopContext(
        shop=shop,
        staff_id=ShopStaffId(staff.id),
        role=ShopRole(staff.role),
        status=ShopStatus(shop.status),
    )


def _unselected_context() -> CurrentShopContext:
    return CurrentShopContext(
        shop=None,
        staff_id=None,
        role=None,
        status=None,
    )
