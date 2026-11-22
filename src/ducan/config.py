"""Application configuration.

Secrets and deployment settings come from the environment (``.env``); business
rules come from ``config/settings.yaml``. Missing or invalid values fail loudly
at startup instead of falling back to guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from datetime import time as dt_time
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
    anthropic_refusal_fallback: str | None = None
    anthropic_effort: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    pdf_font_path: Path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    owner_email: str | None = None
    public_base_url: str | None = None

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


class ShopInfo(BaseModel):
    """Shop details confirmed by the owner."""

    name: str | None = None
    address: str | None = None


class Replenishment(BaseModel):
    """Forecast and ordering parameters."""

    weight_14d: Decimal = Decimal("0.6")
    weight_28d: Decimal = Decimal("0.4")
    min_history_days: int = Field(default=7, ge=1)
    cover_days: int = Field(default=3, ge=0)


class DemandPrior(BaseModel):
    """Demand estimate used before enough sales history exists."""

    expected_daily_revenue_gross: Decimal | None = None


class PricePointTier(BaseModel):
    """Allowed cent endings for prices up to ``up_to`` EUR (None = no limit)."""

    up_to: Decimal | None
    endings: list[Decimal]


class Pricing(BaseModel):
    """Weekly pricing rules."""

    default_floor_margin_pct: Decimal | None = None
    category_floor_margin_pct: dict[str, Decimal] = Field(default_factory=dict)
    competitor_gap_pct: Decimal = Decimal("10")
    competitor_max_age_days: int = 30
    price_points: list[PricePointTier] = Field(default_factory=list)

    def floor_for(self, category: str) -> Decimal | None:
        """Floor margin for a category, falling back to the default."""
        return self.category_floor_margin_pct.get(category, self.default_floor_margin_pct)


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class LabourRules(BaseModel):
    """Labour-law limits from ``labour.yaml``. None = not confirmed by the owner yet."""

    max_hours_per_day: Decimal | None = None
    max_hours_per_week: Decimal | None = None
    min_daily_rest_hours: Decimal | None = None
    min_weekly_rest_hours: Decimal | None = None
    break_after_hours: Decimal | None = None
    break_minutes: int | None = None

    def missing(self) -> list[str]:
        """Names of values the owner still has to fill in."""
        return [name for name, value in self.model_dump().items() if value is None]


class Peak(BaseModel):
    """Extra staff needed in a time window."""

    model_config = {"populate_by_name": True}

    days: list[str] = Field(default_factory=lambda: list(WEEKDAYS))
    from_: time = Field(alias="from")
    to: time
    min_staff: int = Field(ge=1)


class Season(BaseModel):
    """Opening hours for part of the year (month-day range, may wrap over New Year)."""

    model_config = {"populate_by_name": True}

    name: str
    from_: str = Field(alias="from")
    to: str
    hours: dict[str, str] = Field(default_factory=dict)
    min_staff: int = Field(default=1, ge=1)
    peaks: list[Peak] = Field(default_factory=list)

    @field_validator("hours")
    @classmethod
    def _check_hours(cls, value: dict[str, str]) -> dict[str, str]:
        for day, span in value.items():
            if day not in WEEKDAYS:
                raise ValueError(f"unknown weekday '{day}'")
            start, end = span.split("-")
            if time.fromisoformat(start) >= time.fromisoformat(end):
                raise ValueError(f"{day}: opening must be before closing")
        return value

    def contains(self, day: date) -> bool:
        key = day.strftime("%m-%d")
        if self.from_ <= self.to:
            return self.from_ <= key <= self.to
        return key >= self.from_ or key <= self.to

    def opening(self, day: date) -> tuple[time, time] | None:
        span = self.hours.get(WEEKDAYS[day.weekday()])
        if not span:
            return None
        start, end = span.split("-")
        return time.fromisoformat(start), time.fromisoformat(end)


class RotaDeadline(BaseModel):
    """When next week's rota must exist."""

    model_config = {"populate_by_name": True}

    weekday: str = "thu"
    at: dt_time = Field(default=dt_time(12, 0), alias="time")


class Staffing(BaseModel):
    """Seasonal opening hours and staffing needs from ``opening_hours.yaml``."""

    seasons: list[Season] = Field(default_factory=list)
    rota_deadline: RotaDeadline = RotaDeadline()

    def season_for(self, day: date) -> Season | None:
        return next((s for s in self.seasons if s.contains(day)), None)


class Dashboard(BaseModel):
    """Dashboard settings."""

    expiry_warning_days: int = 7


class BusinessConfig(BaseModel):
    """Business rules loaded from ``settings.yaml``."""

    timezone: str = "Europe/Zagreb"
    currency: str = "EUR"
    spending_caps: SpendingCaps
    low_risk: LowRisk
    vat_rates: list[Decimal]
    pos: PosConfig = PosConfig()
    shop: ShopInfo = ShopInfo()
    replenishment: Replenishment = Replenishment()
    seasonality: dict[int, Decimal] = Field(default_factory=dict)
    demand_prior: DemandPrior = DemandPrior()
    pricing: Pricing = Pricing()
    dashboard: Dashboard = Dashboard()
    labour: LabourRules = LabourRules()
    staffing: Staffing = Staffing()

    def season_factor(self, month: int) -> Decimal:
        """Seasonal factor for a month (1 when not configured)."""
        return self.seasonality.get(month, Decimal("1"))


def load_business_config(config_dir: Path) -> BusinessConfig:
    """Load and validate ``settings.yaml`` from ``config_dir``."""
    path = config_dir / "settings.yaml"
    if not path.is_file():
        raise ConfigError(f"Missing configuration file: {path}")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    for name, key in (("labour.yaml", "labour"), ("opening_hours.yaml", "staffing")):
        extra = config_dir / name
        if not extra.is_file():
            raise ConfigError(f"Missing configuration file: {extra}")
        raw[key] = yaml.safe_load(extra.read_text(encoding="utf-8")) or {}
    try:
        return BusinessConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid configuration in {config_dir}: {exc}") from exc


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
