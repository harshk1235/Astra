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
from astra.db.wars import CWL_DAYS, CwlEntry, WarsRepository, WarStatsEntry
from astra.months import cwl_sheet_name, month_key as season_key, month_start, next_month_start

DONATIONS_TAB = "Donations"
DONATIONS_HEADER = ["Name", "Donated", "Received"]
WAR_STATS_TAB = "War Stats"
WAR_STATS_HEADER = ["Player", "TH Level", "Total Attacks", "Total 3 Stars", "Hit Rate (%)", "Total Destruction"]


CWL_HEADER = (["Name", "Tag", "Clan", "TH", "Total Hits", "Triples", "Total Stars"]
              + [h for d in range(1, CWL_DAYS + 1) for h in (f"Day {d} Stars", f"Day {d} %")])


class SheetWriter(Protocol):
    async def write_rows(self, worksheet: str, rows: list[list]) -> None: ...


def _number(x: float) -> float | int:
    return int(x) if float(x).is_integer() else round(x, 2)


def donation_rows(entries: list[LeaderboardEntry]) -> list[list]:
    return [DONATIONS_HEADER] + [[e.name, e.donated, e.received] for e in entries]


def war_stats_rows(entries: list[WarStatsEntry]) -> list[list]:
    return [WAR_STATS_HEADER] + [
        [e.name, e.th, e.attacks, e.triples, e.hit_rate, _number(e.destruction)] for e in entries
    ]


def cwl_rows(entries: list[CwlEntry]) -> list[list]:
    """The old CWL sheet's columns, minus Dips and Reaches (21 columns). A day
    with no attack is left blank, in its own column, so later days never
    shift left."""
    rows = [CWL_HEADER]
    for e in entries:
        row = [e.name, e.tag, e.clan, e.th, e.hits, e.triples, e.stars]
        for d in range(1, CWL_DAYS + 1):
            stars, pct = e.days.get(d, ("", ""))
            row += [stars, _number(pct) if pct != "" else ""]
        rows.append(row)
    return rows


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


async def export_cwl(sheets: SheetWriter, repo: WarsRepository, now: datetime | None = None) -> int:
    """Writes the month's 'Cwl <Mon><Year>' tab (created if missing)."""
    now = now or datetime.now(timezone.utc)
    entries = await repo.cwl_stats(month_start(now), next_month_start(now))
    await sheets.write_rows(cwl_sheet_name(now), cwl_rows(entries))
    return len(entries)
