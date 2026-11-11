import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest

from astra.clashking import ClashKingError
from astra.db import run_migrations
from astra.db.donations import DonationRow, DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.jobs import donations as job
from astra.jobs.donations import fetch_month_events, month_start, run_donations_job, season_key, sum_events

FIXTURES = Path(__file__).parent / "fixtures"
REAL_DONATED = json.loads((FIXTURES / "history_donated.json").read_text())   # recorded from api.clashk.ing
REAL_RECEIVED = json.loads((FIXTURES / "history_received.json").read_text())
NOW = datetime(2026, 11, 11, 13, 16, tzinfo=timezone.utc)
PR, DOP, OTHER = "#28VUPJRPU", "#2Y0VPJUVJ", "#2R0CPC8PR"


def ev(clan, delta, t="2026-11-05T10:00:00.000Z", prev=0):
    return {"eventTime": t, "clanTag": clan, "statType": "donated", "previousValue": prev,
            "currentValue": prev + delta, "delta": delta}


# --- pure helpers -------------------------------------------------------------

def test_month_start_and_season_are_utc():
    assert month_start(NOW) == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert season_key(NOW) == "2026-11"
    ist_just_after_midnight = datetime.fromisoformat("2026-12-01T00:30:00+05:30")  # still Nov in UTC
    assert season_key(ist_just_after_midnight) == "2026-11"


def test_sum_events_on_real_clashking_response():
    assert sum_events(REAL_DONATED["items"], {PR}) == 234
    assert sum_events(REAL_RECEIVED["items"], {PR}) == 355
    assert sum_events(REAL_DONATED["items"], {DOP}) == 0      # none of it was in DOP


def test_sum_events_counts_only_tracked_clans():
    items = [ev(PR, 1100), ev(DOP, 2167), ev(OTHER, 3022)]
    assert sum_events(items, {PR, DOP}) == 3267
    assert sum_events(items, {PR}) == 1100


def test_sum_events_ignores_resets_and_bad_items():
    items = [ev(PR, 50), ev(PR, -300), ev(PR, 0), {"clanTag": PR, "delta": "x"}, {"delta": 99}, ev(PR, 7)]
    assert sum_events(items, {PR}) == 57


class PagingClient:
    """Serves `events` newest first, honouring limit and time[before]."""
    def __init__(self, events):
        self.events = sorted(events, key=lambda e: e["eventTime"], reverse=True)
        self.calls = []

    async def get_json(self, path, params=None):
        self.calls.append(dict(params))
        items = [e for e in self.events if not params.get("time[before]") or e["eventTime"] < params["time[before]"]]
        return {"items": items[: params["limit"]]}


def test_fetch_follows_pages(monkeypatch):
    monkeypatch.setattr(job, "PAGE_SIZE", 3)
    events = [ev(PR, 1, f"2026-11-0{d}T10:00:00.000Z", prev=d) for d in range(1, 9)]   # 8 events
    client = PagingClient(events)
    got = asyncio.run(fetch_month_events(client, PR, "donated", month_start(NOW)))
    assert len(got) == 8 and len(client.calls) == 3          # 3 + 3 + 2
    assert client.calls[0]["time[after]"] == "2026-11-01T00:00:00Z"
    assert "time[before]" in client.calls[1]


def test_fetch_gives_up_on_runaway_paging(monkeypatch):
    monkeypatch.setattr(job, "PAGE_SIZE", 1)
    monkeypatch.setattr(job, "MAX_PAGES", 3)

    class Endless:
        async def get_json(self, path, params=None):
            return {"items": [ev(PR, 1, "2026-11-05T10:00:00.000Z")]}
    with pytest.raises(ClashKingError, match="more than 3 pages"):
        asyncio.run(fetch_month_events(Endless(), PR, "donated", month_start(NOW)))


# --- the job, against a real Postgres (skipped without TEST_DATABASE_URL) ------

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

ROSTER = [["#2Y0VPJUVJ", "HarshMain", "Harsh"], ["#28VUPJRPU", "HarshAlt", "Harsh"],
          ["#QQQQQQQQ", "BobAcc", "Bob"]]

# Per account: (donated events, received events). Harsh's main donated in PR,
# his alt in DOP and in an outside clan; Bob's donations were all elsewhere.
EVENTS = {
    "#2Y0VPJUVJ": ([ev(PR, 1000)], [ev(PR, 200)]),
    "#28VUPJRPU": ([ev(DOP, 500), ev(OTHER, 4000)], [ev(DOP, 100)]),
    "#QQQQQQQQ": ([ev(OTHER, 1200)], [ev(OTHER, 900)]),
}


