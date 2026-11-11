"""Donation snapshots and the donation leaderboard query."""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class DonationRow:
    account_tag: str
    donated: int
    received: int


@dataclass(frozen=True)
class LeaderboardEntry:
    name: str        # the person's display name (all their accounts summed)
    donated: int
    received: int


class DonationsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert_season(self, season: str, rows: list[DonationRow]) -> None:
        """Write one season's numbers for many accounts in a single round trip.
        Upsert: running the job twice updates rows instead of duplicating them."""
        if not rows:
            return
        await self._pool.executemany(
            """INSERT INTO donation_snapshots (account_tag, season, donated, received)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (account_tag, season) DO UPDATE
               SET donated = EXCLUDED.donated, received = EXCLUDED.received, fetched_at = now()""",
            [(r.account_tag, season, r.donated, r.received) for r in rows],
        )

    async def leaderboard(self, season: str) -> list[LeaderboardEntry]:
        """Per person, summed over all their accounts, most donated first.
        Same grouping as the old Donations sheet (by 'singular name')."""
        rows = await self._pool.fetch(
            """SELECT m.display_name AS name,
                      SUM(d.donated)::int  AS donated,
                      SUM(d.received)::int AS received
               FROM donation_snapshots d
               JOIN accounts a ON a.tag = d.account_tag
               JOIN members  m ON m.id  = a.member_id
               WHERE d.season = $1
               GROUP BY m.id, m.display_name
               ORDER BY donated DESC, received ASC, name""",
            season,
        )
        return [LeaderboardEntry(r["name"], r["donated"], r["received"]) for r in rows]
