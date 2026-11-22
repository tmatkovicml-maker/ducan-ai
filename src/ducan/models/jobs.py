"""Record of each scheduled job run, including what the owner needs to resolve."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ducan.db import Base, UTCDateTime, utcnow


class JobRun(Base):
    """One run of a job such as nightly replenishment."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error
    # {"proposed": [action ids], "flags": ["text for the owner", ...], ...}
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
