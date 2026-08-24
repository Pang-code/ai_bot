from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    model_api_key: SecretStr
    model_base_url: str = Field(min_length=1)
    model_name: str
    model_timeout_seconds: float = Field(default=60, gt=0)
    model_max_retries: int = Field(default=2, ge=0)

    jwt_secret: SecretStr | None = None
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_access_token_minutes: int = Field(default=60, ge=5, le=1440)
    jwt_issuer: str = "ai-agents-api"
    jwt_audience: str = "ai-agents-frontend"

    database_url: str | None = None
    postgres_pool_min_size: int = Field(default=1, ge=1)
    postgres_pool_max_size: int = Field(default=10, ge=1)
    postgres_pool_timeout_seconds: float = Field(default=30, gt=0)

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_allow_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173"
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str | None) -> str | None:
        if value and not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL 必须是 PostgreSQL 连接地址")
        return value

    @model_validator(mode="after")
    def validate_pool_size(self) -> Self:
        if self.postgres_pool_max_size < self.postgres_pool_min_size:
            raise ValueError(
                "POSTGRES_POOL_MAX_SIZE 不能小于 POSTGRES_POOL_MIN_SIZE"
            )
        return self

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ValueError("启动 API 前请先在 .env 中填写 DATABASE_URL。")
        return self.database_url

    def require_jwt_secret(self) -> str:
        if self.jwt_secret is None or len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET 必须设置为至少 32 字符的随机密钥。")
        return self.jwt_secret.get_secret_value()

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allow_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
