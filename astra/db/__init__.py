"""Postgres connection pool and a small migration runner.

Migrations are plain numbered .sql files in astra/db/migrations/. Each one
runs once, inside a transaction, and is recorded in schema_migrations.
A Postgres advisory lock makes sure two bot instances starting at the same
time can't apply the same migration twice.
"""
from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_LOCK_ID = 0x41535452  # 'ASTR'; any constant shared by all instances


async def create_pool(database_url: str) -> asyncpg.Pool:
    # Small pool: one bot process, a handful of concurrent commands and jobs.
    return await asyncpg.create_pool(database_url, min_size=1, max_size=5, command_timeout=30)


def _migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    versions = [f.name.split("_", 1)[0] for f in files]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"Duplicate migration numbers in {MIGRATIONS_DIR}")
    return files


async def run_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply any migrations not yet applied. Returns the names it applied."""
    applied_now: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version     TEXT PRIMARY KEY,
                       applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for path in _migration_files():
                if path.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", path.name)
                log.info("Applied migration %s", path.name)
                applied_now.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)
    return applied_now
