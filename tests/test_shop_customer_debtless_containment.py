import ast
from pathlib import Path

from app.shop_customer.models import ShopCustomer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHOP_CUSTOMER_DIR = PROJECT_ROOT / "app" / "shop_customer"


def _production_paths() -> tuple[Path, ...]:
    return tuple(sorted(SHOP_CUSTOMER_DIR.glob("*.py")))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _assigned_attribute_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            for descendant in ast.walk(target):
                if isinstance(descendant, ast.Attribute):
                    names.add(descendant.attr)
    return names


def test_policy_projection_and_port_are_pure_and_have_no_persistence_or_pii() -> None:
    contracts = (SHOP_CUSTOMER_DIR / "contracts.py").read_text(encoding="utf-8")
    start = contracts.index("class DebtlessShopCustomerPolicyProjection")
    end = contracts.index("class ShopCustomerCreationSnapshot")
    projection_contracts = contracts[start:end]
    assert "class DebtlessShopCustomerPolicyProjection" in projection_contracts
    assert "class ShopCustomerPolicyReadPort" in projection_contracts
    for forbidden in (
        "sqlalchemy",
        "Session",
        "phone",
        "jshshir",
        "document",
        "telegram",
        "mute",
        "notification",
        "scheduler",
    ):
        assert forbidden.casefold() not in projection_contracts.casefold()


def test_m12_policy_surface_adds_no_debt_query_or_enforcement_capability() -> None:
    module_names = {path.stem for path in _production_paths()}
    assert module_names.isdisjoint(
        {
            "debt",
            "debts",
            "payment",
            "payments",
            "balance",
            "exposure",
            "installment",
            "rating",
            "notification",
            "scheduler",
        }
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in _production_paths())
    for forbidden_call in (
        "create_debt(",
        "list_debts(",
        "count_open_debts(",
        "calculate_exposure(",
        "enforce_credit_limit(",
        "create_payment(",
    ):
        assert forbidden_call not in source


def test_m12_sources_do_not_import_or_mutate_out_of_scope_foundations() -> None:
    imported = {
        module for path in _production_paths() for module in _imported_modules(path)
    }
    assert not {
        module
        for module in imported
        if module.startswith(
            (
                "app.customer_activation",
                "app.customer_identity",
                "app.customer_document",
                "app.storage",
            )
        )
    }
    telegram_imports = {
        module for module in imported if module.startswith("app.telegram")
    }
    assert telegram_imports == {
        "app.telegram.client_ip",
        "app.telegram.models",
        "app.telegram.repository",
    }
    assigned = {
        attribute
        for path in _production_paths()
        for attribute in _assigned_attribute_names(path)
    }
    assert assigned.isdisjoint(
        {
            "onboarding_status",
            "activated_at",
            "linked_at",
            "unlinked_at",
            "phone_verified_at",
            "telegram_chat_id",
            "object_lifecycle",
            "available_at",
            "deleted_at",
        }
    )


def test_one_pii_free_table_has_no_mute_or_debt_state_columns() -> None:
    assert set(ShopCustomer.__table__.columns).isdisjoint(
        {
            "mute",
            "muted_at",
            "debt",
            "balance",
            "exposure",
            "open_debt_count",
            "payment",
            "phone",
            "name",
            "jshshir",
            "document_number",
        }
    )
