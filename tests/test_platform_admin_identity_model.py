from sqlalchemy import Boolean

from app.auth.models import User
from app.shop.models import ShopStaff


def test_platform_admin_identity_is_explicit_non_null_and_false_by_default() -> None:
    column = User.__table__.c.is_platform_admin

    assert isinstance(column.type, Boolean)
    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg is False
    assert column.server_default is not None
    assert str(column.server_default.arg) == "false"


def test_platform_admin_identity_has_no_tenant_role_foreign_key() -> None:
    column = User.__table__.c.is_platform_admin

    assert not column.foreign_keys
    assert "role" not in User.__table__.c
    assert "shop_id" not in User.__table__.c
    assert "is_platform_admin" not in ShopStaff.__table__.c
