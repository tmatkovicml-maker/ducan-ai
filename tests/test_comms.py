"""Phase 4: public texts, posts, review replies, customer chat, transparency."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from ducan.actions import service
from ducan.brand import Brand
from ducan.comms.chat import answer
from ducan.comms.checks import check_public_text
from ducan.comms.disclosure import DISCLOSURE, with_disclosure
from ducan.comms.posts import PostNotDrafted, draft_post
from ducan.comms.reviews import ReplyNotDrafted, draft_reply
from ducan.comms.sign import window_sign
from ducan.config import Staffing
from ducan.models import (
    Action,
    ActionType,
    AuditLog,
    ChatEscalation,
    ContentCalendarEntry,
    FaqEntry,
    Review,
)
from ducan.validation.references import Reference
from tests.conftest import NOW
from tests.fakes import fake_agent, response, tool_use

TODAY = date(2026, 11, 18)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
HOURS = Staffing.model_validate(
    {
        "seasons": [
            {
                "name": "Godina",
                "from": "01-01",
                "to": "12-31",
                "hours": {d: "07:00-21:00" for d in ("mon", "tue", "wed", "thu", "fri", "sat")},
            }
        ]
    }
)


@pytest.fixture
def cfg(app_config):
    app_config.business.staffing = HOURS
    return app_config


def water_ref():
    return [
        Reference(
            kind="product",
            id="P4-0001",
            fields={"name_hr": "Mineralna voda 0,5 l", "retail_price_gross": "1.20"},
        )
    ]


# --- public text checks ----------------------------------------------------------------


def test_disclosure_added_once():
    text = with_disclosure(with_disclosure("Bok!", "de"), "de")
    assert text.count(DISCLOSURE["de"]) == 1


def test_public_text_rules(catalog, cfg):
    business = cfg.business
    ok = check_public_text(
        catalog, "Mineralna voda 0,5 l je 1,20 €. Otvoreno od 07:00.", water_ref(), business
    )
    assert ok.ok, ok.errors
    promo = check_public_text(catalog, "Super popust na vodu!", [], business)
    assert any("popust" in e for e in promo.errors)
    percent = check_public_text(catalog, "Save 20% today", [], business)
    assert not percent.ok
    wrong_time = check_public_text(catalog, "We are open until 23:00.", [], business)
    assert any("23:00" in e for e in wrong_time.errors)


# --- chat ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "lang"),
    [
        ("Jesi li ti robot?", "hr"),
        ("Are you a real person?", "en"),
        ("Sind Sie ein Mensch?", "de"),
        ("Sei un bot?", "it"),
    ],
)
def test_chat_admits_being_ai_without_model_call(catalog, cfg, message, lang):
    agent, client = fake_agent(cfg, [])
    reply = answer(catalog, cfg.business, message, None, agent, TODAY)
    assert reply.source == "identity" and reply.language == lang
    assert ("AI" in reply.text) or ("KI" in reply.text) or ("IA" in reply.text)
    assert client.messages.requests == []


def test_chat_faq_fallback_uses_only_reviewed_answers(catalog, cfg):
    catalog.add_all(
        [
            FaqEntry(
                question_hr="Mogu li platiti karticom?",
                keywords="card karte",
                answers={"hr": "Da, primamo kartice.", "en": "Yes, we accept cards."},
                reviewed=True,
            ),
            FaqEntry(
                question_hr="Imate li parking?", answers={"hr": "Nepotvrđeno."}, reviewed=False
            ),
        ]
    )
    catalog.flush()
    reply = answer(catalog, cfg.business, "Can I pay by card?", "en", None, TODAY)
    assert reply.source == "faq" and reply.text.startswith("Yes, we accept cards.")
    assert DISCLOSURE["en"] in reply.text
    unknown = answer(catalog, cfg.business, "Imate li parking?", "hr", None, TODAY)
    assert unknown.escalated
    assert catalog.scalar(select(ChatEscalation)).question == "Imate li parking?"


def test_chat_llm_answer_from_tools(catalog, cfg):
    agent, client = fake_agent(
        cfg,
        [
            response([tool_use("find_products", {"query": "voda"})]),
            response(
                [
                    tool_use(
                        "answer",
                        {"text": "Da, Mineralna voda 0,5 l je 1,20 €.", "skus": ["P4-0001"]},
                        "t2",
                    )
                ]
            ),
        ],
    )
    reply = answer(catalog, cfg.business, "Imate li vode?", "hr", agent, TODAY)
    assert reply.source == "llm" and "1,20 €" in reply.text
    found = client.messages.requests[1]["messages"][-1]["content"][0]["content"]
    assert "P4-0001" in found and "in_stock" in found


def test_chat_invented_price_is_escalated(catalog, cfg):
    agent, _ = fake_agent(
        cfg,
        [
            response(
                [
                    tool_use(
                        "answer", {"text": "Mineralna voda 0,5 l je 0,50 €.", "skus": ["P4-0001"]}
                    )
                ]
            ),
        ],
    )
    reply = answer(catalog, cfg.business, "Koliko je voda?", "hr", agent, TODAY)
    assert reply.escalated
    assert catalog.scalar(select(AuditLog).where(AuditLog.event == "llm.output_rejected"))


def test_chat_model_can_escalate(catalog, cfg):
    agent, _ = fake_agent(
        cfg,
        [
            response([tool_use("escalate", {"reason": "Pitanje o reklamaciji"})]),
        ],
    )
    reply = answer(catalog, cfg.business, "Želim vratiti robu", "hr", agent, TODAY)
    assert reply.escalated
    assert catalog.scalar(select(ChatEscalation)).reason == "Pitanje o reklamaciji"


# --- social posts ----------------------------------------------------------------------


def _entry(session, **kw):
    entry = ContentCalendarEntry(
        publish_on=date(2026, 11, 20),
        topic="product",
        skus=["P4-0001"],
        languages=["hr", "en"],
        **kw,
    )
    session.add(entry)
    session.flush()
    return entry


def _brand(cfg):
    return Brand(root=cfg.env.brand_dir, name=None)


def test_post_drafted_validated_and_queued(catalog, cfg):
    agent, _ = fake_agent(
        cfg,
        [
            response(
                [
                    tool_use(
                        "submit_post",
                        {
                            "hr": "Hladna Mineralna voda 0,5 l za 1,20 € čeka vas "
                            "na rivi od 07:00.",
                            "en": "Cold Mineralna voda 0,5 l for 1,20 € on the riva from 07:00.",
                        },
                    )
                ]
            )
        ],
    )
    entry = _entry(catalog)
    action = draft_post(catalog, cfg.business, _brand(cfg), entry, agent, TODAY)
    assert action.type == ActionType.SOCIAL_POST and entry.action_id == action.id
    assert action.payload["texts"]["en"].endswith(DISCLOSURE["en"])
    assert action.payload["brand_assets"] == []


def test_post_with_invented_promotion_is_not_queued(catalog, cfg):
    agent, _ = fake_agent(
        cfg,
        [
            response(
                [
                    tool_use(
                        "submit_post", {"hr": "Akcija! Voda samo danas.", "en": "Big sale today!"}
                    )
                ]
            )
        ],
    )
    with pytest.raises(PostNotDrafted, match="akciju"):
        draft_post(catalog, cfg.business, _brand(cfg), _entry(catalog), agent, TODAY)
    assert catalog.scalar(select(Action)) is None


def test_post_needs_model_and_existing_brand_assets(catalog, cfg):
    with pytest.raises(PostNotDrafted, match="AI model"):
        draft_post(catalog, cfg.business, _brand(cfg), _entry(catalog), None, TODAY)
    agent, _ = fake_agent(cfg, [])
    with pytest.raises(PostNotDrafted, match="brand"):
        draft_post(
            catalog,
            cfg.business,
            _brand(cfg),
            _entry(catalog, brand_assets=["logo_png"]),
            agent,
            TODAY,
        )


def test_tampered_post_rejected_by_queue(catalog, cfg):
    with pytest.raises(service.ProposalRejected):
        service.propose(
            catalog,
            cfg.business,
            action_type=ActionType.SOCIAL_POST,
            title="x",
            payload={"texts": {"hr": "Rasprodaja svega!"}},
            now=NOW,
        )


# --- review replies --------------------------------------------------------------------


def _review(session, stars=5, text="Super dućan, ljubazno osoblje!"):
    review = Review(stars=stars, text=text, language="hr")
    session.add(review)
    session.flush()
    return review


def test_review_reply_drafted(catalog, cfg):
    agent, client = fake_agent(
        cfg,
        [
            response(
                [
                    tool_use(
                        "submit_reply",
                        {
                            "text": "Hvala vam od srca na lijepim riječima! "
                            "Veselimo se vašem sljedećem dolasku."
                        },
                    )
                ]
            )
        ],
    )
    review = _review(catalog)
    action = draft_reply(catalog, cfg.business, review, agent, TODAY)
    assert action.type == ActionType.REVIEW_REPLY and action.risk == "low"
    assert action.payload["texts"]["hr"].endswith(DISCLOSURE["hr"])
    assert "never defensive" in client.messages.requests[0]["system"]


def test_review_reply_with_promise_rejected(catalog, cfg):
    agent, _ = fake_agent(
        cfg,
        [
            response(
                [
                    tool_use(
                        "submit_reply", {"text": "Žao nam je, sljedeći put dobivate popust od 10%."}
                    )
                ]
            )
        ],
    )
    with pytest.raises(ReplyNotDrafted):
        draft_reply(catalog, cfg.business, _review(catalog, 2, "Loše."), agent, TODAY)


def test_low_rating_reply_is_not_low_risk(catalog, cfg):
    agent, _ = fake_agent(
        cfg,
        [
            response(
                [
                    tool_use(
                        "submit_reply",
                        {"text": "Hvala na povratnoj informaciji, provjerit ćemo što se dogodilo."},
                    )
                ]
            )
        ],
    )
    action = draft_reply(catalog, cfg.business, _review(catalog, 2, "Loše."), agent, TODAY)
    assert action.risk == "normal"


# --- transparency ----------------------------------------------------------------------


def test_window_sign_pdf(cfg):
    from pathlib import Path

    pdf = window_sign(_brand(cfg), Path(FONT))
    assert pdf.startswith(b"%PDF")


def test_chat_language_follows_the_message_when_clear():
    from ducan.comms.chat import detect_language

    assert detect_language("Mogu li platiti karticom?", "en") == "hr"
    assert detect_language("Wann haben Sie geöffnet?", "hr") == "de"
    assert detect_language("ok", "it") == "it"
    assert detect_language("ok", None) == "hr"
