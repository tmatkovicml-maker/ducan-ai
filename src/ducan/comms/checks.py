"""Checks for every public text the agent drafts (posts, review replies, chat answers).

On top of the reference validator:

* no promotions: the shop has no promotions table, so any discount or offer
  wording is an invented promotion and is rejected;
* clock times must be opening or closing times from ``opening_hours.yaml``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import time
from decimal import Decimal

from sqlalchemy.orm import Session

from ducan.config import BusinessConfig
from ducan.validation import ValidationResult
from ducan.validation.references import Draft, Reference, validate_draft

PROMO_RE = re.compile(
    r"%|\b(popust\w*|akcij\w*|snižen\w*|gratis|besplatn\w*|rasprodaj\w*|"
    r"discount\w*|sale|offer\w*|deal\w*|free|"
    r"rabatt\w*|angebot\w*|reduziert\w*|kostenlos\w*|"
    r"scont\w*|offert\w*|saldi|gratuit\w*)\b",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")


def opening_times(config: BusinessConfig) -> set[time]:
    """Every opening and closing time in the configured seasons."""
    times: set[time] = set()
    for season in config.staffing.seasons:
        for span in season.hours.values():
            start, end = span.split("-")
            times.update({time.fromisoformat(start), time.fromisoformat(end)})
    return times


def check_public_text(
    session: Session,
    text: str,
    references: list[Reference],
    config: BusinessConfig,
    extra_money: Iterable[Decimal] = (),
) -> ValidationResult:
    """Validate a public text; errors mean it must not be shown or proposed."""
    result = validate_draft(session, Draft(text=text, references=references), extra_money)
    for match in PROMO_RE.finditer(text):
        result.errors.append(
            f"Tekst spominje akciju ili popust ('{match.group(0)}'), a "
            "nijedna akcija nije upisana u sustav."
        )
        break
    allowed = opening_times(config)
    for match in TIME_RE.finditer(text):
        value = time(int(match.group(1)), int(match.group(2)))
        if value not in allowed:
            result.errors.append(f"Vrijeme {match.group(0)} nije u upisanom radnom vremenu.")
    return result
