import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "deploy/minio-backup-restore-exercise.sh"
RUNBOOK = PROJECT_ROOT / "docs/m8_storage_runbook.md"
DEFAULT_ROOT_USER = "local-minio-root"
DEFAULT_ROOT_PASSWORD = "change-me-local-minio-root-secret-at-least-32-chars"
DEFAULT_BUCKET = "nasiya-private"


def test_backup_restore_script_and_runbook_are_safe_and_complete() -> None:
    script = SCRIPT.read_text()
    runbook = RUNBOOK.read_text()

    syntax = subprocess.run(
        ["sh", "-n", str(SCRIPT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0
    assert os.access(SCRIPT, os.X_OK)
    assert script.count("run_quiet mirror") == 2
    assert "mktemp -d" in script
    assert "anonymous set private" in script
    assert script.count("anonymous get") == 2
    assert "checksum-sha256" in script
    assert "cmp -s" in script
    assert 'find "$exercise_dir" -mindepth 1 -delete' in script
    assert 'rmdir "$exercise_dir"' in script
    assert "set -x" not in script
    assert "down -v" not in script
    assert "MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD" not in script
    assert "docker compose down -v" in runbook
    assert "Never run" in runbook
    assert "production backup design" in runbook
    assert "RPO" in runbook
    assert "RTO" in runbook
    assert "STORAGE_BACKUP_RESTORE_PASS" in runbook


@pytest.mark.integration
def test_real_minio_backup_restore_exercise_is_sanitized_and_cleans_temp_data() -> None:
    environment = os.environ.copy()
    if environment.get("GITHUB_ACTIONS") == "true":
        endpoint = environment.get(
            "M8_MINIO_TEST_ENDPOINT",
            "http://127.0.0.1:9000",
        )
        network = "host"
    else:
        endpoint = "http://minio:9000"
        network = "nasiya_default"
    root_user = environment.get("MINIO_ROOT_USER", DEFAULT_ROOT_USER)
    root_password = environment.get(
        "MINIO_ROOT_PASSWORD",
        DEFAULT_ROOT_PASSWORD,
    )
    bucket = environment.get("MINIO_BUCKET", DEFAULT_BUCKET)
    environment.update(
        {
            "MINIO_ENDPOINT": endpoint,
            "MINIO_ROOT_USER": root_user,
            "MINIO_ROOT_PASSWORD": root_password,
            "MINIO_BUCKET": bucket,
            "MINIO_DOCKER_NETWORK": network,
            "M8_STORAGE_BACKUP_PARENT": str(PROJECT_ROOT),
        }
    )
    before_temp_dirs = set(PROJECT_ROOT.glob(".m8-storage-backup.*"))

    completed = subprocess.run(
        [str(SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    after_temp_dirs = set(PROJECT_ROOT.glob(".m8-storage-backup.*"))
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert re.fullmatch(
        (
            r"STORAGE_BACKUP_RESTORE_PASS "
            r"source=(\d+) backup=\1 restored=\1 "
            r"checksum=VERIFIED privacy=PRIVATE\n"
        ),
        completed.stdout,
    )
    assert before_temp_dirs == after_temp_dirs
    for hidden in (
        endpoint,
        root_user,
        root_password,
        bucket,
        "v1/objects/",
        "nasiya-backup-source-",
        "nasiya-restore-",
    ):
        assert hidden not in f"{completed.stdout} {completed.stderr}"
