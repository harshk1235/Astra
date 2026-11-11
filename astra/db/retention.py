"""Delete data older than the retention window, to keep the database small."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from astra.months import month_key

JOB_RUNS_KEEP_DAYS = 60


@dataclass(frozen=True)
class CleanupResult:
    wars: int
    donation_rows: int
    job_runs: int


async def delete_before(pool: asyncpg.Pool, cutoff: datetime) -> CleanupResult:
    """Remove wars (and, by cascade, their attacks) and donation snapshots from
    before `cutoff` (a month start), plus job_runs older than 60 days.
    One transaction: either everything old goes, or nothing does."""
    async with pool.acquire() as conn, conn.transaction():
        wars = await conn.execute("DELETE FROM wars WHERE start_time < $1", cutoff)
        donations = await conn.execute("DELETE FROM donation_snapshots WHERE season < $1", month_key(cutoff))
        runs = await conn.execute(
            "DELETE FROM job_runs WHERE started_at < now() - make_interval(days => $1)", JOB_RUNS_KEEP_DAYS
        )
    count = lambda status: int(status.split()[-1])    # 'DELETE 12' -> 12
    return CleanupResult(count(wars), count(donations), count(runs))
