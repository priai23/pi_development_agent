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
    erp_allowed_hosts: str = Field(default="localhost,127.0.0.1", validation_alias="ERP_ALLOWED_HOSTS")
    workspace_root: Path = Field(default=Path("workspaces"), validation_alias="WORKSPACE_ROOT")
    max_workspace_file_bytes: int = Field(default=5_000_000, validation_alias="MAX_WORKSPACE_FILE_BYTES")
    secure_cookies: bool = Field(default=False, validation_alias="SECURE_COOKIES")
    session_hours: int = 8
    action_expiry_minutes: int = 30
    public_base_url: str = Field(default="http://localhost:8001", validation_alias="PUBLIC_BASE_URL")
    worker_concurrency: int = Field(default=5, validation_alias="WORKER_CONCURRENCY")
    validation_postgres_admin_dsn: str = Field(default="", validation_alias="VALIDATION_POSTGRES_ADMIN_DSN")
    validation_postgres_host: str = Field(default="localhost", validation_alias="VALIDATION_POSTGRES_HOST")
    validation_postgres_port: int = Field(default=5432, validation_alias="VALIDATION_POSTGRES_PORT")
    validation_postgres_user: str = Field(default="odoo", validation_alias="VALIDATION_POSTGRES_USER")
    validation_postgres_password: str = Field(default="", validation_alias="VALIDATION_POSTGRES_PASSWORD")
    validation_odoo_image: str = Field(default="odoo:19.0", validation_alias="VALIDATION_ODOO_IMAGE")
    validation_timeout_seconds: int = Field(default=600, validation_alias="VALIDATION_TIMEOUT_SECONDS")
    autonomous_repair_enabled: bool = Field(default=True, validation_alias="AUTONOMOUS_REPAIR_ENABLED")

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
