import pytest
from pydantic import ValidationError

from app.main import create_app
from app.settings import Settings


def test_settings_created_with_required_values(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    settings = Settings(
        _env_file=None,
        app_environment="production",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya",
    )

    assert settings.app_environment == "production"
    assert settings.debug is True
    assert settings.database_url == "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya"


def test_settings_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_environment="development", debug=False)


def test_create_app_accepts_explicit_settings() -> None:
    settings = Settings(
        app_environment="testing",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test",
    )
    app = create_app(settings=settings)

    assert app.state.settings is settings
