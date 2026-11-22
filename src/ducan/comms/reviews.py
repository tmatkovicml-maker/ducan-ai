"""Reply drafts for Google reviews the owner pasted in."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ducan.actions import service
from ducan.agent.client import Agent, LLMUnavailable, Tool
from ducan.audit.log import record
from ducan.comms.checks import check_public_text
from ducan.comms.disclosure import with_disclosure
from ducan.comms.tools import read_tools
from ducan.config import BusinessConfig
from ducan.models import Action, ActionType, Review

SYSTEM = (
    "You draft the shop owner's public reply to a Google review of a small shop on the "
    "waterfront in Mali Lošinj, Croatia. Reply in the review's language. Be warm, short "
    "(two to four sentences), honest and never defensive: thank the person, acknowledge "
    "what they said, and for a complaint say the owner will look into it. Do not argue, do "
    "not blame the customer, do not promise refunds, discounts, gifts or changes the owner "
    "has not decided, and do not invent facts; use the tools if you mention products or "
    "opening hours. Do not repeat personal details from the review. Do not mention AI; the "
    "shop adds its own disclosure line. Call submit_reply with the text when done."
)


class ReplyNotDrafted(RuntimeError):
    """The reply could not be drafted; the message says why (in Croatian)."""


def draft_reply(
    session: Session, config: BusinessConfig, review: Review, agent: Agent | None, today: date
) -> Action:
    """Draft and queue a review_reply action."""
    if agent is None:
        raise ReplyNotDrafted(
            "AI model nije postavljen (ANTHROPIC_MODEL); odgovor nije pripremljen."
        )
    submit = Tool(
        "submit_reply",
        "Submit the reply text.",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    try:
        run = agent.run(
            session,
            purpose=f"review_reply #{review.id}",
            system=SYSTEM,
            user=f"Review ({review.stars} of 5 stars, language: {review.language}):\n{review.text}",
            tools=[*read_tools(session, config, today), submit],
            submit_tool="submit_reply",
        )
    except LLMUnavailable as exc:
        raise ReplyNotDrafted(f"AI model trenutno nije dostupan: {exc}") from exc
    text = (run.submitted or {}).get("text", "").strip()
    if not text:
        raise ReplyNotDrafted("Model nije predao odgovor.")
    text = with_disclosure(text, review.language)
    check = check_public_text(session, text, [], config)
    if not check.ok:
        record(
            session,
            actor="system",
            event="llm.output_rejected",
            summary=f"Odgovor na recenziju #{review.id} nije prošao provjeru",
            details={"text": text, "errors": check.errors},
        )
        raise ReplyNotDrafted("Nacrt nije prošao provjeru: " + "; ".join(check.errors[:3]))
    action = service.propose(
        session,
        config,
        action_type=ActionType.REVIEW_REPLY,
        title=f"{review.stars}★, {review.language.upper()}",
        payload={
            "review_id": review.id,
            "stars": review.stars,
            "language": review.language,
            "review_text": review.text,
            "texts": {review.language: text},
        },
        reasoning_summary="Kratak, topao odgovor bez obećanja koja vlasnik nije dao.",
        data_used={"review_id": review.id},
        proposed_by="agent",
    )
    review.action_id = action.id
    return action
