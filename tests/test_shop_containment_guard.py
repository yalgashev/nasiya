import ast
from dataclasses import dataclass
from pathlib import Path

TARGET_MODEL_NAMES = frozenset(
    {
        "Shop",
        "ShopStaff",
        "ShopStatusEvent",
        "ShopStaffEvent",
    }
)
CORE_QUERY_FUNCTIONS = frozenset({"select", "insert", "update", "delete"})
SELECT_CHAIN_METHODS = frozenset(
    {
        "filter",
        "filter_by",
        "join",
        "outerjoin",
        "order_by",
        "select_from",
        "where",
    }
)
ORM_INSERT_METHODS = frozenset({"add", "add_all", "bulk_save_objects"})
ORM_DELETE_METHODS = frozenset({"delete"})
DIRECT_SHOP_QUERY_ALLOWLIST = {
    "app/shop/repository.py": "Canonical shop persistence boundary.",
}


@dataclass(frozen=True)
class TargetRefs:
    name_aliases: dict[str, str]
    module_aliases: set[str]


@dataclass(frozen=True)
class DirectShopQueryViolation:
    path: Path
    lineno: int
    operation: str
    models: tuple[str, ...]

    def render(self) -> str:
        models = ", ".join(self.models)
        return f"{self.path}:{self.lineno}: direct {self.operation} on {models}"


def test_direct_shop_query_boundary_guard_is_green_for_app() -> None:
    """Bu IDOR himoyasi emas; bu modul boundary regressiya testi.

    Tenant xavfsizligi service va HTTP cross-shop testlari bilan isbotlanadi.
    """
    violations = [
        violation
        for path in _iter_scanned_app_files()
        for violation in _find_direct_shop_query_violations(path)
    ]

    assert violations == [], "\n".join(violation.render() for violation in violations)


def test_direct_shop_query_boundary_guard_flags_intentional_bad_example() -> None:
    bad_source = """
from sqlalchemy import delete, insert, select, update
from app.shop.models import Shop as ShopModel, ShopStaff


def bad_queries(session, shop_id):
    session.execute(select(ShopModel).where(ShopModel.id == shop_id))
    session.execute(insert(ShopStaff).values(shop_id=shop_id))
    session.execute(update(ShopModel).where(ShopModel.id == shop_id))
    session.execute(delete(ShopStaff).where(ShopStaff.shop_id == shop_id))
"""

    violations = _find_violations_in_source(
        Path("app/not_shop_repository.py"),
        bad_source,
    )

    assert {violation.operation for violation in violations} == {
        "delete",
        "insert",
        "select",
        "update",
    }
    assert {model for violation in violations for model in violation.models} == {
        "Shop",
        "ShopStaff",
    }


def test_direct_shop_query_boundary_guard_flags_module_alias_example() -> None:
    bad_source = """
from sqlalchemy import select
from app.shop import models as shop_models


def bad_query(session):
    return session.scalars(select(shop_models.ShopStatusEvent))
"""

    violations = _find_violations_in_source(
        Path("app/bad_module_alias.py"),
        bad_source,
    )

    assert [violation.operation for violation in violations] == ["select"]
    assert violations[0].models == ("ShopStatusEvent",)


def test_direct_shop_query_boundary_guard_flags_orm_query_example() -> None:
    bad_source = """
from app.shop.models import ShopStaff


def bad_query(session):
    return session.query(ShopStaff).all()
"""

    violations = _find_violations_in_source(
        Path("app/bad_orm_query.py"),
        bad_source,
    )

    assert [violation.operation for violation in violations] == ["select"]
    assert violations[0].models == ("ShopStaff",)


def test_direct_shop_query_allowlist_entries_are_commented() -> None:
    assert DIRECT_SHOP_QUERY_ALLOWLIST == {
        "app/shop/repository.py": "Canonical shop persistence boundary.",
    }
    assert all(comment.strip() for comment in DIRECT_SHOP_QUERY_ALLOWLIST.values())


def _iter_scanned_app_files() -> list[Path]:
    paths = sorted(Path("app").rglob("*.py"))
    assert paths
    assert all(path.parts[0] == "app" for path in paths)
    return [
        path
        for path in paths
        if _relative_posix_path(path) not in DIRECT_SHOP_QUERY_ALLOWLIST
    ]


