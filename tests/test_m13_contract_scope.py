import ast
from pathlib import Path

PACKAGE_ROOTS = (Path("app/debt"), Path("app/idempotency"))
FORBIDDEN_RUNTIME_IMPORTS = (
    "fastapi",
    "starlette",
    "app.payment",
    "app.payments",
    "app.rating",
    "app.notification",
    "app.notifications",
    "app.scheduler",
    "app.storage",
    "app.customer_identity",
)
PERSISTENCE_OR_TRANSPORT_FILES = {
    "service.py",
    "router.py",
}


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def test_m13_model_checkpoint_has_only_models_and_no_transport_or_out_imports() -> None:
    python_files = tuple(
        path for root in PACKAGE_ROOTS for path in sorted(root.glob("*.py"))
    )
    assert python_files
    assert PERSISTENCE_OR_TRANSPORT_FILES.isdisjoint(path.name for path in python_files)
    for path in python_files:
        for imported in _imports(path):
            assert not imported.startswith(FORBIDDEN_RUNTIME_IMPORTS), (
                f"{path} imports out-of-scope runtime module {imported}"
            )
