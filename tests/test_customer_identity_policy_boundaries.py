import ast
from pathlib import Path

POLICY_FILES = (
    Path("app/customer_identity/contracts.py"),
    Path("app/customer_document/contracts.py"),
)
FORBIDDEN_IMPORT_PREFIXES = (
    "sqlalchemy",
    "app.storage",
    "app.auth",
    "app.offers",
    "app.otp",
    "app.telegram",
)


def test_completeness_contract_modules_have_no_outer_layer_imports() -> None:
    imported_modules: set[str] = set()
    for path in POLICY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)

    assert not any(
        module.startswith(prefix)
        for module in imported_modules
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )
