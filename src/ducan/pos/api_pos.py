"""Placeholder for a POS vendor API adapter, to be implemented once the vendor is known."""

from __future__ import annotations

from collections.abc import Iterator

from ducan.pos.base import PosAdapter, SaleBatch


class ApiPos(PosAdapter):
    """Stub. Implement ``fetch`` / ``acknowledge`` against the vendor's API."""

    source_name = "api"

    def fetch(self) -> Iterator[SaleBatch]:
        raise NotImplementedError("POS API adapter is not implemented yet (vendor unknown).")

    def acknowledge(self, batch: SaleBatch, *, accepted: bool) -> None:
        raise NotImplementedError("POS API adapter is not implemented yet (vendor unknown).")
