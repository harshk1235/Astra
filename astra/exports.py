"""Postgres -> Google Sheets exports.

The sheet is only a view now: Postgres is the source of truth, and every tab
can be rebuilt at any time. It has five fixed tabs, all for the current month
except Members:

    Members    the roster: owner, Discord ID, ranks, every linked account
    All Data   one row per account with every number (see astra.alldata)
    Donations  per person, like the old Donations sheet
    War Stats  regular wars, like the old War Stats sheet
    CWL        this month's CWL, day by day

Older months are in Discord (/donos, /warstats, /cwlstats, /export with a
month option), so the sheet no longer grows a new tab every month.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

import asyncpg

from astra.alldata import all_data_rows, members_rows
from astra.db.donations import DonationsRepository, LeaderboardEntry
from astra.db.wars import CWL_DAYS, CwlEntry, WarsRepository, WarStatsEntry
from astra.months import month_key as season_key, month_start, next_month_start

MEMBERS_TAB = "Members"
ALL_DATA_TAB = "All Data"
CWL_TAB = "CWL"
DONATIONS_TAB = "Donations"
DONATIONS_HEADER = ["Name", "Donated", "Received"]
WAR_STATS_TAB = "War Stats"
WAR_STATS_HEADER = ["Player", "TH Level", "Total Attacks", "Total 3 Stars", "Hit Rate (%)", "Total Destruction"]


CWL_HEADER = (["Name", "Tag", "Clan", "TH", "Total Hits", "Triples", "Total Stars"]
              + [h for d in range(1, CWL_DAYS + 1) for h in (f"Day {d} Stars", f"Day {d} %")])


SHEET_TABS = [MEMBERS_TAB, ALL_DATA_TAB, DONATIONS_TAB, "War Stats", CWL_TAB]   # in display order


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
    now = now or datetime.now(timezone.utc)
    entries = await repo.cwl_stats(month_start(now), next_month_start(now))
    await sheets.write_rows(CWL_TAB, cwl_rows(entries))
    return len(entries)


async def export_members(sheets: SheetWriter, pool: asyncpg.Pool) -> int:
    rows = await members_rows(pool)
    await sheets.write_rows(MEMBERS_TAB, rows)
    return len(rows) - 1


async def export_all_data(sheets: SheetWriter, pool: asyncpg.Pool, now: datetime | None = None) -> int:
    rows = await all_data_rows(pool, month_start(now or datetime.now(timezone.utc)))
    await sheets.write_rows(ALL_DATA_TAB, rows)
    return len(rows) - 1


async def export_everything(sheets: SheetWriter, pool: asyncpg.Pool, now: datetime | None = None) -> dict[str, int]:
    """Write all five tabs. Returns rows written per tab."""
    wars = WarsRepository(pool)
    return {
        MEMBERS_TAB: await export_members(sheets, pool),
        ALL_DATA_TAB: await export_all_data(sheets, pool, now),
        DONATIONS_TAB: await export_donations(sheets, DonationsRepository(pool), now),
        WAR_STATS_TAB: await export_war_stats(sheets, wars, now),
        CWL_TAB: await export_cwl(sheets, wars, now),
    }


async def rebuild_sheet(sheets, pool: asyncpg.Pool, now: datetime | None = None) -> tuple[dict[str, int], list[str]]:
    """Write the five tabs, then delete every other tab (old monthly CWL tabs,
    Test_Sheet, Player Tags, ...). Returns (rows per tab, deleted tab names)."""
    written = await export_everything(sheets, pool, now)
    deleted = await sheets.keep_only(SHEET_TABS)
    return written, deleted
