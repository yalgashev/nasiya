from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHOP_CUSTOMER_DIR = PROJECT_ROOT / "app" / "shop_customer"


def test_borrowed_session_layers_never_own_full_lifecycle() -> None:
    for filename in ("repository.py", "service.py", "targeting.py"):
        source = (SHOP_CUSTOMER_DIR / filename).read_text(encoding="utf-8")
        assert ".commit(" not in source
        assert ".rollback(" not in source
        assert ".close(" not in source
    repository = (SHOP_CUSTOMER_DIR / "repository.py").read_text(encoding="utf-8")
    assert repository.count("begin_nested()") == 1
    assert "SHOP_CUSTOMER_PAIR_CONSTRAINT" in repository


def test_router_keeps_closed_prephases_and_domain_transactions_separate() -> None:
    router = (SHOP_CUSTOMER_DIR / "router.py").read_text(encoding="utf-8")
    dependencies = (SHOP_CUSTOMER_DIR / "dependencies.py").read_text(encoding="utf-8")
    rate = (SHOP_CUSTOMER_DIR / "rate_limit.py").read_text(encoding="utf-8")
    assert "get_detached_shop_customer_authority" in router
    assert "record_shop_customer_link_attempt" in router
    assert "coordinate_link_active_customer" in router
    assert "with session_factory.begin()" in dependencies
    assert "with session_factory.begin()" in rate
    assert ".commit(" not in router
    assert ".rollback(" not in router
    assert ".close(" not in router


def test_m12_production_does_not_mutate_inherited_lifecycle_modules() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SHOP_CUSTOMER_DIR.glob("*.py"))
    )
    for forbidden in (
        "transition_existing_own_customer_draft_to_active",
        "unlink_verified_private_chat",
        "accept_registration_offer",
        "encrypt_customer_identity",
        "ingest_sanitized_image",
        "create_debt",
        "create_payment",
    ):
        assert forbidden not in source
