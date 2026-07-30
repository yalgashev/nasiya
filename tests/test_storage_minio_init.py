from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _script() -> str:
    return (PROJECT_ROOT / "deploy" / "minio-init.sh").read_text(encoding="utf-8")


def test_init_is_fail_closed_silent_and_idempotent() -> None:
    script = _script()

    assert "set -eu" in script
    assert "mc ready nasiya-minio" in script
    assert 'mc mb --ignore-existing "nasiya-minio/$MINIO_BUCKET"' in script
    assert 'mc anonymous set private "nasiya-minio/$MINIO_BUCKET"' in script
    assert "mc admin policy create" in script
    assert "mc admin user add" in script
    assert "mc admin user enable" in script
    assert "mc admin policy attach" in script
    assert "mc admin user info" in script
    assert "mc anonymous get" in script
    assert "*'`private`'*)" in script
    assert "set -x" not in script
    assert "env" not in script
    assert "echo " not in script
    assert "--json" not in script
    assert "admin user rm" not in script
    assert "admin policy rm" not in script


def test_policy_is_exactly_bucket_scoped_and_least_privilege() -> None:
    script = _script()

    for action in (
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
    ):
        assert f'"{action}"' in script
    assert "arn:aws:s3:::$MINIO_BUCKET" in script
    assert "arn:aws:s3:::$MINIO_BUCKET/*" in script
    assert "s3:*" not in script
    assert "admin:*" not in script
    assert "s3:ListAllMyBuckets" not in script
    assert "s3:PutBucketPolicy" not in script
    assert "s3:PutBucketAcl" not in script
    assert "s3:CreateBucket" not in script
    assert "s3:DeleteBucket" not in script


def test_secret_values_are_only_quoted_command_arguments() -> None:
    script = _script()

    assert '"$MINIO_ROOT_USER"' in script
    assert '"$MINIO_ROOT_PASSWORD"' in script
    assert '"$MINIO_APP_ACCESS_KEY"' in script
    assert '"$MINIO_APP_SECRET_KEY"' in script
    assert "run_quiet mc alias set" in script
    assert "run_quiet mc admin user add" in script
    assert '"$@" >/dev/null 2>&1' in script
    assert "printf '%s\\n' \"minio-init failed\"" in script
    assert "printf '%s\\n' \"$MINIO" not in script
