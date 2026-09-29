"""All runtime configuration, from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "test", "demo", "public"] = "local"
    sim_base_url: str = "http://localhost:8000"
    redis_url: str = "redis://localhost:6379/0"
    # Neon pooled URL in production; unset means a local SQLite file (degraded, dev only).
    database_url: str | None = None
    database_url_direct: str | None = None

    poll_interval_ms: int = 1000
    sim_connect_timeout_s: float = 0.5
    sim_read_timeout_s: float = 2.0
    sim_total_timeout_s: float = 3.0

    risk_horizon_ticks: int = 32
    min_leg_liters: float = 200.0
    depot_reserve_fraction: float = 0.05
    rec_valid_ticks: int = 4

    enable_simulator_test_controls: bool = True
    auth_mode: Literal["off", "apikey"] = "off"
    api_key_viewer: str | None = None
    api_key_operator: str | None = None
    api_key_admin: str | None = None
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    worker_metrics_port: int = 9101
    log_level: str = "INFO"

    @property
    def test_plane_enabled(self) -> bool:
        return self.app_env in {"local", "test", "demo"} and self.enable_simulator_test_controls

    @property
    def effective_auth_mode(self) -> str:
        return "apikey" if self.app_env == "public" else self.auth_mode

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or "sqlite+aiosqlite:///./data/fuelops.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
