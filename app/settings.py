from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.telegram.bot import TelegramBotUsername

MIN_RATE_LIMIT_HMAC_KEY_LENGTH = 32
ClientIpNetwork = IPv4Network | IPv6Network


class ClientIpMode(StrEnum):
    DIRECT = "direct"
    TRUSTED_PROXY = "trusted_proxy"


class TelegramWorkerSettingsError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Telegram worker credentials are not configured")


@dataclass(frozen=True, repr=False)
class TelegramWorkerCredentials:
    bot_token: SecretStr
    bot_username: TelegramBotUsername

    def __repr__(self) -> str:
        return "TelegramWorkerCredentials(bot_token=<redacted>, bot_username=<set>)"


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
    telegram_bot_token: SecretStr | None = None
    client_ip_mode: ClientIpMode = ClientIpMode.DIRECT
    trusted_proxy_cidrs: Annotated[tuple[ClientIpNetwork, ...], NoDecode] = ()

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

    @field_validator("telegram_bot_token", mode="before")
    @classmethod
    def validate_telegram_bot_token(cls, value: object) -> SecretStr | None:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
        elif isinstance(value, str):
            secret = value
        else:
            raise ValueError("telegram_bot_token must be a secret string")

        if secret == "":
            return None
        if secret != secret.strip():
            raise ValueError("telegram_bot_token must not contain outer whitespace")
        return SecretStr(secret)

    @field_validator("trusted_proxy_cidrs", mode="before")
    @classmethod
    def validate_trusted_proxy_cidrs(
        cls,
        value: object,
    ) -> tuple[ClientIpNetwork, ...]:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            raw_cidrs: tuple[object, ...] = tuple(value.split(","))
        elif isinstance(value, (list, tuple)):
            raw_cidrs = tuple(value)
        else:
            raise ValueError("trusted_proxy_cidrs must be a CIDR list")

        networks: list[ClientIpNetwork] = []
        for raw_cidr in raw_cidrs:
            if isinstance(raw_cidr, (IPv4Network, IPv6Network)):
                network = raw_cidr
            elif isinstance(raw_cidr, str):
                candidate = raw_cidr.strip()
                if not candidate or "/" not in candidate:
                    raise ValueError(
                        "trusted_proxy_cidrs must contain explicit valid CIDRs"
                    )
                try:
                    network = ip_network(candidate, strict=False)
                except ValueError as exc:
                    raise ValueError(
                        "trusted_proxy_cidrs must contain explicit valid CIDRs"
                    ) from exc
            else:
                raise ValueError(
                    "trusted_proxy_cidrs must contain explicit valid CIDRs"
                )

            if network.prefixlen == 0:
                raise ValueError("trusted_proxy_cidrs must not trust all addresses")
            if network not in networks:
                networks.append(network)

        return tuple(networks)

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
        if (
            self.client_ip_mode is ClientIpMode.TRUSTED_PROXY
            and not self.trusted_proxy_cidrs
        ):
            raise ValueError(
                "trusted_proxy mode requires a non-empty trusted_proxy_cidrs"
            )
        if self.client_ip_mode is ClientIpMode.DIRECT and self.trusted_proxy_cidrs:
            raise ValueError(
                "trusted_proxy_cidrs requires client_ip_mode=trusted_proxy"
            )
        return self

    def require_telegram_worker_credentials(self) -> TelegramWorkerCredentials:
        if self.telegram_bot_token is None or self.telegram_bot_username is None:
            raise TelegramWorkerSettingsError()
        return TelegramWorkerCredentials(
            bot_token=self.telegram_bot_token,
            bot_username=self.telegram_bot_username,
        )
