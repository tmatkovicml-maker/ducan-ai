"""AI disclosure texts. Added by code and templates, never left to the language model."""

from __future__ import annotations

DISCLOSURE_HR = "Poslovanje dućana vodi AI uz nadzor ljudi."

# The same sentence for customers who do not read Croatian (shop window, chat, posts).
DISCLOSURE = {
    "hr": DISCLOSURE_HR,
    "en": "This shop is run by an AI under human supervision.",
    "de": "Dieses Geschäft wird von einer KI unter menschlicher Aufsicht geführt.",
    "it": "Questo negozio è gestito da un'intelligenza artificiale sotto supervisione umana.",
}

# Truthful, immediate answer when someone asks whether they are talking to an AI.
AI_IDENTITY_ANSWER = {
    "hr": "Da, ja sam AI asistent dućana, a ne čovjek. Poslovanje dućana vodi AI uz nadzor "
    "ljudi; ako želite razgovarati s osobom, javite se osoblju u dućanu.",
    "en": "Yes, I am the shop's AI assistant, not a person. This shop is run by an AI under "
    "human supervision; if you would like to talk to a person, please ask the staff in "
    "the shop.",
    "de": "Ja, ich bin der KI-Assistent des Geschäfts, kein Mensch. Dieses Geschäft wird von "
    "einer KI unter menschlicher Aufsicht geführt; wenn Sie mit einem Menschen sprechen "
    "möchten, wenden Sie sich bitte an das Personal im Geschäft.",
    "it": "Sì, sono l'assistente IA del negozio, non una persona. Questo negozio è gestito da "
    "un'intelligenza artificiale sotto supervisione umana; se desidera parlare con una "
    "persona, si rivolga al personale in negozio.",
}

# Short texts for places the owner publishes himself.
JOB_AD_TEXT = (
    "Napomena: planiranje, narudžbe, cijene i raspored smjena u ovom dućanu priprema AI sustav, "
    "a svaku odluku odobrava vlasnik. AI ne prati rad zaposlenika i ne odlučuje o zapošljavanju."
)
SUPPLIER_TEXT = (
    "Narudžbe i poruke koje primate od nas priprema AI sustav, a prije slanja ih odobrava "
    "vlasnik. Za sve dogovore odgovara vlasnik dućana."
)
CHAT_INTRO = {
    "hr": "Bok! Ja sam AI asistent dućana. Odgovaram samo na temelju provjerenih podataka; kad "
    "ne znam, proslijedim pitanje osoblju.",
    "en": "Hi! I am the shop's AI assistant. I only answer from checked information; when I do "
    "not know, I pass your question on to the staff.",
    "de": "Hallo! Ich bin der KI-Assistent des Geschäfts. Ich antworte nur mit geprüften "
    "Informationen; wenn ich etwas nicht weiß, gebe ich Ihre Frage an das Personal weiter.",
    "it": "Ciao! Sono l'assistente IA del negozio. Rispondo solo con informazioni verificate; "
    "quando non so qualcosa, inoltro la domanda al personale.",
}


def with_disclosure(text: str, language: str = "hr") -> str:
    """Append the disclosure line in the given language (once)."""
    line = DISCLOSURE.get(language, DISCLOSURE_HR)
    text = text.rstrip()
    return text if text.endswith(line) else f"{text}\n\n{line}"
