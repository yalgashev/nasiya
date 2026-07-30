import os
import re
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "deploy/minio-backup-restore-exercise.sh"
RUNBOOK = PROJECT_ROOT / "docs/m8_storage_runbook.md"
DEFAULT_ROOT_USER = "local-minio-root"
DEFAULT_ROOT_PASSWORD = "change-me-local-minio-root-secret-at-least-32-chars"
DEFAULT_BUCKET = "nasiya-private"
CI_EVIDENCE_ENV = "M8_STORAGE_BACKUP_EVIDENCE_FILE"
CI_EVIDENCE_PREFIX = "nasiya-storage-backup-evidence."
SAFE_SUCCESS = (
    "STORAGE_BACKUP_RESTORE_PASS "
    "source=1 backup=1 restored=1 "
    "checksum=VERIFIED privacy=PRIVATE"
)
SAFE_EVIDENCE_ERROR = "STORAGE_BACKUP_RESTORE_FAILED code=EVIDENCE"
SAFE_OUTPUT_ERROR = "STORAGE_BACKUP_RESTORE_FAILED code=OUTPUT"
ROOT_ONLY_ENV = (
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "M8_MINIO_ROOT_ENV_FILE",
)


class _CIEvidenceError(AssertionError):
    pass


def _fail_ci_evidence() -> None:
    raise _CIEvidenceError(SAFE_EVIDENCE_ERROR) from None


def _validate_ci_evidence(environment: Mapping[str, str]) -> None:
    if any(name in environment for name in ROOT_ONLY_ENV):
        _fail_ci_evidence()

    raw_evidence_path = environment.get(CI_EVIDENCE_ENV)
    raw_runner_temp = environment.get("RUNNER_TEMP")
    if not raw_evidence_path or not raw_runner_temp:
        _fail_ci_evidence()

    evidence_path = Path(raw_evidence_path)
    runner_temp = Path(raw_runner_temp)
    try:
        resolved_runner_temp = runner_temp.resolve(strict=True)
        resolved_parent = evidence_path.parent.resolve(strict=True)
    except OSError:
        _fail_ci_evidence()
    if (
        not evidence_path.is_absolute()
        or resolved_parent != resolved_runner_temp
        or not evidence_path.name.startswith(CI_EVIDENCE_PREFIX)
    ):
        _fail_ci_evidence()

    expected_bytes = f"{SAFE_SUCCESS}\n".encode()
    if evidence_path.is_symlink():
        _fail_ci_evidence()
    try:
        descriptor = os.open(
            evidence_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        _fail_ci_evidence()
    try:
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_uid != os.getuid()
            or file_stat.st_nlink != 1
            or file_stat.st_size != len(expected_bytes)
        ):
            _fail_ci_evidence()
        evidence = os.read(descriptor, len(expected_bytes) + 1)
    except OSError:
        _fail_ci_evidence()
    finally:
        os.close(descriptor)
    if evidence != expected_bytes:
        _fail_ci_evidence()
    forbidden_markers = (
        b"MINIO_",
        b"http://",
        b"https://",
        b"s3://",
        b"bucket=",
        b"key=",
        b"v1/objects/",
    )
    if any(marker in evidence for marker in forbidden_markers) or re.search(
        rb"\b[0-9a-fA-F]{64}\b",
        evidence,
    ):
        _fail_ci_evidence()


def test_backup_restore_script_and_runbook_are_safe_and_complete(
    tmp_path: Path,
) -> None:
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

    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    evidence_path = runner_temp / f"{CI_EVIDENCE_PREFIX}contract"
    valid_environment = {
        CI_EVIDENCE_ENV: str(evidence_path),
        "RUNNER_TEMP": str(runner_temp),
    }
    with pytest.raises(_CIEvidenceError, match=SAFE_EVIDENCE_ERROR):
        _validate_ci_evidence({})
    evidence_path.write_text("malformed evidence\n", encoding="utf-8")
    evidence_path.chmod(0o600)
    with pytest.raises(_CIEvidenceError, match=SAFE_EVIDENCE_ERROR):
        _validate_ci_evidence(valid_environment)
    evidence_path.write_text(f"{SAFE_SUCCESS}\n", encoding="utf-8")
    evidence_path.chmod(0o640)
    with pytest.raises(_CIEvidenceError, match=SAFE_EVIDENCE_ERROR):
        _validate_ci_evidence(valid_environment)
    evidence_path.chmod(0o600)
    with pytest.raises(_CIEvidenceError, match=SAFE_EVIDENCE_ERROR):
        _validate_ci_evidence(
            {
                **valid_environment,
                "MINIO_ROOT_USER": "must-not-reach-full-pytest",
            }
        )
    _validate_ci_evidence(valid_environment)


@pytest.mark.integration
def test_real_minio_backup_restore_exercise_is_sanitized_and_cleans_temp_data() -> None:
    environment = os.environ.copy()
    if environment.get("GITHUB_ACTIONS") == "true":
        _validate_ci_evidence(environment)
        return

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
    if (
        completed.returncode != 0
        or completed.stderr != ""
        or completed.stdout != f"{SAFE_SUCCESS}\n"
        or before_temp_dirs != after_temp_dirs
    ):
        pytest.fail(SAFE_OUTPUT_ERROR, pytrace=False)
    hidden_values = (
        endpoint,
        root_user,
        root_password,
        bucket,
        "v1/objects/",
        "nasiya-backup-source-",
        "nasiya-restore-",
    )
    rendered = f"{completed.stdout} {completed.stderr}"
    if any(hidden in rendered for hidden in hidden_values):
        pytest.fail(SAFE_OUTPUT_ERROR, pytrace=False)
