"""Semantic identifiers for shop domain service signatures.

These NewType aliases do not perform runtime validation. They exist for
documentation and future static checking; real protection comes from
keyword-only service signatures and integration tests.
"""

from typing import NewType
from uuid import UUID

ShopId = NewType("ShopId", UUID)
ShopStaffId = NewType("ShopStaffId", UUID)
UserId = NewType("UserId", UUID)
