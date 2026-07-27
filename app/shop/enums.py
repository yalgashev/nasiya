from enum import StrEnum


class ShopRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    CASHIER = "cashier"


class ShopStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class ShopStatusAction(StrEnum):
    ACTIVATED = "activated"
    SUSPENDED = "suspended"
    REACTIVATED = "reactivated"


class ShopStaffAction(StrEnum):
    ADDED = "added"
    ROLE_CHANGED = "role_changed"
    REVOKED = "revoked"
