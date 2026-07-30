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


def _compose_text() -> str:
    return (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")


def _service_block(text: str, name: str, next_name: str) -> str:
    return text.split(f"  {name}:", 1)[1].split(f"  {next_name}:", 1)[0]


def test_minio_service_is_immutable_persistent_and_locally_bound() -> None:
    text = _compose_text()
    minio = _service_block(text, "minio", "minio-init")

    assert f"image: {MINIO_IMAGE}" in minio
    assert (
        'command: ["server", "/data", "--address", ":9000", '
        '"--console-address", ":9001"]'
    ) in minio
    assert "- minio-data:/data" in minio
    assert '"${MINIO_BIND_ADDRESS:-127.0.0.1}:${MINIO_API_PORT:-9000}:9000"' in minio
    assert (
        '"${MINIO_BIND_ADDRESS:-127.0.0.1}:${MINIO_CONSOLE_PORT:-9001}:9001"' in minio
    )
    assert 'test: ["CMD", "mc", "ready", "local"]' in minio
    assert "interval: 5s" in minio
    assert "timeout: 5s" in minio
    assert "retries: 10" in minio
    assert "\n  minio-data:" in text


def test_root_credentials_are_confined_to_minio_service() -> None:
    text = _compose_text()
    minio = _service_block(text, "minio", "minio-init")
    minio_init = _service_block(text, "minio-init", "migrate")
    migrate = _service_block(text, "migrate", "web")
    web = _service_block(text, "web", "telegram-worker")
    worker = _service_block(text, "telegram-worker", "otp-dispatcher")
    dispatcher = text.split("  otp-dispatcher:", 1)[1].split(
        "\nvolumes:",
        1,
    )[0]

    assert "MINIO_ROOT_USER:" in minio
    assert "MINIO_ROOT_PASSWORD:" in minio
    assert "MINIO_ROOT_USER:" in minio_init
    assert "MINIO_ROOT_PASSWORD:" in minio_init
    for application_service in (migrate, web, worker, dispatcher):
        assert "MINIO_ROOT_USER" not in application_service
        assert "MINIO_ROOT_PASSWORD" not in application_service


def test_only_web_receives_optional_app_storage_bundle() -> None:
    text = _compose_text()
    db = _service_block(text, "db", "minio")
    minio = _service_block(text, "minio", "minio-init")
    minio_init = _service_block(text, "minio-init", "migrate")
    migrate = _service_block(text, "migrate", "web")
    web = _service_block(text, "web", "telegram-worker")
    worker = _service_block(text, "telegram-worker", "otp-dispatcher")
    dispatcher = text.split("  otp-dispatcher:", 1)[1].split(
        "\nvolumes:",
        1,
    )[0]

    expected_web_values = (
        "OBJECT_STORAGE_ENDPOINT_URL: ${OBJECT_STORAGE_ENDPOINT_URL:-}",
        "OBJECT_STORAGE_REGION: ${OBJECT_STORAGE_REGION:-}",
        "OBJECT_STORAGE_BUCKET: ${OBJECT_STORAGE_BUCKET:-}",
        "OBJECT_STORAGE_ACCESS_KEY: ${OBJECT_STORAGE_ACCESS_KEY:-}",
        "OBJECT_STORAGE_SECRET_KEY: ${OBJECT_STORAGE_SECRET_KEY:-}",
        "OBJECT_STORAGE_USE_SSL: ${OBJECT_STORAGE_USE_SSL:-}",
        "OBJECT_STORAGE_ADDRESSING_STYLE:",
        "OBJECT_STORAGE_PRESIGNED_TTL_SECONDS:",
        "OBJECT_STORAGE_RECONCILE_STALE_SECONDS:",
    )
    for expected in expected_web_values:
        assert expected in web
    for isolated_service in (db, minio, minio_init, migrate, worker, dispatcher):
        assert "OBJECT_STORAGE_" not in isolated_service


def test_web_database_and_migration_do_not_depend_on_minio() -> None:
    text = _compose_text()
    migrate = _service_block(text, "migrate", "web")
    web = _service_block(text, "web", "telegram-worker")
    worker = _service_block(text, "telegram-worker", "otp-dispatcher")
    dispatcher = text.split("  otp-dispatcher:", 1)[1].split(
        "\nvolumes:",
        1,
    )[0]

    assert "minio:" not in migrate
    assert "minio:" not in web
    assert "minio:" not in worker
    assert "minio:" not in dispatcher
    assert "condition: service_completed_successfully" in web
    assert "condition: service_completed_successfully" in worker
    assert "condition: service_completed_successfully" in dispatcher


def test_example_env_contains_only_synthetic_local_minio_values() -> None:
    env_text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "MINIO_ROOT_USER=local-minio-root" in env_text
    assert (
        "MINIO_ROOT_PASSWORD=change-me-local-minio-root-secret-at-least-32-chars"
    ) in env_text
    assert "MINIO_BIND_ADDRESS=127.0.0.1" in env_text
    assert "MINIO_API_PORT=9000" in env_text
    assert "MINIO_CONSOLE_PORT=9001" in env_text
    assert "MINIO_BUCKET=nasiya-private" in env_text
    assert "MINIO_APP_ACCESS_KEY=local-nasiya-storage-app" in env_text
    assert (
        "MINIO_APP_SECRET_KEY="
        "change-me-local-nasiya-storage-app-secret-at-least-32-chars"
    ) in env_text
    assert "OBJECT_STORAGE_ACCESS_KEY=\n" in env_text
    assert "OBJECT_STORAGE_SECRET_KEY=\n" in env_text


def test_minio_init_uses_pinned_mc_and_waits_for_health() -> None:
    text = _compose_text()
    minio_init = _service_block(text, "minio-init", "migrate")

    assert f"image: {MC_IMAGE}" in minio_init
    assert 'entrypoint: ["/bin/sh", "/scripts/minio-init.sh"]' in minio_init
    assert "- ./deploy/minio-init.sh:/scripts/minio-init.sh:ro" in minio_init
    assert "MINIO_ENDPOINT: http://minio:9000" in minio_init
    assert "MINIO_APP_ACCESS_KEY:" in minio_init
    assert "MINIO_APP_SECRET_KEY:" in minio_init
    assert "MINIO_APP_POLICY_NAME:" in minio_init
    assert "condition: service_healthy" in minio_init
    assert 'restart: "no"' in minio_init
