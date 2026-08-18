from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://localhost:5432/erp_agent",
        validation_alias="DATABASE_URL",
    )
    encryption_key: str = Field(default="", validation_alias="ENCRYPTION_KEY")
    frontend_origin: str = Field(default="http://localhost:3000", validation_alias="FRONTEND_ORIGIN")
    trusted_hosts: str = Field(default="localhost,127.0.0.1", validation_alias="TRUSTED_HOSTS")
    erp_allowed_hosts: str = Field(default="*", validation_alias="ERP_ALLOWED_HOSTS")
    workspace_root: Path = Field(default=Path("workspaces"), validation_alias="WORKSPACE_ROOT")
    max_workspace_file_bytes: int = Field(default=5_000_000, validation_alias="MAX_WORKSPACE_FILE_BYTES")
    secure_cookies: bool = Field(default=False, validation_alias="SECURE_COOKIES")
    session_hours: int = 8
    action_expiry_minutes: int = 30
    public_base_url: str = Field(default="http://localhost:8001", validation_alias="PUBLIC_BASE_URL")
    worker_concurrency: int = Field(default=5, validation_alias="WORKER_CONCURRENCY")

    @field_validator("encryption_key")
    @classmethod
    def require_encryption_key(cls, value: str) -> str:
        if not value:
            raise ValueError("ENCRYPTION_KEY is required")
        return value

    @property
    def allowed_hosts(self) -> set[str]:
        return {host.strip().lower() for host in self.erp_allowed_hosts.split(",") if host.strip()}

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @property
    def checkpoint_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


settings = Settings()
