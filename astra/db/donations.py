"""Donation snapshots and the donation leaderboard query."""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class DonationRow:
    account_tag: str
    clan_tag: str        # the clan the donations were made in
    donated: int
    received: int


@dataclass(frozen=True)
class LeaderboardEntry:
    name: str        # the person's display name (all their accounts summed)
    donated: int
    received: int
    discord_id: int | None = None


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
            """INSERT INTO donation_snapshots (account_tag, season, clan_tag, donated, received)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (account_tag, season, clan_tag) DO UPDATE
               SET donated  = GREATEST(donation_snapshots.donated,  EXCLUDED.donated),
                   received = GREATEST(donation_snapshots.received, EXCLUDED.received),
                   fetched_at = now()""",
            [(r.account_tag, season, r.clan_tag, r.donated, r.received) for r in rows],
        )

    async def leaderboard(self, season: str, *, clan_tag: str | None = None,
                          include_inactive: bool = False) -> list[LeaderboardEntry]:
        """Per person, summed over all their accounts, most donated first.
        Same grouping as the old Donations sheet (by 'singular name').

        clan_tag: only donations made in that clan, and only that clan's
        members. Otherwise all tracked clans.
        By default only people who are in a tracked clan (or that clan), or
        who donated something, are listed; linked players from other clans
        would otherwise fill the bottom with zeros."""
        rows = await self._pool.fetch(
            """SELECT m.display_name AS name, m.discord_user_id,
                      SUM(d.donated)::int  AS donated,
                      SUM(d.received)::int AS received
               FROM donation_snapshots d
               JOIN clans c    ON c.tag = d.clan_tag AND c.active
               JOIN accounts a ON a.tag = d.account_tag
               JOIN members  m ON m.id  = a.member_id
               WHERE d.season = $1 AND ($2::text IS NULL OR d.clan_tag = $2)
               GROUP BY m.id, m.display_name, m.discord_user_id
               HAVING $3
                   OR SUM(d.donated) > 0
                   OR EXISTS (SELECT 1 FROM clan_memberships cm
                              JOIN clans c2 ON c2.tag = cm.clan_tag AND c2.active
                              WHERE cm.member_id = m.id AND ($2::text IS NULL OR cm.clan_tag = $2))
               ORDER BY donated DESC, received ASC, name""",
            season, clan_tag, include_inactive,
        )
        return [LeaderboardEntry(r["name"], r["donated"], r["received"], r["discord_user_id"]) for r in rows]

    async def account_leaderboard(self, season: str, *, clan_tag: str | None = None) -> list["AccountDonations"]:
        return await account_leaderboard(self._pool, season, clan_tag=clan_tag)


@dataclass(frozen=True)
class AccountDonations:
    tag: str
    name: str        # the Clash of Clans account name
    donated: int
    received: int
    discord_id: int | None


async def account_leaderboard(pool: asyncpg.Pool, season: str, *, clan_tag: str | None = None
                              ) -> list[AccountDonations]:
    """Per Clash of Clans account (not summed per person), most donated first.
    Same filter as the per-person board: accounts of people in the clan(s),
    plus any account that donated something."""
    rows = await pool.fetch(
        """SELECT a.tag, a.name, m.discord_user_id,
                  SUM(d.donated)::int  AS donated,
                  SUM(d.received)::int AS received
           FROM donation_snapshots d
           JOIN clans c    ON c.tag = d.clan_tag AND c.active
           JOIN accounts a ON a.tag = d.account_tag
           JOIN members  m ON m.id  = a.member_id
           WHERE d.season = $1 AND ($2::text IS NULL OR d.clan_tag = $2)
           GROUP BY a.tag, a.name, m.id, m.discord_user_id
           HAVING SUM(d.donated) > 0
               OR EXISTS (SELECT 1 FROM clan_memberships cm
                          JOIN clans c2 ON c2.tag = cm.clan_tag AND c2.active
                          WHERE cm.member_id = m.id AND ($2::text IS NULL OR cm.clan_tag = $2))
           ORDER BY donated DESC, received ASC, a.name""",
        season, clan_tag,
    )
    return [AccountDonations(r["tag"], r["name"], r["donated"], r["received"], r["discord_user_id"]) for r in rows]