def _find_direct_shop_query_violations(path: Path) -> list[DirectShopQueryViolation]:
    return _find_violations_in_source(path, path.read_text())


def _find_violations_in_source(
    path: Path,
    source: str,
) -> list[DirectShopQueryViolation]:
    tree = ast.parse(source, filename=str(path))
    refs = _collect_target_refs(tree)
    if not refs.name_aliases and not refs.module_aliases:
        return []

    violations = [
        violation
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for violation in _violations_for_call(path, node, refs)
    ]
    return _deduplicate_violations(violations)


def _collect_target_refs(tree: ast.AST) -> TargetRefs:
    name_aliases: dict[str, str] = {}
    module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.shop.models":
                for alias in node.names:
                    if alias.name in TARGET_MODEL_NAMES:
                        name_aliases[alias.asname or alias.name] = alias.name
            if node.module == "app.shop":
                for alias in node.names:
                    if alias.name == "models":
                        module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.shop.models":
                    module_aliases.add(alias.asname or "app.shop.models")

    return TargetRefs(name_aliases=name_aliases, module_aliases=module_aliases)


def _violations_for_call(
    path: Path,
    call: ast.Call,
    refs: TargetRefs,
) -> list[DirectShopQueryViolation]:
    operation = _direct_core_operation(call)
    if operation is None:
        operation = _query_chain_operation(call)
    if operation is None:
        operation = _orm_mutation_operation(call)
    if operation is None:
        return []

    models = _target_models_in_node(call, refs)
    if not models:
        return []

    return [
        DirectShopQueryViolation(
            path=path,
            lineno=call.lineno,
            operation=operation,
            models=tuple(sorted(models)),
        )
    ]


def _direct_core_operation(call: ast.Call) -> str | None:
    function_name = _call_function_name(call)
    if function_name in CORE_QUERY_FUNCTIONS:
        return function_name
    if function_name == "query":
        return "select"
    return None


def _query_chain_operation(call: ast.Call) -> str | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    if call.func.attr not in SELECT_CHAIN_METHODS:
        return None
    root_operation = _query_chain_root_operation(call.func.value)
    if root_operation in CORE_QUERY_FUNCTIONS or root_operation == "query":
        return "select" if root_operation == "query" else root_operation
    return None


def _orm_mutation_operation(call: ast.Call) -> str | None:
    if not isinstance(call.func, ast.Attribute):
        return None

    method = call.func.attr
    if method in ORM_INSERT_METHODS or method == "bulk_insert_mappings":
        return "insert"
    if method == "bulk_update_mappings":
        return "update"
    if method in ORM_DELETE_METHODS:
        return "delete"
    return None


def _query_chain_root_operation(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, ast.Call):
        function_name = _call_function_name(current)
        if function_name in CORE_QUERY_FUNCTIONS or function_name == "query":
            return function_name

        if isinstance(current.func, ast.Attribute):
            current = current.func.value
            continue
        break
    return None


def _call_function_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _target_models_in_node(node: ast.AST, refs: TargetRefs) -> set[str]:
    models: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in refs.name_aliases:
            models.add(refs.name_aliases[child.id])
        elif isinstance(child, ast.Attribute):
            model_name = _model_name_from_attribute(child, refs)
            if model_name is not None:
                models.add(model_name)
    return models


def _model_name_from_attribute(
    node: ast.Attribute,
    refs: TargetRefs,
) -> str | None:
    if node.attr not in TARGET_MODEL_NAMES:
        return None

    path = _attribute_path(node)
    if ".".join(path[:-1]) == "app.shop.models":
        return node.attr
    if path[:-1] and path[-2] in refs.module_aliases:
        return node.attr
    return None


def _attribute_path(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [*_attribute_path(node.value), node.attr]
    return []


def _deduplicate_violations(
    violations: list[DirectShopQueryViolation],
) -> list[DirectShopQueryViolation]:
    seen: set[tuple[Path, int, str, tuple[str, ...]]] = set()
    unique: list[DirectShopQueryViolation] = []
    for violation in violations:
        key = (
            violation.path,
            violation.lineno,
            violation.operation,
            violation.models,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(violation)
    return unique


def _relative_posix_path(path: Path) -> str:
    if path.is_absolute():
        return path.relative_to(Path.cwd()).as_posix()
    return path.as_posix()
