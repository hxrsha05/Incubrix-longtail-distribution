"""All settings come from environment variables (Integration Standard point 6)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"
    simulation_mode: bool = True

    database_url: str = "sqlite:///./data/app.db"

    s3_endpoint_url: str = "http://minio:9000"
    s3_bucket: str = "incubrix-distribution"
    s3_access_key: SecretStr = SecretStr("change-me")
    s3_secret_key: SecretStr = SecretStr("change-me")

    callback_hmac_secret: SecretStr = SecretStr("change-me")
    test_post_ttl_seconds: int = 600

    http_timeout_s: float = 10.0
    job_workers: int = 4
    idempotency_pending_timeout_s: float = 120.0  # a 'pending' claim older than this is treated as 'unknown'
    callback_max_attempts: int = 3
    callback_backoff_s: float = 0.5
    # Kill switch: comma-separated connector keys that must refuse every call (CONNECTOR_DISABLED)
    disabled_connectors: str = ""

    @property
    def disabled_connector_set(self) -> set[str]:
        return {k.strip() for k in self.disabled_connectors.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
