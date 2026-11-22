"""Customer FAQ chat (HR/EN/DE/IT) for the website widget.

Answers come only from the owner-reviewed FAQ and live product / opening-hours
tools. Asked whether it is an AI, it says so at once. When unsure it hands the
question to the staff instead of guessing. Every answer carries the disclosure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from ducan.agent.client import Agent, LLMUnavailable, Tool
from ducan.audit.log import record
from ducan.comms.checks import check_public_text
from ducan.comms.disclosure import AI_IDENTITY_ANSWER, with_disclosure
from ducan.comms.tools import faq_search, price_references, read_tools
from ducan.config import BusinessConfig
from ducan.models import ChatEscalation, Product
from ducan.validation.references import Reference

LANGUAGES = ("hr", "en", "de", "it")
MAX_MESSAGE = 500

IDENTITY_RE = re.compile(
    r"(jesi li|jeste li|je li ovo|razgovaram li s)\b.*\b(ai|a\.i\.|umjetn\w*|robot\w*|bot|"
    r"čovjek\w*|stvarn\w* osob\w*|stroj\w*)"
    r"|\bare you\b.*\b(ai|a\.i\.|bot|robot|human|real person|person|machine)"
    r"|\b(am i|is this)\b.*\b(talking|chatting|speaking)\b.*\b(ai|bot|robot|human|person)"
    r"|\b(bist du|sind sie|ist das)\b.*\b(ki|bot|roboter|mensch|maschine)"
    r"|\b(sei|è|sono)\b.*\b(ia|ai|bot|robot|umano|persona|macchina)",
    re.IGNORECASE,
)

ESCALATED = {
    "hr": "Na to ne znam pouzdan odgovor. Proslijedio sam pitanje osoblju; najbrže ćete "
    "dobiti odgovor u dućanu.",
    "en": "I don't have a reliable answer to that. I have passed your question on to the "
    "staff; the quickest way to get an answer is to ask in the shop.",
    "de": "Darauf habe ich keine verlässliche Antwort. Ich habe Ihre Frage an das Personal "
    "weitergeleitet; am schnellsten erhalten Sie eine Antwort im Geschäft.",
    "it": "Non ho una risposta affidabile a questa domanda. L'ho inoltrata al personale; il "
    "modo più rapido per avere una risposta è chiedere in negozio.",
}

SYSTEM = (
    "You are the website chat assistant of a small shop on the waterfront in Mali Lošinj, "
    "Croatia. You are an AI and never pretend otherwise. Answer in the customer's language "
    "({lang}), in one to three short sentences, only with facts from the tools: the "
    "owner-reviewed FAQ, product facts (name, price, in stock yes/no) and opening hours. "
    "Never invent products, prices, stock, promotions, delivery, services or opening hours. "
    "Keep product names in Croatian as given, prices with a decimal comma and the € sign, "
    "times as HH:MM. If the tools do not clearly answer the question, or it asks for "
    "something personal, a complaint, an order, a reservation, medical or legal advice, call "
    "escalate instead of answering. Otherwise call answer with the text and the SKUs of any "
    "products you mention."
)


@dataclass
class ChatReply:
    """What the widget shows."""

    text: str
    language: str
    escalated: bool
    source: str  # identity | llm | faq | escalation


LANG_WORDS = {
    "hr": {
        "je",
        "li",
        "imate",
        "radite",
        "koliko",
        "gdje",
        "što",
        "jesi",
        "jeste",
        "mogu",
        "ima",
        "kada",
        "otvoreno",
        "hvala",
        "molim",
    },
    "en": {
        "are",
        "you",
        "is",
        "what",
        "where",
        "can",
        "do",
        "have",
        "open",
        "the",
        "when",
        "how",
        "much",
        "please",
        "thanks",
    },
    "de": {
        "sind",
        "sie",
        "ist",
        "haben",
        "wann",
        "wo",
        "was",
        "bist",
        "du",
        "ich",
        "kann",
        "gibt",
        "es",
        "geöffnet",
        "danke",
        "bitte",
    },
    "it": {
        "sei",
        "siete",
        "è",
        "avete",
        "quando",
        "dove",
        "cosa",
        "quanto",
        "posso",
        "aperto",
        "grazie",
        "un",
        "una",
    },
}


def detect_language(text: str, hint: str | None) -> str:
    """The language the customer wrote in; the widget's choice decides when unclear.

    Default Croatian when there is neither a clear signal nor a valid hint.
    """
    words = re.findall(r"[a-zčćđšžäöüßàèéìòù']+", text.casefold())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in LANG_WORDS.items()}
    if re.search(r"[čćđšž]", text.casefold()):
        scores["hr"] += 2
    if re.search(r"[äöüß]", text.casefold()):
        scores["de"] += 2
    ranked = sorted(scores, key=lambda lang: -scores[lang])
    best, second = ranked[0], ranked[1]
    if scores[best] >= 2 and scores[best] > scores[second]:
        return best
    if hint in LANGUAGES:
        return hint  # type: ignore[return-value]
    return best if scores[best] > scores["hr"] else "hr"


def escalate(session: Session, message: str, language: str, reason: str) -> ChatReply:
    session.add(
        ChatEscalation(question=message[:MAX_MESSAGE], language=language, reason=reason[:200])
    )
    record(session, actor="agent", event="chat.escalated", summary=reason[:200])
    return ChatReply(with_disclosure(ESCALATED[language], language), language, True, "escalation")


def answer(
    session: Session,
    config: BusinessConfig,
    message: str,
    language_hint: str | None,
    agent: Agent | None,
    today: date,
) -> ChatReply:
    """Answer one customer message."""
    message = message.strip()[:MAX_MESSAGE]
    lang = detect_language(message, language_hint)
    if not message:
        return ChatReply("", lang, False, "empty")
    if IDENTITY_RE.search(message):
        return ChatReply(AI_IDENTITY_ANSWER[lang], lang, False, "identity")
    if agent is None:
        hits = faq_search(session, message, lang, limit=1)
        if hits:
            return ChatReply(with_disclosure(hits[0]["answer"], lang), lang, False, "faq")
        return escalate(session, message, lang, "Nema odgovora u FAQ-u (AI model nije postavljen).")
    final = [
        Tool(
            "answer",
            "Give the answer to the customer.",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "skus": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "skus"],
                "additionalProperties": False,
            },
        ),
        Tool(
            "escalate",
            "Hand the question to the staff when the tools do not answer it.",
            {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        ),
    ]
    try:
        run = agent.run(
            session,
            purpose="chat",
            system=SYSTEM.format(lang=lang),
            user=message,
            tools=[*read_tools(session, config, today), *final],
            submit_tool=("answer", "escalate"),
            max_turns=5,
        )
    except LLMUnavailable:
        return escalate(session, message, lang, "AI model nije dostupan.")
    if run.submitted is None or run.submitted_by != "answer":
        reason = (run.submitted or {}).get("reason") or "Model nije dao odgovor."
        return escalate(session, message, lang, str(reason))
    skus = [s for s in run.submitted.get("skus", []) if session.get(Product, s) is not None]
    text = with_disclosure(str(run.submitted.get("text", "")).strip(), lang)
    refs = [Reference.from_dict(r) for r in price_references(session, skus)]
    check = check_public_text(session, text, refs, config)
    if not check.ok:
        record(
            session,
            actor="system",
            event="llm.output_rejected",
            summary="Odgovor u chatu nije prošao provjeru",
            details={"text": text, "errors": check.errors},
        )
        return escalate(session, message, lang, "Odgovor nije prošao provjeru.")
    return ChatReply(text, lang, False, "llm")
