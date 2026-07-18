from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_environment: str = "development"
    debug: bool = False
    database_url: str

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
