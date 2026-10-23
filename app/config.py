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

    s3_enabled: bool = False  # true = also store evidence in S3 and hand out presigned download links
    s3_public_url: str | None = None  # address a person's browser can use for presigned links, if it differs from s3_endpoint_url
    evidence_dir: str = "evidence/certification"
    connections_file: str | None = None  # JSON file with credentials per destination; never stored in the database
    mock_platforms_url: str = "http://mock-platforms:9100"  # where simulation mode finds the fake platforms

    import_dir: str = "evidence/imports"
    import_workers: int = 32  # concurrent site checks during a CSV import
    import_fetch_timeout_s: float = 5.0
    import_allow_private_hosts: bool = False  # false = refuse private/loopback addresses (SSRF); compose turns it on for the local demo

    callback_hmac_secret: SecretStr = SecretStr("change-me")
    test_post_ttl_seconds: int = 600

    certify_max_consecutive_failures: int = 3  # a batch stops hitting a connector after this many failures in a row
    certify_rate_limit_wait_cap_s: float = 30.0  # longest we wait out a 429 during certification

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
