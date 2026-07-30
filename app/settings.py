import re
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.storage.contracts import StorageConfig
from app.telegram.bot import TelegramBotUsername

MIN_RATE_LIMIT_HMAC_KEY_LENGTH = 32
MIN_OTP_HMAC_KEY_LENGTH = 32
MAX_OBJECT_STORAGE_UPLOAD_BYTES = 10_485_760
MAX_OBJECT_STORAGE_MULTIPART_OVERHEAD_BYTES = 1_048_576
MAX_OBJECT_STORAGE_IMAGE_PIXELS = 40_000_000
MAX_OBJECT_STORAGE_IMAGE_DIMENSION = 16_384
MAX_OBJECT_STORAGE_UPLOAD_USER_ATTEMPTS = 5
MAX_OBJECT_STORAGE_UPLOAD_IP_ATTEMPTS = 20
MIN_OBJECT_STORAGE_UPLOAD_WINDOW_SECONDS = 900
_OBJECT_STORAGE_REGION_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$",
    flags=re.ASCII,
)
_OBJECT_STORAGE_BUCKET_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$",
    flags=re.ASCII,
)
_IPV4_STYLE_BUCKET_PATTERN = re.compile(
    r"^[0-9]{1,3}(?:\.[0-9]{1,3}){3}$",
    flags=re.ASCII,
)
ClientIpNetwork = IPv4Network | IPv6Network


class ClientIpMode(StrEnum):
    DIRECT = "direct"
    TRUSTED_PROXY = "trusted_proxy"


class TelegramWorkerSettingsError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Telegram worker credentials are not configured")


class OtpHmacKeySettingsError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("OTP HMAC key is not configured")


