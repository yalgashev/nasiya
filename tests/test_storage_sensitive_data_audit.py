import ast
import logging
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    ObjectReadAuthorizationRequest,
    SanitizedImage,
    SanitizedImageBytes,
    SanitizedImageMetadata,
)
from app.storage.models import ObjectFileStatus
from app.storage.repository import ClaimedObjectFile
from app.storage.service import IngestedImageResult, PreparedImageUpload
from app.telegram.client_ip import ResolvedClientIp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_ACTOR_ID = UUID("11111111-2222-4333-8444-555555555555")
SENSITIVE_OBJECT_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
SENSITIVE_IP = "203.0.113.199"
SENSITIVE_BUCKET = "m8-sensitive-audit-bucket"
SENSITIVE_KEY = "v1/objects/1234567890abcdef1234567890abcdef.png"
SENSITIVE_BYTES = b"m8-sensitive-sanitized-bytes"
SENSITIVE_CHECKSUM = sha256(SENSITIVE_BYTES).hexdigest()
SENSITIVE_PARENT = "m8-sensitive-domain-parent"
TEST_ONLY_CANARIES = (
    str(SENSITIVE_ACTOR_ID),
    str(SENSITIVE_OBJECT_ID),
    SENSITIVE_IP,
    SENSITIVE_BUCKET,
    SENSITIVE_KEY,
    SENSITIVE_BYTES.decode("ascii"),
    SENSITIVE_CHECKSUM,
    SENSITIVE_PARENT,
)


def _image() -> SanitizedImage:
    return SanitizedImage(
        metadata=SanitizedImageMetadata(
            content_type="image/png",
            canonical_extension="png",
            size_bytes=len(SENSITIVE_BYTES),
            width_px=3,
            height_px=2,
            checksum_sha256=ObjectChecksumSha256(SENSITIVE_CHECKSUM),
        ),
        sanitized_bytes=SanitizedImageBytes(SENSITIVE_BYTES),
    )


def test_runtime_m8_wrappers_and_generic_log_rendering_hide_identity_and_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    bucket = BucketName(SENSITIVE_BUCKET)
    key = ObjectKey(SENSITIVE_KEY)
    image = _image()
    values = (
        PreparedImageUpload(
            object_file_id=SENSITIVE_OBJECT_ID,
            bucket=bucket,
            object_key=key,
            image=image,
        ),
        IngestedImageResult(
            object_file_id=SENSITIVE_OBJECT_ID,
            content_type=image.metadata.content_type,
            size_bytes=image.metadata.size_bytes,
            width_px=image.metadata.width_px,
            height_px=image.metadata.height_px,
            checksum_sha256=image.metadata.checksum_sha256,
        ),
        ClaimedObjectFile(
            object_file_id=SENSITIVE_OBJECT_ID,
            bucket=bucket,
            object_key=key,
            content_type=image.metadata.content_type,
            size_bytes=image.metadata.size_bytes,
            checksum_sha256=image.metadata.checksum_sha256,
            width_px=image.metadata.width_px,
            height_px=image.metadata.height_px,
            status=ObjectFileStatus.PENDING_UPLOAD,
            failure_code=None,
            claimed_at=now,
        ),
        ObjectReadAuthorizationRequest(
            actor_user_id=SENSITIVE_ACTOR_ID,
            object_file_id=SENSITIVE_OBJECT_ID,
            domain_parent_reference=SENSITIVE_PARENT,
        ),
        ResolvedClientIp(SENSITIVE_IP),
        image,
    )

    with caplog.at_level(logging.INFO, logger="tests.storage.sensitive-audit"):
        logging.getLogger("tests.storage.sensitive-audit").info(
            "m8 wrappers=%r",
            values,
        )

    rendered = f"{values!s} {values!r} {caplog.text}"
    for canary in TEST_ONLY_CANARIES:
        assert canary not in rendered


def test_m8_production_modules_have_no_body_or_exception_output_sink() -> None:
    storage_modules = sorted((PROJECT_ROOT / "app/storage").glob("*.py"))
    assert storage_modules

    for path in storage_modules:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported_roots = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert imported_roots.isdisjoint({"logging", "traceback"})
        assert called_names.isdisjoint({"breakpoint", "pprint", "print"})
        assert "logger." not in source
        assert "request.body(" not in source


def test_exact_test_canaries_are_allowlisted_to_this_test_module() -> None:
    searchable_files = (
        *sorted((PROJECT_ROOT / "app").rglob("*.py")),
        *sorted((PROJECT_ROOT / "deploy").glob("*")),
        *sorted((PROJECT_ROOT / "docs").glob("m8*.md")),
        PROJECT_ROOT / "compose.yaml",
        PROJECT_ROOT / ".github/workflows/ci.yml",
    )

    for path in searchable_files:
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        for canary in TEST_ONLY_CANARIES:
            assert canary not in source


def test_operational_surfaces_forbid_sensitive_debug_modes() -> None:
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    init_script = (PROJECT_ROOT / "deploy/minio-init.sh").read_text(encoding="utf-8")
    backup_script = (
        PROJECT_ROOT / "deploy/minio-backup-restore-exercise.sh"
    ).read_text(encoding="utf-8")
    runbook = (PROJECT_ROOT / "docs/m8_storage_runbook.md").read_text(encoding="utf-8")
    combined_runtime = "\n".join((compose, workflow, init_script, backup_script))

    for forbidden in ("set -x", "printenv", "docker logs", "curl -v", "pytest -s"):
        assert forbidden not in combined_runtime
    assert 'echo "::add-mask::$masked_value"' in workflow
    assert "Never print or paste an endpoint" in runbook
    assert "presigned" in runbook.casefold()
    assert "private fixture" in runbook.casefold()
