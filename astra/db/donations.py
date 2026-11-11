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
        Upsert: running the job twice updates rows instead of duplicating them.
        A month's totals only ever grow, so keep the larger value: a run that
        got an incomplete answer from the API can't shrink what we already
        recorded."""
        if not rows:
            return
        await self._pool.executemany(
            """INSERT INTO donation_snapshots (account_tag, season, donated, received)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (account_tag, season) DO UPDATE
               SET donated  = GREATEST(donation_snapshots.donated,  EXCLUDED.donated),
                   received = GREATEST(donation_snapshots.received, EXCLUDED.received),
                   fetched_at = now()""",
            [(r.account_tag, season, r.donated, r.received) for r in rows],
        )

    async def leaderboard(self, season: str, *, include_inactive: bool = False) -> list[LeaderboardEntry]:
        """Per person, summed over all their accounts, most donated first.
        Same grouping as the old Donations sheet (by 'singular name').

        By default only people who are in a tracked clan, or who donated
        something this season, are listed; linked players from other clans
        would otherwise fill the bottom with zeros."""
        rows = await self._pool.fetch(
            """SELECT m.display_name AS name,
                      SUM(d.donated)::int  AS donated,
                      SUM(d.received)::int AS received
               FROM donation_snapshots d
               JOIN accounts a ON a.tag = d.account_tag
               JOIN members  m ON m.id  = a.member_id
               WHERE d.season = $1
               GROUP BY m.id, m.display_name
               HAVING $2
                   OR SUM(d.donated) > 0
                   OR EXISTS (SELECT 1 FROM clan_memberships cm
                              JOIN clans c ON c.tag = cm.clan_tag AND c.active
                              WHERE cm.member_id = m.id)
               ORDER BY donated DESC, received ASC, name""",
            season, include_inactive,
        )
        return [LeaderboardEntry(r["name"], r["donated"], r["received"]) for r in rows]
