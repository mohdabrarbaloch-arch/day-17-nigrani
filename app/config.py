"""Application settings, loaded from environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Nigrani"
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    secret_key: str = "dev-only-insecure-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    database_url: str = "sqlite:///./nigrani.db"

    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    rate_limit_per_minute: int = 120

    health_critical_threshold: int = 40
    health_warning_threshold: int = 65
    rul_history_window: int = 20

    rpa_auto_work_orders: bool = True
    rpa_escalation_hours: int = 24

    seed_admin_email: str = "admin@nigrani.dev"
    seed_admin_password: str = "ChangeMe123!"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
