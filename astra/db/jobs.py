"""Bookkeeping for scheduled jobs: one row per run in job_runs, plus a lock
so the same job never runs twice at once (e.g. a slow run overlapping the
next tick, or two bot instances during a deploy)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg


class JobRunsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @asynccontextmanager
    async def exclusive(self, job: str) -> AsyncIterator[bool]:
        """Yields True if this caller got the lock, False if the job is already
        running elsewhere. Postgres advisory locks belong to a connection, so we
        hold one connection for the duration; if the process dies, Postgres
        drops the connection and the lock with it, so a crash can't leave the
        job locked forever."""
        async with self._pool.acquire() as conn:
            got = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", f"job:{job}")
            try:
                yield got
            finally:
                if got:
                    await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", f"job:{job}")

    async def start(self, job: str) -> int:
        return await self._pool.fetchval("INSERT INTO job_runs (job) VALUES ($1) RETURNING id", job)

    async def finish(self, run_id: int, status: str, *, items_ok: int, items_failed: int,
                     error: str | None = None) -> None:
        await self._pool.execute(
            """UPDATE job_runs SET finished_at = now(), status = $2,
                      items_ok = $3, items_failed = $4, error = $5
               WHERE id = $1""",
            run_id, status, items_ok, items_failed, error,
        )

    async def last_success(self, job: str):
        """When this job last finished with data (ok or partial): for 'last updated' footers."""
        return await self._pool.fetchval(
            "SELECT max(finished_at) FROM job_runs WHERE job = $1 AND status IN ('ok', 'partial')", job
        )
