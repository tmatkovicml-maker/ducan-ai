"""CSV POS adapter: imports daily sales exports dropped into a watched folder.

Format (defined by this project until the POS vendor is known; see README):

* UTF-8 (a BOM is accepted), delimiter ``,`` or ``;``, header row required.
* Columns: ``timestamp, sku, quantity, gross_amount, payment_method``.
  Any other column is ignored and never stored (only its name is logged).
* ``timestamp``: ISO 8601. Without an offset it is read as local shop time.
* Decimals use a point or a comma. ``payment_method``: cash | card | other
  (Croatian ``gotovina`` / ``kartica`` / ``ostalo`` also accepted).
* A negative quantity is a refund.
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from ducan.models import PaymentMethod
from ducan.pos.base import PosAdapter, SaleBatch, SaleRow

REQUIRED = ("timestamp", "sku", "quantity", "gross_amount", "payment_method")
PAYMENT_ALIASES = {
    "cash": PaymentMethod.CASH,
    "gotovina": PaymentMethod.CASH,
    "card": PaymentMethod.CARD,
    "kartica": PaymentMethod.CARD,
    "other": PaymentMethod.OTHER,
    "ostalo": PaymentMethod.OTHER,
}


def _decimal(value: str) -> Decimal:
    return Decimal(value.strip().replace(" ", "").replace(",", "."))


def parse_csv(content: bytes, *, name: str, tz: str, source: str = "csv") -> SaleBatch:
    """Parse a CSV export into a :class:`SaleBatch` without touching the database."""
    batch = SaleBatch(source=source, name=name, sha256=hashlib.sha256(content).hexdigest())
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        batch.fatal_error = "Datoteka nije u UTF-8 kodiranju."
        return batch
    first_line = text.splitlines()[0] if text else ""
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        batch.fatal_error = f"Nedostaju stupci: {', '.join(missing)}"
        return batch
    batch.ignored_columns = [h for h in header if h not in REQUIRED]
    zone = ZoneInfo(tz)

    for row_no, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=zone)
            payment = PAYMENT_ALIASES.get(row["payment_method"].lower())
            if payment is None:
                raise ValueError("nepoznat način plaćanja")
            if not row["sku"]:
                raise ValueError("prazan SKU")
            quantity = _decimal(row["quantity"])
            if quantity == 0:
                raise ValueError("količina je 0")
            sale = SaleRow(
                ts=ts,
                sku=row["sku"],
                quantity=quantity,
                gross_amount=_decimal(row["gross_amount"]),
                payment_method=payment,
            )
        except (ValueError, InvalidOperation) as exc:
            # Only the row number and the reason are kept, never the raw row.
            batch.errors.append({"row": row_no, "error": str(exc) or "neispravan redak"})
            continue
        batch.rows.append((row_no, sale))
    return batch


class CsvPos(PosAdapter):
    """Reads ``*.csv`` files from ``inbox`` and moves them to ``archive`` or ``rejected``."""

    source_name = "csv"

    def __init__(self, inbox: Path, archive: Path, rejected: Path, tz: str) -> None:
        self.inbox, self.archive, self.rejected, self.tz = inbox, archive, rejected, tz
        for folder in (inbox, archive, rejected):
            folder.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, Path] = {}

    def fetch(self) -> Iterator[SaleBatch]:
        for path in sorted(self.inbox.glob("*.csv")):
            batch = parse_csv(
                path.read_bytes(), name=path.name, tz=self.tz, source=self.source_name
            )
            self._paths[batch.sha256] = path
            yield batch

    def acknowledge(self, batch: SaleBatch, *, accepted: bool) -> None:
        path = self._paths.pop(batch.sha256, None)
        if path is None or not path.exists():
            return
        target_dir = self.archive if accepted else self.rejected
        target = target_dir / path.name
        if target.exists():
            target = target_dir / f"{path.stem}-{batch.sha256[:8]}{path.suffix}"
        shutil.move(str(path), target)
