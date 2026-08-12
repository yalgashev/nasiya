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
    assert 'test "$current_revision" = "c7d8e9f0a1b2"' in workflow
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
    admin_step = workflow.index("Run admin-plane MinIO backup restore acceptance")
    narrow_step = workflow.index("Run narrow private MinIO integration")
    credential_step = workflow[mask_step:start_step]

    assert mask_step < start_step < init_step < admin_step < narrow_step
    assert "openssl rand -hex 32" in workflow
    assert "openssl rand -hex 8" in workflow
    assert 'echo "::add-mask::$masked_value"' in workflow
    for variable in ("MINIO_APP_ACCESS_KEY", "MINIO_APP_SECRET_KEY"):
        assert f'echo "{variable}=$' in workflow
        assert f"--env {variable}" in workflow
    assert 'echo "MINIO_ROOT_USER=$minio_root_user"' in credential_step
    assert 'echo "MINIO_ROOT_PASSWORD=$minio_root_password"' in credential_step
    assert "umask 077" in credential_step
    assert 'mktemp "$RUNNER_TEMP/nasiya-minio-root.XXXXXX"' in credential_step
    assert '} >"$minio_root_env_file"' in credential_step
    assert 'chmod 600 "$minio_root_env_file"' in credential_step
    github_env_values = credential_step.split(
        '} >"$minio_root_env_file"',
        1,
    )[1]
    assert "MINIO_ROOT_USER=" not in github_env_values
    assert "MINIO_ROOT_PASSWORD=" not in github_env_values
    assert "M8_MINIO_ROOT_ENV_FILE=" not in github_env_values
    assert 'echo "root_env_file=$minio_root_env_file" >>"$GITHUB_OUTPUT"' in workflow
    assert (
        workflow.count(
            "M8_MINIO_ROOT_ENV_FILE: "
            "${{ steps.minio-credentials.outputs.root_env_file }}"
        )
        == 4
    )
    assert workflow.count('--env-file "$M8_MINIO_ROOT_ENV_FILE"') == 2
    assert "--env MINIO_ROOT_USER" not in workflow
    assert "--env MINIO_ROOT_PASSWORD" not in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "AWS_SECRET_ACCESS_KEY" not in workflow

    init_slice = workflow[init_step:admin_step]
    admin_slice = workflow[admin_step:narrow_step]
    assert 'rm -f -- "$M8_MINIO_ROOT_ENV_FILE"' not in init_slice
    assert "trap cleanup_admin EXIT" in admin_slice
    assert '. "$M8_MINIO_ROOT_ENV_FILE"' in admin_slice
    assert (
        "deploy/minio-backup-restore-exercise.sh "
        '\\\n            >"$stdout_file" 2>"$stderr_file"'
    ) in admin_slice
    assert "unset MINIO_ROOT_USER MINIO_ROOT_PASSWORD" in admin_slice
    assert 'rm -f -- "$M8_MINIO_ROOT_ENV_FILE"' in admin_slice
    assert (
        "printf 'M8_STORAGE_BACKUP_EVIDENCE_FILE=%s\\n' "
        '\\\n            "$evidence_file" >>"$GITHUB_ENV"'
    ) in admin_slice
    for line in workflow.splitlines():
        if "GITHUB_ENV" in line:
            assert "MINIO_ROOT_USER" not in line
            assert "MINIO_ROOT_PASSWORD" not in line
            assert "M8_MINIO_ROOT_ENV_FILE" not in line


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
    init = workflow.index("Initialize private MinIO policy twice")
    admin = workflow.index("Run admin-plane MinIO backup restore acceptance")
    narrow = workflow.index("Run narrow private MinIO integration")
    full = workflow.index("Run full pytest")
    cleanup = workflow.index("Clean up MinIO test runtime")

    assert init < admin < narrow < full < cleanup
    assert "uv run pytest -q tests/test_storage_minio_integration.py" in workflow
    admin_slice = workflow[admin:narrow]
    assert "M8_STORAGE_BACKUP_EVIDENCE_FILE" in admin_slice
    assert "STORAGE_BACKUP_RESTORE_PASS" in admin_slice
    assert "if: always()" in workflow[cleanup:]
    assert "docker rm --force nasiya-ci-minio" in workflow[cleanup:]
    assert 'docker volume rm "$M8_MINIO_CI_VOLUME"' in workflow[cleanup:]
    assert 'rm -f -- "$M8_MINIO_ROOT_ENV_FILE"' in workflow[cleanup:]
    assert 'rm -f -- "$M8_STORAGE_BACKUP_EVIDENCE_FILE"' in workflow[cleanup:]


def test_root_credentials_are_not_inherited_by_application_test_steps() -> None:
    workflow = _workflow()
    start = workflow.index("Run narrow private MinIO integration")
    cleanup = workflow.index("Clean up MinIO test runtime")
    application_steps = workflow[start:cleanup]

    assert "MINIO_ROOT_USER" not in application_steps
    assert "MINIO_ROOT_PASSWORD" not in application_steps
    assert "M8_MINIO_ROOT_ENV_FILE" not in application_steps
    assert "nasiya-minio-root." not in application_steps
    assert "--deselect" not in application_steps


def test_ci_does_not_print_generated_credentials_or_provider_details() -> None:
    workflow = _workflow()

    assert "set -x" not in workflow
    assert "printenv" not in workflow
    assert "docker logs" not in workflow
    assert "env |" not in workflow
    assert "curl -v" not in workflow
    assert "pytest -s" not in workflow
