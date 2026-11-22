"""POS adapter interface.

An adapter delivers batches of sales rows; :func:`ducan.pos.importer.import_batches`
stores them. Only these fields are ever kept: timestamp, sku, quantity, gross
amount and payment method category. No card numbers or personal data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ducan.models import PaymentMethod


@dataclass(frozen=True)
class SaleRow:
    """One parsed sales line."""

    ts: datetime  # timezone-aware
    sku: str
    quantity: Decimal
    gross_amount: Decimal
    payment_method: PaymentMethod


@dataclass
class SaleBatch:
    """A unit of import (e.g. one CSV file)."""

    source: str
    name: str
    sha256: str
    rows: list[tuple[int, SaleRow]] = field(default_factory=list)  # (row number, row)
    errors: list[dict[str, object]] = field(default_factory=list)  # [{"row": n, "error": ...}]
    ignored_columns: list[str] = field(default_factory=list)
    fatal_error: str | None = None


class PosAdapter(ABC):
    """Source of sales data."""

    source_name: str

    @abstractmethod
    def fetch(self) -> Iterator[SaleBatch]:
        """Yield batches that are ready to import."""

    @abstractmethod
    def acknowledge(self, batch: SaleBatch, *, accepted: bool) -> None:
        """Mark a batch as processed (e.g. move the file to the archive)."""
