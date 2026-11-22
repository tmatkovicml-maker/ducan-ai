"""Weekly pricing: floor margins, price points, competitor prices, LLM reason."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ducan.actions import service
from ducan.agent.client import Agent
from ducan.config import PricePointTier, Pricing
from ducan.models import Action, ActionType, AuditLog, CompetitorPrice, Product
from ducan.pricing.job import run_pricing
from ducan.pricing.rules import margin_pct, min_gross_for_margin, round_down, round_up
from tests.conftest import NOW

TODAY = date(2026, 11, 18)
D = Decimal
TIERS = [
    PricePointTier(
        up_to=D("5"),
        endings=[
            D("0.09"),
            D("0.19"),
            D("0.29"),
            D("0.39"),
            D("0.49"),
            D("0.59"),
            D("0.69"),
            D("0.79"),
            D("0.89"),
            D("0.99"),
        ],
    ),
    PricePointTier(up_to=D("20"), endings=[D("0.49"), D("0.99")]),
    PricePointTier(up_to=None, endings=[D("0.90")]),
]


@pytest.fixture
def priced(app_config):
    app_config.business.pricing = Pricing(default_floor_margin_pct=D("30"), price_points=TIERS)
    return app_config


def test_price_points():
    assert round_up(D("1.21"), TIERS) == D("1.29")
    assert round_up(D("4.995"), TIERS) == D("5.49")  # 5.09 is not a point above 5 EUR
    assert round_up(D("15.95"), TIERS) == D("15.99")
    assert round_up(D("20.10"), TIERS) == D("20.90")
    assert round_down(D("1.21"), TIERS) == D("1.19")
    assert round_down(D("15.30"), TIERS) == D("14.99")


def test_margin_math():
    # 1.20 incl. 25 % VAT = 0.96 net; purchase 0.40 -> 58.3 %
    assert round(margin_pct(D("1.20"), D("25"), D("0.40")), 1) == D("58.3")
    assert min_gross_for_margin(D("0.60"), D("25"), D("40")) == D("1.25")


def _proposals(session):
    return list(session.scalars(select(Action).where(Action.type == ActionType.PRICE_CHANGE)))


def test_no_floor_no_proposals(catalog, app_config):
    run = run_pricing(catalog, app_config, TODAY, NOW)
    assert _proposals(catalog) == []
    assert any("Minimalna marža" in f for f in run.summary["flags"])


def test_raise_when_margin_below_floor(catalog, priced):
    catalog.get(Product, "P4-0001").purchase_price_net = D("0.70")  # margin 27 %
    run_pricing(catalog, priced, TODAY, NOW)
    (action,) = _proposals(catalog)
    # min price for 30 %: 0.70 / 0.7 * 1.25 = 1.25 -> 1.29
    assert action.payload["new_retail_price_gross"] == "1.29"
    assert "ispod minimalne" in action.reasoning_summary
    assert action.data_used["reason_source"] == "template"


def test_competitor_decrease(catalog, priced):
    catalog.add(
        CompetitorPrice(
            sku="P4-0002",
            competitor="Market A",
            price_gross=D("11.00"),
            observed_on=date(2026, 11, 10),
            entered_by="owner",
        )
    )
    run_pricing(catalog, priced, TODAY, NOW)
    (action,) = _proposals(catalog)
    # 11.00 + 10 % = 12.10 -> 11.99, but the floor price 8 / 0.7 * 1.25 = 14.29 -> 14.49 wins
    assert action.payload["new_retail_price_gross"] == "14.49"


def test_competitor_decrease_respects_floor(catalog, priced):
    catalog.get(Product, "P4-0002").purchase_price_net = D("5.00")  # floor price 8.93 -> 8.99
    catalog.add(
        CompetitorPrice(
            sku="P4-0002",
            competitor="Market A",
            price_gross=D("11.00"),
            observed_on=date(2026, 11, 10),
            entered_by="owner",
        )
    )
    run_pricing(catalog, priced, TODAY, NOW)
    (action,) = _proposals(catalog)
    assert action.payload["new_retail_price_gross"] == "11.99"
    assert "Market A" in action.reasoning_summary
    assert "11,00 €" in action.reasoning_summary


def test_old_competitor_prices_ignored(catalog, priced):
    catalog.get(Product, "P4-0002").purchase_price_net = D("6.00")
    catalog.add(
        CompetitorPrice(
            sku="P4-0002",
            competitor="Market A",
            price_gross=D("11.00"),
            observed_on=date(2026, 9, 1),
            entered_by="owner",
        )
    )
    run_pricing(catalog, priced, TODAY, NOW)
    assert _proposals(catalog) == []


def test_validator_blocks_price_below_floor(catalog, priced):
    with pytest.raises(service.ProposalRejected, match="ispod minimalne"):
        service.propose(
            catalog,
            priced.business,
            action_type=ActionType.PRICE_CHANGE,
            title="x",
            payload={
                "sku": "P4-0001",
                "old_retail_price_gross": "1.20",
                "new_retail_price_gross": "0.69",
            },
            now=NOW,
        )


def test_validator_blocks_non_price_point(catalog, priced):
    with pytest.raises(service.ProposalRejected, match="cjenovna točka"):
        service.propose(
            catalog,
            priced.business,
            action_type=ActionType.PRICE_CHANGE,
            title="x",
            payload={
                "sku": "P4-0001",
                "old_retail_price_gross": "1.20",
                "new_retail_price_gross": "1.33",
            },
            now=NOW,
        )


# --- LLM-written reason (Anthropic client mocked) ------------------------------------


def _response(blocks, stop="tool_use"):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop,
        model="test-model",
        usage=SimpleNamespace(input_tokens=120, output_tokens=30, cache_read_input_tokens=0),
    )


def _tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _agent(app_config, responses):
    app_config.env.anthropic_model = "test-model"
    client = SimpleNamespace(messages=FakeMessages(responses))
    return Agent(app_config.env, client=client), client


def test_llm_reason_is_used_when_valid(catalog, priced):
    catalog.get(Product, "P4-0001").purchase_price_net = D("0.70")
    agent, client = _agent(
        priced,
        [
            _response([_tool_use("get_product", {"sku": "P4-0001"})]),
            _response(
                [
                    _tool_use(
                        "submit_reason",
                        {
                            "text": "Nabavna cijena je 0,70 € bez PDV-a, "
                            "pa uz 1,20 € marža pada ispod minimuma; "
                            "predlažem 1,29 €."
                        },
                        "t2",
                    )
                ]
            ),
        ],
    )
    run_pricing(catalog, priced, TODAY, NOW, agent=agent)
    (action,) = _proposals(catalog)
    assert action.data_used["reason_source"] == "llm"
    assert action.reasoning_summary.startswith("Nabavna cijena je 0,70 €")
    request = client.messages.requests[0]
    assert request["model"] == "test-model"
    assert all(tool["strict"] for tool in request["tools"])
    tool_result = client.messages.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and "0.70" in tool_result["content"]
    calls = list(catalog.scalars(select(AuditLog).where(AuditLog.event == "llm.call")))
    assert len(calls) == 2
    assert calls[0].input_tokens == 120 and calls[0].output_tokens == 30
    assert calls[0].details["tool_calls"][0]["name"] == "get_product"


def test_llm_reason_with_invented_number_is_rejected(catalog, priced):
    catalog.get(Product, "P4-0001").purchase_price_net = D("0.70")
    agent, _ = _agent(
        priced,
        [
            _response(
                [
                    _tool_use(
                        "submit_reason",
                        {"text": "Konkurencija prodaje po 0,99 €, predlažem 1,29 €."},
                    )
                ]
            ),
        ],
    )
    run_pricing(catalog, priced, TODAY, NOW, agent=agent)
    (action,) = _proposals(catalog)
    assert action.data_used["reason_source"] == "template"
    assert catalog.scalar(select(AuditLog).where(AuditLog.event == "llm.output_rejected"))


def test_no_model_configured_means_no_agent(app_config):
    from ducan.pricing.job import make_agent

    app_config.env.anthropic_model = None
    assert make_agent(app_config) is None
