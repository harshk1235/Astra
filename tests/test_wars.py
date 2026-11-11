import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest

from astra.clashking import ClashKingError
from astra.db import run_migrations
from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.db.wars import AttackRecord, WarRecord, WarsRepository
from astra.exports import next_month_start, war_stats_rows
from astra.jobs.wars import parse_coc_time, parse_war, run_wars_job

FIXTURES = Path(__file__).parent / "fixtures"
REGULAR = json.loads((FIXTURES / "war_regular.json").read_text())   # recorded from api.clashk.ing
CWL = json.loads((FIXTURES / "war_cwl.json").read_text())
PR, DOP = "#28VUPJRPU", "#2Y0VPJUVJ"
UTC = timezone.utc


# --- parsing --------------------------------------------------------------------

def test_parse_coc_time():
    assert parse_coc_time("20261017T225639.000Z") == datetime(2026, 10, 17, 22, 56, 39, tzinfo=UTC)


def test_parse_real_regular_war():
    war, attacks = parse_war(REGULAR, PR, "regular")
    assert (war.clan_tag, war.opponent_tag, war.war_type, war.cwl_war_tag) == (PR, "#2JLQ98UCR", "regular", None)
    assert war.start_time == datetime(2026, 10, 18, 21, 56, 39, tzinfo=UTC)
    assert len(attacks) == 56
    a = next(x for x in attacks if x.attack_order == 49)
    # defender TH is looked up from the opponent's roster
    assert (a.attacker_tag, a.attacker_th, a.defender_tag, a.defender_th, a.stars, a.destruction) == \
           ("#RC9PJJ0G", 18, "#L90Q8GGC2", 18, 3, 100.0)
    assert len({x.attack_order for x in attacks}) == 56


def test_parse_finds_our_side_even_if_listed_as_opponent():
    swapped = dict(REGULAR, clan=REGULAR["opponent"], opponent=REGULAR["clan"])
    _, attacks = parse_war(swapped, PR, "regular")
    assert len(attacks) == 56


def test_parse_cwl_keeps_war_tag():
    war, _ = parse_war(CWL, PR, "cwl")
    assert war.cwl_war_tag == "#8LRGG0YGL"


def test_parse_rejects_war_without_our_clan():
    with pytest.raises(ValueError):
        parse_war(REGULAR, DOP, "regular")


# --- leaderboard rules, against a real Postgres --------------------------------

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

ROSTER = [["#PYLQPYLQ", "Ace", "Harsh"], ["#CCCCCCCC", "Cee", "Bob"], ["#GGGGGGGG", "Gee", "Low"],
          ["#JJJJJJJJ", "Jay", "Jay"]]          # #LLLLLLLL attacks too but isn't linked


def war(prep_day, war_type="regular", month=11):
    t = datetime(2026, month, prep_day, 20, 0, tzinfo=UTC)
    return WarRecord(PR, war_type, None, "#2JLQ98UCR", "Opp", 15, "warEnded",
                     t, t.replace(day=prep_day + 1), t.replace(day=prep_day + 2))


def atk(order, tag, th, def_th, stars, dest):
    return AttackRecord(order, tag, None, th, "#QQQQQQQQ", def_th, stars, dest, 100)


def with_db(test_fn):
    async def inner():
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
        try:
            await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await run_migrations(pool)
            roster = RosterRepository(pool)
            await roster.upsert_clan(PR, "Phoenix Reborn", "PR")
            await roster.import_player_tags(ROSTER)
            await test_fn(pool, roster, WarsRepository(pool))
        finally:
            await pool.close()
    asyncio.run(inner())


NOV = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))


