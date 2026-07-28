from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def compose_text() -> str:
    return (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")


def service_block(text: str, service_name: str, next_service_name: str) -> str:
    return text.split(f"  {service_name}:", 1)[1].split(
        f"  {next_service_name}:",
        1,
    )[0]


def test_compose_migration_orders_web_and_worker_without_web_token() -> None:
    text = compose_text()
    migration = service_block(text, "migrate", "web")
    web = service_block(text, "web", "telegram-worker")
    worker = text.split("  telegram-worker:", 1)[1].split("\nvolumes:", 1)[0]

    assert 'command: ["alembic", "upgrade", "head"]' in migration
    assert "condition: service_healthy" in migration
    assert 'restart: "no"' in migration
    assert "condition: service_completed_successfully" in web
    assert "condition: service_completed_successfully" in worker
    assert "TELEGRAM_BOT_TOKEN" not in web
    assert "TELEGRAM_BOT_TOKEN:" in worker


def test_compose_worker_uses_same_image_contract_and_exact_lifecycle_values() -> None:
    text = compose_text()
    web = service_block(text, "web", "telegram-worker")
    worker = text.split("  telegram-worker:", 1)[1].split("\nvolumes:", 1)[0]

    for build_line in ("build:", "context: .", "dockerfile: Dockerfile"):
        assert build_line in web
        assert build_line in worker
    assert 'command: ["python", "-m", "app.telegram.worker", "run"]' in worker
    assert "restart: unless-stopped" in worker
    assert "stop_grace_period: 45s" in worker
    assert "replicas: 1" in worker
    assert (
        'test: ["CMD", "python", "-m", "app.telegram.worker", "healthcheck"]' in worker
    )
    for health_value in (
        "interval: 15s",
        "timeout: 5s",
        "retries: 3",
        "start_period: 20s",
    ):
        assert health_value in worker
