from pathlib import Path

from app.debt.enums import M15_PERSISTED_STATUSES, DebtStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]

M15_RUNTIME_AND_PERSISTENCE = (
    "app/debt/overdue_ports.py",
    "app/debt/rating_ports.py",
    "app/debt/overdue_targeting.py",
    "app/debt/overdue_service.py",
    "app/debt/creation_eligibility.py",
    "app/debt/customer_accept_service.py",
    "app/payment/commands.py",
    "app/payment/contracts.py",
    "app/payment/repository.py",
    "app/payment/read_service.py",
    "app/payment/service.py",
    "app/payment/router.py",
    "app/payment/rating_ports.py",
    "alembic/versions/b6c7d8e9f0a1_add_overdue_persistence.py",
)


def _source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_m17_written_off_extension_is_exact_but_absent_from_m15_revision() -> None:
    assert DebtStatus.WRITTEN_OFF not in M15_PERSISTED_STATUSES
    assert DebtStatus.WRITTEN_OFF_SETTLED not in M15_PERSISTED_STATUSES

    vocabulary_paths = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "app").rglob("*.py")
        if "written_off" in path.read_text(encoding="utf-8").casefold()
    }
    assert vocabulary_paths == {
        "app/audit/contracts.py",
        "app/audit/models.py",
        "app/audit/redaction.py",
        "app/audit/repository.py",
        "app/debt/admin_write_off_presentation.py",
        "app/debt/contracts.py",
        "app/debt/enums.py",
        "app/debt/models.py",
        "app/debt/payment_progress.py",
        "app/debt/policy.py",
        "app/debt/rating_ports.py",
        "app/debt/repository.py",
        "app/debt/router.py",
        "app/debt/web_presentation.py",
        "app/debt/write_off_core.py",
        "app/debt/write_off_service.py",
        "app/debt/write_off_targeting.py",
        "app/payment/contracts.py",
        "app/payment/policy.py",
        "app/payment/presentation.py",
        "app/payment/rating_ports.py",
        "app/payment/read_service.py",
        "app/payment/router.py",
        "app/payment/service.py",
        "app/payment/values.py",
        "app/rating/adapters.py",
        "app/rating/contracts.py",
        "app/rating/current_read_service.py",
        "app/rating/enums.py",
        "app/rating/models.py",
        "app/rating/service.py",
    }

    assert (
        "written_off"
        not in _source(
            "alembic/versions/b6c7d8e9f0a1_add_overdue_persistence.py"
        ).casefold()
    )


def test_future_integrations_are_absent_from_new_m15_runtime_surfaces() -> None:
    runtime_parts = []
    for path in M15_RUNTIME_AND_PERSISTENCE:
        source = _source(path).casefold()
        if path == "app/payment/commands.py":
            # M18 extends this closed parsing/command boundary only.  The
            # producer remains forbidden until its later checkpoint.
            for approved_symbol in (
                "create_void_payment_request_hash_v1",
                "assemble_void_payment_command",
            ):
                source = source.replace(approved_symbol, "")
        runtime_parts.append(source)
    runtime = "\n".join(runtime_parts)
    for forbidden_wiring in (
        "from app.rating",
        "import app.rating",
        "rating.",
        "from app.notification",
        "import app.notification",
        "notification.",
        "from app.scheduler",
        "import app.scheduler",
        "scheduler.",
        "job_run",
        "void_payment",
        "reverse_payment",
        "clawback_reversal",
        "clawback_reversed",
        "cached_balance",
        "cached_exposure",
    ):
        assert forbidden_wiring not in runtime


def test_payment_route_surface_has_no_new_trigger_admin_or_api_endpoint() -> None:
    router = _source("app/payment/router.py")
    assert router.count("@router.get(") == 5
    assert router.count("@router.post(") == 1
    assert "@router.put(" not in router
    assert "@router.patch(" not in router
    assert "@router.delete(" not in router
    for forbidden_path in ('"/admin', '"/api', '"/overdue'):
        assert forbidden_path not in router


def test_payment_templates_do_not_render_internal_markers_or_request_material() -> None:
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "app/templates/payment").glob("*.html"))
    ).casefold()
    for forbidden in (
        "overdue_at",
        "overdue_revision",
        "request_hash",
        "actor_user_id",
        "customer_id",
        "shop_customer_id",
    ):
        assert forbidden not in templates


def test_inherited_m13_m14_source_scoped_guards_remain_present() -> None:
    retained_guards = (
        (
            "tests/test_m12_combined_lock_order.py",
            "test_closed_prephases_and_lock_paths_have_no_correctness_workaround",
        ),
        (
            "tests/test_m14_out_containment.py",
            "test_payment_runtime_has_no_out_process_or_external_io_dependency",
        ),
        (
            "tests/test_m14_combined_lock_order_postgresql.py",
            "test_m13_m14_shared_paths_have_one_forward_order_and_append_tail",
        ),
        (
            "tests/test_payment_idempotency_contracts.py",
            "create_payment_request_hash",
        ),
    )
    for relative_path, required_symbol in retained_guards:
        path = PROJECT_ROOT / relative_path
        assert path.is_file()
        assert required_symbol in path.read_text(encoding="utf-8")