class FakeClashKing:
    def __init__(self, fail=()):
        self.fail, self.paths = set(fail), []

    async def get_json(self, path, params=None):
        self.paths.append(path)
        tag = next(t for t in EVENTS if t.replace("#", "%23") in path)
        if tag in self.fail:
            raise ClashKingError("HTTP 503 after retries")
        donated, received = EVENTS[tag]
        return {"items": donated if params["type"] == "donated" else received}


def with_db(test_fn, clans=((PR, "Phoenix Reborn", "PR"), (DOP, "DawnOfPhoenix", "DOP"))):
    async def inner():
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
        try:
            await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await run_migrations(pool)
            roster = RosterRepository(pool)
            for c in clans:
                await roster.upsert_clan(*c)
            await roster.import_player_tags(ROSTER)
            await test_fn(pool, roster, DonationsRepository(pool), JobRunsRepository(pool))
        finally:
            await pool.close()
    asyncio.run(inner())


@needs_db
def test_job_counts_only_donations_in_tracked_clans():
    async def t(pool, roster, donations, jobs):
        client = FakeClashKing()
        result = await run_donations_job(client, roster, donations, jobs, now=NOW)
        assert (result.status, result.items_ok, result.items_failed) == ("ok", 3, 0)
        assert "/v2/player/%232Y0VPJUVJ/history/stats" in client.paths
        board = await donations.leaderboard("2026-11")
        # Harsh: 1000 (PR) + 500 (DOP); the 4000 outside is ignored.
        # Bob donated nothing in PR/DOP and isn't a member, so he's hidden by default.
        assert [(e.name, e.donated, e.received) for e in board] == [("Harsh", 1500, 300)]
        everyone = await donations.leaderboard("2026-11", include_inactive=True)
        assert [(e.name, e.donated) for e in everyone] == [("Harsh", 1500), ("Bob", 0)]
        run = await pool.fetchrow("SELECT * FROM job_runs")
        assert (run["job"], run["status"], run["items_ok"], run["finished_at"] is not None) == \
               ("donations", "ok", 3, True)
    with_db(t)


@needs_db
def test_one_failing_account_makes_run_partial_not_failed():
    async def t(pool, roster, donations, jobs):
        result = await run_donations_job(FakeClashKing(fail={"#QQQQQQQQ"}), roster, donations, jobs, now=NOW)
        assert (result.status, result.items_ok, result.items_failed) == ("partial", 2, 1)
        assert [e.name for e in await donations.leaderboard("2026-11")] == ["Harsh"]
        assert "#QQQQQQQQ" in await pool.fetchval("SELECT error FROM job_runs")
    with_db(t)


@needs_db
def test_api_down_marks_run_failed_and_keeps_old_data():
    async def t(pool, roster, donations, jobs):
        await run_donations_job(FakeClashKing(), roster, donations, jobs, now=NOW)
        result = await run_donations_job(FakeClashKing(fail=set(EVENTS)), roster, donations, jobs, now=NOW)
        assert result.status == "failed"
        assert len(await donations.leaderboard("2026-11")) == 1   # still serving last good numbers
        assert await jobs.last_success("donations") is not None
    with_db(t)


@needs_db
def test_no_tracked_clans_skips_without_calling_api():
    async def t(pool, roster, donations, jobs):
        client = FakeClashKing()
        assert (await run_donations_job(client, roster, donations, jobs, now=NOW)).status == "skipped"
        assert client.paths == []
    with_db(t, clans=())


@needs_db
def test_rerun_never_shrinks_a_month_total():
    async def t(pool, roster, donations, jobs):
        await donations.upsert_season("2026-11", [DonationRow("#QQQQQQQQ", 500, 50)])
        await donations.upsert_season("2026-11", [DonationRow("#QQQQQQQQ", 300, 60)])  # incomplete answer
        row = await pool.fetchrow("SELECT donated, received FROM donation_snapshots")
        assert (row["donated"], row["received"]) == (500, 60)
    with_db(t)


@needs_db
def test_overlapping_runs_are_skipped():
    async def t(pool, roster, donations, jobs):
        async with jobs.exclusive("donations") as first:
            assert first is True
            result = await run_donations_job(FakeClashKing(), roster, donations, jobs, now=NOW)
            assert result.status == "skipped"
        assert await pool.fetchval("SELECT count(*) FROM job_runs") == 0
    with_db(t)
