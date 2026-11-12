"""One row per linked account with every number the bot has for a month:
owner, ranks, TH, clan, donations, regular-war stats and CWL day by day.
This is the "All Data" sheet tab and the /export CSV, made to be dropped into
a spreadsheet or an AI tool as a single table.

Also the "Members" table: the roster itself.
"""
from __future__ import annotations

from datetime import datetime

import asyncpg

from astra.db.wars import CWL_DAYS, WarsRepository
from astra.months import month_key, next_month_start

MEMBERS_HEADER = ["Owner", "Discord ID", "Ranks", "Account", "Tag", "TH", "In-game Clan", "Verified"]
ALL_DATA_HEADER = (
    ["Month", "Owner", "Discord ID", "Ranks", "Account", "Tag", "TH", "In-game Clan",
     "Donated", "Received",
     "War Attacks", "War Triples", "War Hit Rate %", "War Destruction",
     "CWL Hits", "CWL Triples", "CWL Stars"]
    + [h for d in range(1, CWL_DAYS + 1) for h in (f"CWL Day {d} Stars", f"CWL Day {d} %")]
)

_ACCOUNTS_SQL = """
    SELECT m.display_name AS owner, m.discord_user_id, a.tag, a.name, a.th_level, a.verified,
           ic.name AS ingame_clan,
           (SELECT string_agg(c.short_name || ' ' || initcap(replace(cm.rank::text, '_', '-')), ', '
                              ORDER BY c.short_name)
              FROM clan_memberships cm JOIN clans c ON c.tag = cm.clan_tag AND c.active
             WHERE cm.member_id = m.id) AS ranks,
           EXISTS (SELECT 1 FROM clan_memberships cm JOIN clans c ON c.tag = cm.clan_tag AND c.active
                    WHERE cm.member_id = m.id AND ($1::text IS NULL OR cm.clan_tag = $1)) AS is_member
    FROM accounts a
    JOIN members m ON m.id = a.member_id
    LEFT JOIN clans ic ON ic.tag = a.ingame_clan_tag
    ORDER BY lower(m.display_name), a.th_level DESC NULLS LAST, lower(a.name)
"""


def _num(x: float) -> float | int:
    return int(x) if float(x).is_integer() else round(x, 2)


async def members_rows(pool: asyncpg.Pool) -> list[list]:
    rows = await pool.fetch(_ACCOUNTS_SQL, None)
    return [MEMBERS_HEADER] + [
        [r["owner"], str(r["discord_user_id"] or ""), r["ranks"] or "", r["name"], r["tag"],
         r["th_level"] or "", r["ingame_clan"] or "", "Yes" if r["verified"] else "No"]
        for r in rows
    ]


async def all_data_rows(pool: asyncpg.Pool, start: datetime, clan_tag: str | None = None) -> list[list]:
    """Accounts of people in the clan(s), plus any account with activity there
    this month. With clan_tag, every number counts that clan only."""
    end = next_month_start(start)
    accounts = await pool.fetch(_ACCOUNTS_SQL, clan_tag)
    donations = {r["account_tag"]: (r["donated"], r["received"]) for r in await pool.fetch(
        """SELECT account_tag, SUM(donated)::int AS donated, SUM(received)::int AS received
           FROM donation_snapshots d JOIN clans c ON c.tag = d.clan_tag AND c.active
           WHERE season = $1 AND ($2::text IS NULL OR d.clan_tag = $2) GROUP BY account_tag""",
        month_key(start), clan_tag)}
    wars_repo = WarsRepository(pool)
    wars = {e.tag: e for e in await wars_repo.war_stats(start, end, clan_tag)}
    cwl = {e.tag: e for e in await wars_repo.cwl_stats(start, end, clan_tag)}

    out = [ALL_DATA_HEADER]
    for r in accounts:
        tag = r["tag"]
        donated, received = donations.get(tag, (0, 0))
        w, c = wars.get(tag), cwl.get(tag)
        active = donated or received or w or c
        if not (r["is_member"] or active):
            continue
        row = [month_key(start), r["owner"], str(r["discord_user_id"] or ""), r["ranks"] or "", r["name"], tag,
               (c.th if c else w.th if w else r["th_level"]) or "", r["ingame_clan"] or "",
               donated, received]
        row += [w.attacks, w.triples, w.hit_rate, _num(w.destruction)] if w else [0, 0, "", 0]
        row += [c.hits, c.triples, c.stars] if c else [0, 0, 0]
        for d in range(1, CWL_DAYS + 1):
            stars, pct = c.days.get(d, ("", "")) if c else ("", "")
            row += [stars, _num(pct) if pct != "" else ""]
        out.append(row)
    return out
