"""Reference validator: drafts may only state facts that exist in the database."""

from __future__ import annotations

import pytest

from ducan.actions import service
from ducan.models import ActionType
from ducan.validation.references import Draft, Reference, validate_draft
from tests.conftest import NOW


def _product(sku="P4-0001", **fields):
    return Reference(kind="product", id=sku, fields=fields)


def test_valid_draft_passes(catalog):
    draft = Draft(
        text="Mineralna voda 0,5 l sada je 1,20 €. Na zalihi: 30 kom.",
        references=[_product(retail_price_gross="1.20", stock="30")],
    )
    result = validate_draft(catalog, draft)
    assert result.ok, result.errors


def test_nonexistent_sku_is_rejected(catalog):
    draft = Draft(text="Novi proizvod stiže sutra.", references=[_product("P4-9999")])
    result = validate_draft(catalog, draft)
    assert not result.ok
    assert any("P4-9999" in e for e in result.errors)


def test_nonexistent_sku_blocks_proposal(catalog, business):
    with pytest.raises(service.ProposalRejected):
        service.propose(
            catalog,
            business,
            action_type=ActionType.SOCIAL_POST,
            title="Objava",
            payload={"draft_text": "Stiglo: P4-9999"},
            references=[{"kind": "product", "id": "P4-9999"}],
            now=NOW,
        )


def test_wrong_price_is_rejected(catalog):
    draft = Draft(text="Voda je 0,99 €.", references=[_product(retail_price_gross="0.99")])
    result = validate_draft(catalog, draft)
    assert any("1.20" in e for e in result.errors)


def test_amount_in_text_must_match_reference(catalog):
    draft = Draft(
        text="Mineralna voda 0,5 l samo 0,99 €!", references=[_product(retail_price_gross="1.20")]
    )
    result = validate_draft(catalog, draft)
    assert any("0,99" in e for e in result.errors)


def test_unreferenced_product_name_is_rejected(catalog):
    draft = Draft(text="Probajte naše Maslinovo ulje 0,5 l!", references=[])
    result = validate_draft(catalog, draft)
    assert any("Maslinovo ulje" in e for e in result.errors)


def test_unreferenced_sku_token_is_rejected(catalog):
    result = validate_draft(catalog, Draft(text="Artikl P4-0002 je u akciji.", references=[]))
    assert any("P4-0002" in e for e in result.errors)


def test_unknown_fact_cannot_be_claimed(catalog):
    # Razglednica has no price in the database; claiming one is inventing a fact.
    draft = Draft(text="Razglednica 1 €", references=[_product("P4-0003", retail_price_gross="1")])
    result = validate_draft(catalog, draft)
    assert any("nije poznat" in e for e in result.errors)


def test_quantity_must_match_stock(catalog):
    draft = Draft(text="Mineralna voda 0,5 l: još 50 kom.", references=[_product(stock="30")])
    result = validate_draft(catalog, draft)
    assert any("50 kom" in e for e in result.errors)


def test_nonexistent_supplier_is_rejected(catalog):
    draft = Draft(text="Dostava stiže.", references=[Reference(kind="supplier", id="42")])
    assert not validate_draft(catalog, draft).ok


def test_supplier_terms_checked(catalog):
    ok = Draft(
        text="Minimalna narudžba je 50 €.",
        references=[Reference(kind="supplier", id="1", fields={"min_order_net": "50"})],
    )
    assert validate_draft(catalog, ok).ok
    bad = Draft(
        text="Minimalna narudžba je 30 €.",
        references=[Reference(kind="supplier", id="1", fields={"min_order_net": "30"})],
    )
    assert not validate_draft(catalog, bad).ok
