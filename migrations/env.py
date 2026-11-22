"""Alembic environment. Reads DATABASE_URL from the environment / .env."""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

import ducan.models  # noqa: F401  (registers tables)
from ducan.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL")
    if not url and Path(".env").exists():
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    url = url or "sqlite:///data/ducan.sqlite3"
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(), target_metadata=Base.metadata, render_as_batch=True, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
