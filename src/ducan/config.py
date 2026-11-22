"""Application configuration.

Secrets and deployment settings come from the environment (``.env``); business
rules come from ``config/settings.yaml``. Missing or invalid values fail loudly
at startup instead of falling back to guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


class EnvSettings(BaseSettings):
    """Settings read from environment variables / ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str
    admin_password_hash: str
    database_url: str = "sqlite:///data/ducan.sqlite3"
    config_dir: Path = Path("config")
    brand_dir: Path = Path("brand")
    data_dir: Path = Path("data")
    cookie_secure: bool = True
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None

    @field_validator("secret_key")
    @classmethod
    def _secret_key_strong(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return value

    @field_validator("admin_password_hash")
    @classmethod
    def _hash_is_argon2(cls, value: str) -> str:
        if not value.startswith("$argon2"):
            raise ValueError("ADMIN_PASSWORD_HASH must be an argon2 hash (run: make hash-password)")
        return value


class SpendingCaps(BaseModel):
    """Hard caps for purchase orders, EUR net."""

    per_order_net: Decimal = Decimal("0")
    per_week_net: Decimal = Decimal("0")


class LowRisk(BaseModel):
    """Thresholds for bulk approval. ``None`` means never low-risk."""

    purchase_order_max_net: Decimal | None = None
    price_change_max_pct: Decimal | None = None
    review_reply_min_stars: int | None = None


class PosConfig(BaseModel):
    """POS adapter selection."""

    adapter: str = "csv"
    poll_minutes: int = Field(default=15, ge=1)


class BusinessConfig(BaseModel):
    """Business rules loaded from ``settings.yaml``."""

    timezone: str = "Europe/Zagreb"
    currency: str = "EUR"
    spending_caps: SpendingCaps
    low_risk: LowRisk
    vat_rates: list[Decimal]
    pos: PosConfig = PosConfig()


def load_business_config(config_dir: Path) -> BusinessConfig:
    """Load and validate ``settings.yaml`` from ``config_dir``."""
    path = config_dir / "settings.yaml"
    if not path.is_file():
        raise ConfigError(f"Missing configuration file: {path}")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    try:
        return BusinessConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid {path}: {exc}") from exc


@dataclass(frozen=True)
class AppConfig:
    """Everything the application needs to know about its configuration."""

    env: EnvSettings
    business: BusinessConfig

    @property
    def pos_inbox(self) -> Path:
        return self.env.data_dir / "pos_inbox"

    @property
    def pos_archive(self) -> Path:
        return self.env.data_dir / "pos_archive"

    @property
    def pos_rejected(self) -> Path:
        return self.env.data_dir / "pos_rejected"


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return the process-wide configuration (cached)."""
    env = EnvSettings()  # type: ignore[call-arg]
    return AppConfig(env=env, business=load_business_config(env.config_dir))
