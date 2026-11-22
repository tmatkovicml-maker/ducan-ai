"""Shared fixtures: in-memory database, fixed business rules, catalogue, web client."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ducan.models  # noqa: F401
from ducan.auth.security import hash_password
from ducan.config import (
    AppConfig,
    BusinessConfig,
    EnvSettings,
    LowRisk,
    ShopInfo,
    SpendingCaps,
)
from ducan.db import Base
from ducan.models import Product, StockLevel, Supplier

PASSWORD = "ispravna-konj-baterija-spajalica"
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)  # Wednesday


@pytest.fixture
def business() -> BusinessConfig:
    return BusinessConfig(
        timezone="Europe/Zagreb",
        spending_caps=SpendingCaps(per_order_net=Decimal("200"), per_week_net=Decimal("300")),
        low_risk=LowRisk(
            purchase_order_max_net=Decimal("100"),
            price_change_max_pct=Decimal("5"),
            review_reply_min_stars=4,
        ),
        vat_rates=[Decimal("5"), Decimal("13"), Decimal("25")],
        shop=ShopInfo(address="Vladimira Gortana 9, Mali Lošinj (P4)"),
    )


@pytest.fixture
def factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as s:
        yield s


@pytest.fixture
def catalog(session: Session) -> Session:
    """One supplier and three products with known values (test data)."""
    session.add(
        Supplier(
            id=1,
            name="Test Dobavljač d.o.o.",
            email="narudzbe@example.test",
            lead_time_days=2,
            min_order_net=Decimal("50"),
            delivery_days="2,5",
        )
    )
    session.add_all(
        [
            Product(
                sku="P4-0001",
                name_hr="Mineralna voda 0,5 l",
                category="Pića",
                unit="boca",
                vat_rate=Decimal("25"),
                supplier_id=1,
                purchase_price_net=Decimal("0.40"),
                retail_price_gross=Decimal("1.20"),
                min_stock=Decimal("24"),
                max_stock=Decimal("240"),
                active=True,
            ),
            Product(
                sku="P4-0002",
                name_hr="Maslinovo ulje 0,5 l",
                category="Lokalni proizvodi",
                unit="boca",
                vat_rate=Decimal("25"),
                supplier_id=1,
                purchase_price_net=Decimal("8.00"),
                retail_price_gross=Decimal("15.90"),
                min_stock=Decimal("4"),
                max_stock=Decimal("20"),
                active=True,
            ),
            Product(
                sku="P4-0003", name_hr="Razglednica", category="Suveniri", unit="kom", active=False
            ),
        ]
    )
    session.add_all(
        [
            StockLevel(sku="P4-0001", quantity=Decimal("30")),
            StockLevel(sku="P4-0002", quantity=Decimal("5")),
            StockLevel(sku="P4-0003", quantity=Decimal("0")),
        ]
    )
    session.commit()
    return session


def add_daily_sales(
    session: Session, sku: str, per_day: str, days: int, today=None, price: str = "1.00"
) -> None:
    """Record ``per_day`` units of ``sku`` at noon on each of the ``days`` days before today."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from ducan.models import Sale, SalesImport

    today = today or NOW.astimezone(ZoneInfo("Europe/Zagreb")).date()
    imp = SalesImport(source="test", filename=f"{sku}-{days}", file_sha256=f"{sku}{days}{per_day}")
    session.add(imp)
    session.flush()
    for offset in range(1, days + 1):
        day = today - timedelta(days=offset)
        ts = datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo("Europe/Zagreb"))
        session.add(
            Sale(
                ts=ts,
                sku=sku,
                quantity=Decimal(per_day),
                gross_amount=Decimal(per_day) * Decimal(price),
                payment_method="card",
                import_id=imp.id,
            )
        )
    session.flush()


@pytest.fixture(autouse=True)
def _reset_executors() -> Iterator[None]:
    from ducan.actions import executors

    executors.reset()
    yield
    executors.reset()


def write_brand(brand_dir: Path) -> None:
    brand_dir.mkdir(parents=True, exist_ok=True)
    (brand_dir / "brand.yaml").write_text("name: null\nfiles: {}\n", encoding="utf-8")


@pytest.fixture
def app_config(tmp_path: Path, business: BusinessConfig) -> AppConfig:
    write_brand(tmp_path / "brand")
    env = EnvSettings(
        secret_key="t" * 40,
        admin_password_hash=hash_password(PASSWORD),
        database_url="sqlite://",
        brand_dir=tmp_path / "brand",
        data_dir=tmp_path / "data",
        cookie_secure=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    return AppConfig(env=env, business=business)


@pytest.fixture
def client(app_config: AppConfig, factory: sessionmaker[Session], catalog: Session) -> TestClient:
    from ducan.main import create_app

    return TestClient(create_app(app_config, factory))


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login", data={"password": PASSWORD, "csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303
    return client
