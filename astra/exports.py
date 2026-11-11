"""Postgres -> Google Sheets exports, in the exact layouts the old Apps
Scripts produced. Leaders keep their familiar tabs, and any bot still reading
Sheets (e.g. the version on main) keeps working.

Sheets is only a view now: Postgres is the source of truth, and an export
can be re-run at any time to rebuild a tab.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from astra.db.donations import DonationsRepository, LeaderboardEntry
from astra.db.wars import WarsRepository, WarStatsEntry
from astra.jobs.donations import _next_month_start, month_start, season_key

DONATIONS_TAB = "Donations"
DONATIONS_HEADER = ["Name", "Donated", "Received"]
WAR_STATS_TAB = "War Stats"
WAR_STATS_HEADER = ["Player", "TH Level", "Total Attacks", "Total 3 Stars", "Hit Rate (%)", "Total Destruction"]


class SheetWriter(Protocol):
    async def write_rows(self, worksheet: str, rows: list[list]) -> None: ...


def next_month_start(now: datetime) -> datetime:
    return _next_month_start(month_start(now))


def _number(x: float) -> float | int:
    return int(x) if float(x).is_integer() else round(x, 2)


def donation_rows(entries: list[LeaderboardEntry]) -> list[list]:
    return [DONATIONS_HEADER] + [[e.name, e.donated, e.received] for e in entries]


def war_stats_rows(entries: list[WarStatsEntry]) -> list[list]:
    return [WAR_STATS_HEADER] + [
        [e.name, e.th, e.attacks, e.triples, e.hit_rate, _number(e.destruction)] for e in entries
    ]


async def export_donations(sheets: SheetWriter, repo: DonationsRepository, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    entries = await repo.leaderboard(season_key(now))
    await sheets.write_rows(DONATIONS_TAB, donation_rows(entries))
    return len(entries)


async def export_war_stats(sheets: SheetWriter, repo: WarsRepository, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    entries = await repo.war_stats(month_start(now), next_month_start(now))
    await sheets.write_rows(WAR_STATS_TAB, war_stats_rows(entries))
    return len(entries)
