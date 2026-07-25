from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.telegram.bot import TelegramBotUsername

MIN_RATE_LIMIT_HMAC_KEY_LENGTH = 32


class Settings(BaseSettings):
    app_environment: str = "development"
    debug: bool = False
    database_url: str
    session_cookie_name: str = "nasiya_session"
    session_cookie_secure: bool
    session_ttl_days: int = Field(default=30, gt=0)
    anonymous_session_ttl_minutes: int = Field(default=30, gt=0)
    session_touch_interval_minutes: int = Field(default=5, gt=0)
    password_min_length: int = Field(default=8, gt=0)
    password_max_length: int = Field(default=128, gt=0)
    login_rate_limit_window_seconds: int = Field(default=900, gt=0)
    login_rate_limit_phone_attempts: int = Field(default=5, gt=0)
    login_rate_limit_ip_attempts: int = Field(default=20, gt=0)
    telegram_link_rate_limit_window_seconds: int = Field(default=900, gt=0)
    telegram_link_rate_limit_user_attempts: int = Field(default=3, gt=0)
    telegram_link_rate_limit_phone_attempts: int = Field(default=3, gt=0)
    telegram_link_rate_limit_ip_attempts: int = Field(default=20, gt=0)
    rate_limit_hmac_key: SecretStr
    telegram_bot_username: TelegramBotUsername | None = None

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        arbitrary_types_allowed=True,
        hide_input_in_errors=True,
    )

    @field_validator("session_cookie_name")
    @classmethod
    def validate_session_cookie_name(cls, value: str) -> str:
        cookie_name = value.strip()
        if not cookie_name:
            raise ValueError("session_cookie_name must not be empty")
        return cookie_name

    @field_validator("rate_limit_hmac_key")
    @classmethod
    def validate_rate_limit_hmac_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret.strip()) < MIN_RATE_LIMIT_HMAC_KEY_LENGTH:
            raise ValueError(
                "rate_limit_hmac_key must be at least "
                f"{MIN_RATE_LIMIT_HMAC_KEY_LENGTH} characters"
            )
        return value

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def validate_telegram_bot_username(
        cls,
        value: object,
    ) -> TelegramBotUsername | None:
        if value is None:
            return None
        if isinstance(value, TelegramBotUsername):
            return value
        if isinstance(value, str):
            if value == "":
                return None
            return TelegramBotUsername(value)
        raise ValueError("telegram_bot_username must be a string")

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        if self.password_max_length < self.password_min_length:
            raise ValueError(
                "password_max_length must be greater than or equal to "
                "password_min_length"
            )
        if (
            self.app_environment.strip().casefold() == "production"
            and not self.session_cookie_secure
        ):
            raise ValueError(
                "session_cookie_secure must be true in production environment"
            )
        return self
