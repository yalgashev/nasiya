from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIO_IMAGE = (
    "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z@"
    "sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
)
MC_IMAGE = (
    "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z@"
    "sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
)


def _workflow() -> str:
    return (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )


def test_ci_keeps_one_dependency_sync_job_and_existing_gates() -> None:
    workflow = _workflow()
    jobs = workflow.split("jobs:", 1)[1]

    assert jobs.count("\n  dependency-sync:") == 1
    assert "\n  build:" not in jobs
    assert "\n  minio:" not in jobs
    assert "Run Alembic migrations" in workflow
    assert 'test "$current_revision" = "f8a9b0c1d2e3"' in workflow
    assert "Run Ruff" in workflow
    assert "Run M5 containment guard" in workflow
    assert "Run M6 Telegram fake-runtime guard" in workflow
    assert "Run M7 OTP containment and fake-runtime guard" in workflow
    assert "Run full pytest" in workflow
    assert "(skipped|xfailed|xpassed)" in workflow


def test_credentials_are_generated_and_masked_before_runtime_use() -> None:
    workflow = _workflow()
    mask_step = workflow.index("Generate and mask MinIO test credentials")
    start_step = workflow.index("Start bounded pinned MinIO runtime")
    init_step = workflow.index("Initialize private MinIO policy twice")

    assert mask_step < start_step < init_step
    assert "openssl rand -hex 32" in workflow
    assert "openssl rand -hex 8" in workflow
    assert 'echo "::add-mask::$masked_value"' in workflow
    for variable in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_APP_ACCESS_KEY",
        "MINIO_APP_SECRET_KEY",
    ):
        assert f'echo "{variable}=$' in workflow
        assert f"--env {variable}" in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "AWS_SECRET_ACCESS_KEY" not in workflow


def test_ci_runtime_is_pinned_bounded_and_health_checked() -> None:
    workflow = _workflow()

    assert MINIO_IMAGE in workflow
    assert MC_IMAGE in workflow
    assert "--publish 127.0.0.1:9000:9000" in workflow
    assert '--health-cmd "mc ready local"' in workflow
    assert 'while [ "$attempt" -le 30 ]' in workflow
    assert "sleep 1" in workflow
    assert "MinIO healthcheck timed out." in workflow
    assert "--network host" in workflow
    assert "run_minio_init()" in workflow
    assert "          run_minio_init\n          run_minio_init\n" in workflow


def test_ci_runs_narrow_matrix_then_full_suite_and_always_cleans_up() -> None:
    workflow = _workflow()
    narrow = workflow.index("Run narrow private MinIO integration")
    full = workflow.index("Run full pytest")
    cleanup = workflow.index("Clean up MinIO test runtime")

    assert narrow < full < cleanup
    assert "uv run pytest -q tests/test_storage_minio_integration.py" in workflow
    assert "if: always()" in workflow[cleanup:]
    assert "docker rm --force nasiya-ci-minio" in workflow[cleanup:]
    assert 'docker volume rm "$M8_MINIO_CI_VOLUME"' in workflow[cleanup:]


def test_ci_does_not_print_generated_credentials_or_provider_details() -> None:
    workflow = _workflow()

    assert "set -x" not in workflow
    assert "printenv" not in workflow
    assert "docker logs" not in workflow
    assert "env |" not in workflow
    assert "curl -v" not in workflow
    assert "pytest -s" not in workflow