class ObjectStorageSettingsError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Object storage configuration is unavailable")


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
    otp_hmac_key: SecretStr | None = None
    otp_login_ttl_seconds: int = Field(default=180, ge=60, le=600)
    otp_login_max_verify_attempts: int = Field(default=5, ge=1, le=10)
    otp_login_resend_cooldown_seconds: int = Field(default=60, gt=0)
    otp_login_rate_limit_window_seconds: int = Field(default=900, gt=0)
    otp_login_rate_limit_phone_attempts: int = Field(default=3, gt=0)
    otp_login_rate_limit_user_attempts: int = Field(default=3, gt=0)
    otp_login_rate_limit_ip_attempts: int = Field(default=20, gt=0)
    otp_dispatch_poll_seconds: int = Field(default=1, gt=0)
    otp_dispatch_batch_size: int = Field(default=20, ge=1, le=100)
    otp_dispatch_claim_stale_seconds: int = Field(default=60, gt=0)
    otp_dispatch_heartbeat_seconds: int = Field(default=10, gt=0)
    otp_dispatch_stale_seconds: int = Field(default=60, gt=0)
    otp_send_timeout_seconds: int = Field(default=5, ge=1, le=15)
    otp_terminal_retention_days: int = Field(default=30, gt=0)
    otp_event_retention_days: int = Field(default=90, gt=0)
    object_storage_endpoint_url: SecretStr | None = None
    object_storage_region: str | None = None
    object_storage_bucket: str | None = Field(default=None, repr=False)
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    object_storage_use_ssl: bool | None = None
    object_storage_addressing_style: str = "path"
    object_storage_presigned_ttl_seconds: int = Field(default=300, ge=60, le=900)
    object_storage_max_upload_bytes: int = Field(
        default=MAX_OBJECT_STORAGE_UPLOAD_BYTES,
        ge=1,
        le=MAX_OBJECT_STORAGE_UPLOAD_BYTES,
    )
    object_storage_max_multipart_bytes: int = Field(
        default=11_010_048,
        gt=1,
    )
    object_storage_max_image_pixels: int = Field(
        default=MAX_OBJECT_STORAGE_IMAGE_PIXELS,
        ge=1,
        le=MAX_OBJECT_STORAGE_IMAGE_PIXELS,
    )
    object_storage_max_image_dimension: int = Field(
        default=MAX_OBJECT_STORAGE_IMAGE_DIMENSION,
        ge=1,
        le=MAX_OBJECT_STORAGE_IMAGE_DIMENSION,
    )
    object_storage_upload_rate_limit_window_seconds: int = Field(
        default=MIN_OBJECT_STORAGE_UPLOAD_WINDOW_SECONDS,
        ge=MIN_OBJECT_STORAGE_UPLOAD_WINDOW_SECONDS,
    )
    object_storage_upload_rate_limit_user_attempts: int = Field(
        default=MAX_OBJECT_STORAGE_UPLOAD_USER_ATTEMPTS,
        ge=1,
        le=MAX_OBJECT_STORAGE_UPLOAD_USER_ATTEMPTS,
    )
    object_storage_upload_rate_limit_ip_attempts: int = Field(
        default=MAX_OBJECT_STORAGE_UPLOAD_IP_ATTEMPTS,
        ge=1,
        le=MAX_OBJECT_STORAGE_UPLOAD_IP_ATTEMPTS,
    )
    object_storage_reconcile_stale_seconds: int = Field(default=60, gt=0)
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

    @field_validator("otp_hmac_key", mode="before")
    @classmethod
    def validate_otp_hmac_key(cls, value: object) -> SecretStr | None:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
        elif isinstance(value, str):
            secret = value
        else:
            raise ValueError("otp_hmac_key must be a secret string")

        if secret == "":
            return None
        if secret != secret.strip():
            raise ValueError("otp_hmac_key must not contain outer whitespace")
        if len(secret) < MIN_OTP_HMAC_KEY_LENGTH:
            raise ValueError(
                f"otp_hmac_key must be at least {MIN_OTP_HMAC_KEY_LENGTH} characters"
            )
        return SecretStr(secret)

    @field_validator("object_storage_endpoint_url", mode="before")
    @classmethod
    def validate_object_storage_endpoint_url(
        cls,
        value: object,
    ) -> SecretStr | None:
        secret = _optional_secret_value(
            value,
            field_name="object_storage_endpoint_url",
        )
        if secret is None:
            return None

        parsed = urlsplit(secret)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("object_storage_endpoint_url must be a valid endpoint")
        return SecretStr(secret.rstrip("/"))

    @field_validator("object_storage_region", mode="before")
    @classmethod
    def validate_object_storage_region(cls, value: object) -> str | None:
        candidate = _optional_string_value(
            value,
            field_name="object_storage_region",
        )
        if candidate is None:
            return None
        if _OBJECT_STORAGE_REGION_PATTERN.fullmatch(candidate) is None:
            raise ValueError("object_storage_region has an invalid format")
        return candidate

    @field_validator("object_storage_bucket", mode="before")
    @classmethod
    def validate_object_storage_bucket(cls, value: object) -> str | None:
        candidate = _optional_string_value(
            value,
            field_name="object_storage_bucket",
        )
        if candidate is None:
            return None
        if (
            _OBJECT_STORAGE_BUCKET_PATTERN.fullmatch(candidate) is None
            or ".." in candidate
            or ".-" in candidate
            or "-." in candidate
            or _IPV4_STYLE_BUCKET_PATTERN.fullmatch(candidate) is not None
        ):
            raise ValueError("object_storage_bucket has an invalid format")
        return candidate

    @field_validator(
        "object_storage_access_key",
        "object_storage_secret_key",
        mode="before",
    )
    @classmethod
    def validate_object_storage_credentials(
        cls,
        value: object,
    ) -> SecretStr | None:
        secret = _optional_secret_value(
            value,
            field_name="object_storage_credential",
        )
        return SecretStr(secret) if secret is not None else None

    @field_validator("object_storage_use_ssl", mode="before")
    @classmethod
    def validate_object_storage_use_ssl(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("object_storage_addressing_style")
    @classmethod
    def validate_object_storage_addressing_style(cls, value: str) -> str:
        candidate = value.strip().casefold()
        if candidate not in {"path", "virtual"}:
            raise ValueError("object_storage_addressing_style must be path or virtual")
        return candidate

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
        if self.otp_login_resend_cooldown_seconds >= self.otp_login_ttl_seconds:
            raise ValueError("otp_login_resend_cooldown_seconds must be below OTP TTL")
        if self.otp_dispatch_stale_seconds <= self.otp_dispatch_heartbeat_seconds:
            raise ValueError(
                "otp_dispatch_stale_seconds must be greater than "
                "otp_dispatch_heartbeat_seconds"
            )
        if self.otp_hmac_key is not None:
            otp_secret = self.otp_hmac_key.get_secret_value()
            if otp_secret == self.rate_limit_hmac_key.get_secret_value():
                raise ValueError("otp_hmac_key must not reuse rate_limit_hmac_key")
            if (
                self.telegram_bot_token is not None
                and otp_secret == self.telegram_bot_token.get_secret_value()
            ):
                raise ValueError("otp_hmac_key must not reuse telegram_bot_token")
        if (
            self.object_storage_max_multipart_bytes
            <= self.object_storage_max_upload_bytes
            or self.object_storage_max_multipart_bytes
            > self.object_storage_max_upload_bytes
            + MAX_OBJECT_STORAGE_MULTIPART_OVERHEAD_BYTES
        ):
            raise ValueError(
                "object_storage_max_multipart_bytes must be greater than upload "
                "bytes and add at most 1 MiB"
            )
        if (
            self.object_storage_endpoint_url is not None
            and self.object_storage_use_ssl is not None
        ):
            endpoint_scheme = urlsplit(
                self.object_storage_endpoint_url.get_secret_value()
            ).scheme
            expected_scheme = "https" if self.object_storage_use_ssl else "http"
            if endpoint_scheme != expected_scheme:
                raise ValueError(
                    "object_storage endpoint scheme and use_ssl must agree"
                )
        return self

    def require_telegram_worker_credentials(self) -> TelegramWorkerCredentials:
        if self.telegram_bot_token is None or self.telegram_bot_username is None:
            raise TelegramWorkerSettingsError()
        return TelegramWorkerCredentials(
            bot_token=self.telegram_bot_token,
            bot_username=self.telegram_bot_username,
        )

    def require_otp_hmac_key(self) -> SecretStr:
        if self.otp_hmac_key is None:
            raise OtpHmacKeySettingsError()
        return self.otp_hmac_key

    def require_object_storage_config(self) -> StorageConfig:
        required_values = (
            self.object_storage_endpoint_url,
            self.object_storage_region,
            self.object_storage_bucket,
            self.object_storage_access_key,
            self.object_storage_secret_key,
            self.object_storage_use_ssl,
        )
        if any(value is None for value in required_values):
            raise ObjectStorageSettingsError()

        endpoint_url = self.object_storage_endpoint_url
        region = self.object_storage_region
        bucket = self.object_storage_bucket
        access_key = self.object_storage_access_key
        secret_key = self.object_storage_secret_key
        use_ssl = self.object_storage_use_ssl
        if (
            endpoint_url is None
            or region is None
            or bucket is None
            or access_key is None
            or secret_key is None
            or use_ssl is None
        ):
            raise ObjectStorageSettingsError()

        return StorageConfig(
            endpoint_url=endpoint_url,
            region=region,
            bucket=bucket,
            access_key=access_key,
            secret_key=secret_key,
            use_ssl=use_ssl,
            addressing_style=self.object_storage_addressing_style,
            presigned_ttl_seconds=self.object_storage_presigned_ttl_seconds,
            max_upload_bytes=self.object_storage_max_upload_bytes,
            max_multipart_bytes=self.object_storage_max_multipart_bytes,
            max_image_pixels=self.object_storage_max_image_pixels,
            max_image_dimension=self.object_storage_max_image_dimension,
            upload_rate_limit_window_seconds=(
                self.object_storage_upload_rate_limit_window_seconds
            ),
            upload_rate_limit_user_attempts=(
                self.object_storage_upload_rate_limit_user_attempts
            ),
            upload_rate_limit_ip_attempts=(
                self.object_storage_upload_rate_limit_ip_attempts
            ),
            reconcile_stale_seconds=self.object_storage_reconcile_stale_seconds,
        )


def _optional_string_value(value: object, *, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain outer whitespace")
    return value


def _optional_secret_value(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        secret = value.get_secret_value()
    elif isinstance(value, str):
        secret = value
    else:
        raise ValueError(f"{field_name} must be a secret string")
    if secret == "":
        return None
    if secret != secret.strip():
        raise ValueError(f"{field_name} must not contain outer whitespace")
    return secret