@needs_db
def test_war_stats_applies_old_sheet_rules():
    async def t(pool, roster, wars):
        await wars.upsert_war(war(10), [
            atk(1, "#PYLQPYLQ", 16, 16, 3, 100),   # counted, triple
            atk(2, "#PYLQPYLQ", 16, 17, 2, 85),    # counted: hitting up ("reach")
            atk(3, "#PYLQPYLQ", 16, 15, 3, 100),   # NOT counted: dip
            atk(4, "#CCCCCCCC", 17, 17, 3, 100),   # counted
            atk(5, "#GGGGGGGG", 12, 12, 3, 100),   # NOT counted: TH12
            atk(6, "#LLLLLLLL", 17, 17, 3, 100),   # not linked: not listed
            atk(7, "#JJJJJJJJ", 16, 16, 1, 50),
        ])
        await wars.upsert_war(war(12), [
            atk(1, "#PYLQPYLQ", 16, 16, 1, 60),
            atk(2, "#JJJJJJJJ", 16, 16, 3, 100),
            atk(3, "#JJJJJJJJ", 16, 16, 3, 100),
        ])
        await wars.upsert_war(war(2, "cwl"), [atk(1, "#PYLQPYLQ", 16, 16, 3, 100)])   # CWL: excluded
        await wars.upsert_war(war(20, month=10), [atk(1, "#PYLQPYLQ", 16, 16, 3, 100)])  # October

        board = [(e.name, e.th, e.attacks, e.triples, e.hit_rate, e.destruction) for e in await wars.war_stats(*NOV)]
        assert board == [
            ("Cee", 17, 1, 1, 100, 100.0),
            ("Jay", 16, 3, 2, 66, 250.0),     # 2/3 = 66.67% -> 66 (rounded down, like the sheet)
            ("Ace", 16, 3, 1, 33, 245.0),     # attacks 1, 2 and war 12's; dip and CWL excluded
        ]
        # Gee (only TH12 attacks) is left out instead of showing a row of zeros.
    with_db(t)


@needs_db
def test_th_is_from_latest_war():
    async def t(pool, roster, wars):
        await wars.upsert_war(war(10), [atk(1, "#PYLQPYLQ", 15, 15, 3, 100)])
        await wars.upsert_war(war(14), [atk(1, "#PYLQPYLQ", 16, 16, 3, 100)])   # upgraded
        assert [e.th for e in await wars.war_stats(*NOV)] == [16]
    with_db(t)


@needs_db
def test_refetching_a_war_updates_instead_of_duplicating():
    async def t(pool, roster, wars):
        in_war = war(10)
        await wars.upsert_war(in_war, [atk(1, "#PYLQPYLQ", 16, 16, 2, 80)])
        await wars.upsert_war(in_war, [atk(1, "#PYLQPYLQ", 16, 16, 2, 80), atk(2, "#CCCCCCCC", 17, 17, 3, 100)])
        assert await pool.fetchval("SELECT count(*) FROM wars") == 1
        assert await pool.fetchval("SELECT count(*) FROM war_attacks") == 2
    with_db(t)


@needs_db
def test_wars_job_saves_real_war_and_tolerates_a_bad_one():
    async def t(pool, roster, wars):
        broken = {"preparationStartTime": "garbage", "clan": {"tag": PR}, "opponent": {}}

        class Fake:
            calls = []
            async def get_json(self, path, params=None):
                self.calls.append((path, params))
                return {"items": [REGULAR, broken]}

        client = Fake()
        result = await run_wars_job(client, roster, wars, JobRunsRepository(pool),
                                    now=datetime(2026, 10, 25, tzinfo=UTC))
        assert (result.status, result.items_ok, result.items_failed) == ("partial", 1, 1)
        path, params = client.calls[0]
        assert path == "/v2/clan/%2328VUPJRPU/wars" and params["type"] == "random"
        assert await pool.fetchval("SELECT count(*) FROM war_attacks") == 56
    with_db(t)


@needs_db
def test_wars_job_api_down_is_failed():
    async def t(pool, roster, wars):
        class Down:
            async def get_json(self, path, params=None):
                raise ClashKingError("HTTP 503 after retries")
        result = await run_wars_job(Down(), roster, wars, JobRunsRepository(pool))
        assert result.status == "failed"
    with_db(t)


def test_war_stats_rows_layout_matches_old_sheet():
    from astra.db.wars import WarStatsEntry
    rows = war_stats_rows([WarStatsEntry("#A", "Ace", 16, 3, 1, 33, 245.0),
                           WarStatsEntry("#B", "Bee", 15, 2, 1, 50, 150.5)])
    assert rows == [["Player", "TH Level", "Total Attacks", "Total 3 Stars", "Hit Rate (%)", "Total Destruction"],
                    ["Ace", 16, 3, 1, 33, 245], ["Bee", 15, 2, 1, 50, 150.5]]


def test_next_month_start_handles_december():
    assert next_month_start(datetime(2026, 12, 20, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)
    assert next_month_start(datetime(2026, 11, 30, 23, 59, tzinfo=UTC)) == datetime(2026, 12, 1, tzinfo=UTC)
