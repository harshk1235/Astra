import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from astra.db import run_migrations
from astra.db.retention import delete_before
from astra.db.roster import RosterRepository
from astra.db.donations import DonationRow, DonationsRepository
from astra.db.wars import AttackRecord, CwlAttack, WarRecord, WarsRepository, build_cwl_entries
from astra.exports import CWL_HEADER, cwl_rows
from astra.jobs.wars import parse_war

UTC = timezone.utc
PR, DOP = "#28VUPJRPU", "#2Y0VPJUVJ"
CWL_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "war_cwl.json").read_text())


def att(tag, day, stars, pct=100.0, th=16, name=None, owner="Owner", clan="Phoenix Reborn", discord_id=None):
    return CwlAttack(tag, name or tag, owner, clan, day, th, stars, pct, discord_id)


# --- day mapping and totals (pure) -------------------------------------------------

def test_missed_day_stays_empty_instead_of_shifting():
    # Played days 1, 3, 4; missed day 2. The old script showed day 3's attack as "Day 2".
    [e] = build_cwl_entries([att("#P", 1, 3), att("#P", 3, 2, 85), att("#P", 4, 1, 50)])
    assert sorted(e.days) == [1, 3, 4]
    assert e.days[3] == (2, 85)
    assert (e.hits, e.triples, e.stars) == (3, 1, 6)


def test_sorted_by_th_then_triples_then_stars():
    entries = build_cwl_entries([
        att("#A", 1, 3, th=16), att("#A", 2, 3, th=16),
        att("#B", 1, 3, th=17), att("#B", 2, 1, th=17),
        att("#C", 1, 3, th=16), att("#C", 2, 2, th=16),
    ])
    assert [e.tag for e in entries] == ["#B", "#A", "#C"]


def test_th_from_latest_round_and_days_out_of_range_ignored():
    [e] = build_cwl_entries([att("#P", 1, 3, th=15), att("#P", 5, 3, th=16), att("#P", 9, 3)])
    assert e.th == 16 and sorted(e.days) == [1, 5]


def test_cwl_rows_layout_matches_old_sheet():
    [e] = build_cwl_entries([att("#P", 1, 3, 100, name="Acc", discord_id=7), att("#P", 3, 2, 85.5, name="Acc")])
    header, row = cwl_rows([e])
    assert header == CWL_HEADER and len(row) == 21        # Dips and Reaches are gone
    assert "Dips" not in header and header[-1] == "Day 7 %"
    assert row[:7] == ["Acc", "#P", "Phoenix Reborn", 16, 2, 1, 5]
    assert row[7:13] == [3, 100, "", "", 2, 85.5]       # Day 2 blank, in its own column
    assert e.discord_id == 7


# --- against a real Postgres --------------------------------------------------------

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


def cwl_round(clan, day, start=datetime(2026, 11, 1, 20, 0, tzinfo=UTC)):
    s = start + timedelta(days=day - 1)
    return WarRecord(clan, "cwl", f"#8L{day}", "#QQQQQQQQ", "Opp", 15, "warEnded",
                     s - timedelta(days=1), s, s + timedelta(days=1))


def a(order, tag, stars, th=16, def_th=16):
    return AttackRecord(order, tag, None, th, "#QQQQQQQQ", def_th, stars, 100.0 if stars == 3 else 70.0, 90)


def with_db(test_fn):
    async def inner():
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
        try:
            await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await run_migrations(pool)
            roster = RosterRepository(pool)
            await roster.upsert_clan(PR, "Phoenix Reborn", "PR")
            await roster.upsert_clan(DOP, "DawnOfPhoenix", "DOP")
            await roster.import_player_tags([["#PYLQPYLQ", "Ace", "Harsh"], ["#PYLQPYL2", "AceAlt", "Harsh"],
                                             ["#CCCCCCCC", "Cee", "Bob"]])
            await test_fn(pool, WarsRepository(pool))
        finally:
            await pool.close()
    asyncio.run(inner())


NOV = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))


@needs_db
def test_cwl_days_come_from_round_dates():
    async def t(pool, wars):
        # PR played rounds 1, 2, 4 (round 3 missing from the data entirely); DOP all from day 1.
        await wars.upsert_war(cwl_round(PR, 1), [a(1, "#PYLQPYLQ", 3)])
        await wars.upsert_war(cwl_round(PR, 2), [a(1, "#CCCCCCCC", 2)])
        await wars.upsert_war(cwl_round(PR, 4), [a(1, "#PYLQPYLQ", 2), a(2, "#CCCCCCCC", 3)])
        await wars.upsert_war(cwl_round(DOP, 1), [a(1, "#PYLQPYL2", 1, th=14, def_th=15)])
        await wars.upsert_war(cwl_round(PR, 1, start=datetime(2026, 10, 1, 20, 0, tzinfo=UTC)),
                              [a(1, "#PYLQPYLQ", 3)])                # October: not in November's board

        board = {e.tag: e for e in await wars.cwl_stats(*NOV)}
        assert sorted(board["#PYLQPYLQ"].days) == [1, 4]             # still day 4, not "day 3"
        assert sorted(board["#CCCCCCCC"].days) == [2, 4]
        assert board["#PYLQPYL2"].clan == "DawnOfPhoenix"
        assert board["#PYLQPYLQ"].owner == board["#PYLQPYL2"].owner == "Harsh"
        only_dop = await wars.cwl_stats(*NOV, clan_tag=DOP)
        assert [e.tag for e in only_dop] == ["#PYLQPYL2"]
    with_db(t)


@needs_db
def test_real_cwl_round_from_clashking():
    async def t(pool, wars):
        war, attacks = parse_war(CWL_FIXTURE, PR, "cwl")
        # Link two of the real attackers so they show up.
        tags = sorted({x.attacker_tag for x in attacks})[:2]
        await RosterRepository(pool).import_player_tags([[t, f"Real{i}", "Real"] for i, t in enumerate(tags)])
        await wars.upsert_war(war, attacks)
        m = war.start_time.replace(day=1, hour=0, minute=0, second=0)
        board = await wars.cwl_stats(m, (m + timedelta(days=32)).replace(day=1))
        assert {e.tag for e in board} == set(tags)
        assert all(list(e.days) == [1] for e in board)               # a single round = day 1
    with_db(t)


@needs_db
def test_retention_deletes_only_older_months():
    async def t(pool, wars):
        await wars.upsert_war(cwl_round(PR, 1, start=datetime(2026, 9, 2, tzinfo=UTC)), [a(1, "#PYLQPYLQ", 3)])
        await wars.upsert_war(cwl_round(PR, 1), [a(1, "#PYLQPYLQ", 3)])
        don = DonationsRepository(pool)
        await don.upsert_season("2026-09", [DonationRow("#PYLQPYLQ", PR, 5, 5)])
        await don.upsert_season("2026-11", [DonationRow("#PYLQPYLQ", PR, 9, 9)])

        r = await delete_before(pool, datetime(2026, 10, 1, tzinfo=UTC))
        assert (r.wars, r.donation_rows) == (1, 1)
        assert await pool.fetchval("SELECT count(*) FROM war_attacks") == 1      # attacks went with the war
        assert await pool.fetchval("SELECT season FROM donation_snapshots") == "2026-11"
    with_db(t)
