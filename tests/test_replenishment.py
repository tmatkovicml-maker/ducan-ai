"""Nightly replenishment: grouping per supplier, caps, documents, sending, receiving."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from ducan.actions import executors, service
from ducan.actions.executors import NotReversible
from ducan.actions.executors.purchase_order import PurchaseOrderExecutor
from ducan.brand import Brand
from ducan.config import AppConfig, SpendingCaps
from ducan.inventory.receiving import ReceivingError, receive_order
from ducan.models import Action, ActionStatus, ActionType, StockLevel, Supplier
from ducan.replenishment.documents import load_lines, order_csv, order_pdf
from ducan.replenishment.job import run_replenishment
from ducan.validation.caps import on_order_quantity
from tests.conftest import NOW, add_daily_sales

TODAY = date(2026, 11, 18)  # Wednesday; supplier delivers Tue/Fri with 2 days lead time
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@pytest.fixture
def config(app_config: AppConfig) -> AppConfig:
    return app_config


@pytest.fixture
def selling(catalog):
    add_daily_sales(catalog, "P4-0001", "10", 28, TODAY)
    add_daily_sales(catalog, "P4-0002", "0.5", 28, TODAY)
    return catalog


def _orders(session):
    return list(session.scalars(select(Action).where(Action.type == ActionType.PURCHASE_ORDER)))


def test_proposes_one_order_per_supplier(selling, config):
    run = run_replenishment(selling, config, TODAY, NOW)
    orders = _orders(selling)
    assert len(orders) == 1 and run.summary["proposed"] == [orders[0].id]
    order = orders[0]
    assert order.status == ActionStatus.PROPOSED
    assert order.payload["delivery_date"] == "2026-11-20"
    lines = {line["sku"]: Decimal(line["quantity"]) for line in order.payload["lines"]}
    assert lines == {"P4-0001": Decimal("84"), "P4-0002": Decimal("4")}
    # Most urgent first: water covers 3 days, oil 10.
    assert [line["sku"] for line in order.payload["lines"]] == ["P4-0001", "P4-0002"]
    text = order.payload["draft_text"]
    assert "84 boca × 0,40 € = 33,60 €" in text
    assert "Ukupno bez PDV-a: 65,60 €" in text
    assert "Poslovanje dućana vodi AI uz nadzor ljudi." in text
    assert "Vladimira Gortana 9" in text


def test_nothing_is_sent_by_the_job(selling, config):
    sent = []
    executors.register(
        ActionType.PURCHASE_ORDER,
        PurchaseOrderExecutor(
            mailer=type("M", (), {"send": lambda self, m: sent.append(m)})(),
            config=config,
            brand=Brand(root=config.env.brand_dir, name=None),
        ),
    )
    run_replenishment(selling, config, TODAY, NOW)
    assert sent == []


def test_zero_caps_block_and_flag(selling, config):
    config.business.spending_caps = SpendingCaps()
    run = run_replenishment(selling, config, TODAY, NOW)
    assert _orders(selling) == []
    assert any("Limiti potrošnje" in f for f in run.summary["flags"])
    assert run.summary["to_order"][0]["proposable"] is False


def test_cap_drops_least_urgent_lines(selling, config):
    selling.get(Supplier, 1).min_order_net = Decimal("30")
    config.business.spending_caps = SpendingCaps(
        per_order_net=Decimal("40"), per_week_net=Decimal("300")
    )
    run = run_replenishment(selling, config, TODAY, NOW)
    order = _orders(selling)[0]
    assert [line["sku"] for line in order.payload["lines"]] == ["P4-0001"]
    assert any("izostavljeno: P4-0002" in f for f in run.summary["flags"])


def test_below_supplier_minimum_not_proposed(selling, config):
    selling.get(Supplier, 1).min_order_net = Decimal("100")
    run = run_replenishment(selling, config, TODAY, NOW)
    assert _orders(selling) == []
    assert any("ispod minimalne" in f for f in run.summary["flags"])


def test_unknown_supplier_terms_flagged(selling, config):
    selling.get(Supplier, 1).lead_time_days = None
    run = run_replenishment(selling, config, TODAY, NOW)
    assert _orders(selling) == []
    assert any("nepoznat rok isporuke" in f for f in run.summary["flags"])


def test_no_history_flagged_not_guessed(catalog, config):
    run = run_replenishment(catalog, config, TODAY, NOW)
    assert _orders(catalog) == []
    assert any("nema dovoljno podataka" in f for f in run.summary["flags"])


def test_pending_order_not_duplicated(selling, config):
    run_replenishment(selling, config, TODAY, NOW)
    run = run_replenishment(selling, config, TODAY, NOW)
    assert len(_orders(selling)) == 1
    assert any("još čeka odobrenje" in f for f in run.summary["flags"])


def test_tampered_email_is_rejected(selling, config):
    run_replenishment(selling, config, TODAY, NOW)
    order = _orders(selling)[0]
    payload = {
        **order.payload,
        "draft_text": order.payload["draft_text"].replace("33,60 €", "30,00 €"),
    }
    with pytest.raises(service.ProposalRejected):
        service.propose(
            selling,
            config.business,
            action_type=ActionType.PURCHASE_ORDER,
            title="x",
            payload=payload,
            references=order.references,
            now=NOW,
        )


def test_documents(selling, config):
    run_replenishment(selling, config, TODAY, NOW)
    order = _orders(selling)[0]
    lines = load_lines(selling, order.payload)
    csv_text = order_csv(lines).decode("utf-8-sig")
    assert "P4-0001;Mineralna voda 0,5 l;boca;84;0,40;33,60" in csv_text
    pdf = order_pdf(
        selling.get(Supplier, 1),
        lines,
        date(2026, 11, 20),
        config.business.shop,
        Brand(root=config.env.brand_dir, name=None),
        config.env.pdf_font_path.__class__(FONT),
        order.id,
    )
    assert pdf.startswith(b"%PDF")


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def test_approved_order_is_emailed_and_cannot_be_unsent(selling, config):
    mailer = FakeMailer()
    config.env.smtp_from = "ducan@example.test"
    config.env.pdf_font_path = config.env.pdf_font_path.__class__(FONT)
    executors.register(
        ActionType.PURCHASE_ORDER,
        PurchaseOrderExecutor(
            mailer=mailer, config=config, brand=Brand(root=config.env.brand_dir, name=None)
        ),
    )
    run_replenishment(selling, config, TODAY, NOW)
    order = _orders(selling)[0]
    service.approve(selling, config.business, order.id, "owner", now=NOW)
    assert order.status == ActionStatus.EXECUTED
    message = mailer.sent[0]
    assert message["To"] == "narudzbe@example.test"
    names = [part.get_filename() for part in message.iter_attachments()]
    assert names == [f"narudzba-{order.id}.pdf", f"narudzba-{order.id}.csv"]
    with pytest.raises(NotReversible):
        service.rollback(selling, order.id, "owner", "greška")


def test_without_smtp_order_stays_approved(selling, config):
    run_replenishment(selling, config, TODAY, NOW)
    order = _orders(selling)[0]
    service.approve(selling, config.business, order.id, "owner", now=NOW)
    assert order.status == ActionStatus.APPROVED


def test_receiving_books_stock_and_closes_open_quantity(selling, config):
    run_replenishment(selling, config, TODAY, NOW)
    order = _orders(selling)[0]
    service.approve(selling, config.business, order.id, "owner", now=NOW)
    assert on_order_quantity(selling, "P4-0001") == Decimal("84")
    before = selling.get(StockLevel, "P4-0001").quantity
    receive_order(selling, order, {"P4-0001": Decimal("80"), "P4-0002": Decimal("4")}, "owner")
    assert selling.get(StockLevel, "P4-0001").quantity == before + 80
    assert on_order_quantity(selling, "P4-0001") == 0
    with pytest.raises(ReceivingError):
        receive_order(selling, order, {"P4-0001": Decimal("1")}, "owner")
