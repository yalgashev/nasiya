from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT_ROOT / "app" / "shop_customer" / "repository.py"
ROUTER = PROJECT_ROOT / "app" / "shop_customer" / "router.py"


def test_every_shop_customer_select_has_exact_tenant_or_own_scope() -> None:
    repository = REPOSITORY.read_text(encoding="utf-8")
    router = ROUTER.read_text(encoding="utf-8")
    assert repository.count("select(ShopCustomer)") == 5
    assert repository.count("ShopCustomer.shop_id ==") == 4
    assert repository.count("ShopCustomer.customer_id ==") == 2
    assert router.count("select(ShopCustomer, User.phone)") == 1
    roster = router[
        router.index("def _list_masked_roster(") : router.index(
            "def _list_own_shop_names("
        )
    ]
    assert "ShopCustomer.shop_id == shop_id" in roster
    assert ".where(ShopCustomer.customer_id" not in roster


def test_production_has_no_unscoped_primary_key_shop_customer_get() -> None:
    production = tuple((PROJECT_ROOT / "app").rglob("*.py"))
    source = "\n".join(path.read_text(encoding="utf-8") for path in production)
    assert "get(ShopCustomer," not in source
    assert "session.get(ShopCustomer" not in source
    assert "db.get(ShopCustomer" not in source
