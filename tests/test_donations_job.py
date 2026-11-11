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
from astra.jobs.donations import month_start, run_donations_job, season_key, sum_month

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "activity_summary.json").read_text())
NOW = datetime(2026, 11, 11, 13, 16, tzinfo=timezone.utc)


# --- pure helpers -------------------------------------------------------------

def test_month_start_and_season_are_utc():
    assert month_start(NOW) == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert season_key(NOW) == "2026-11"
    ist_just_after_midnight = datetime.fromisoformat("2026-12-01T00:30:00+05:30")  # still Nov in UTC
    assert season_key(ist_just_after_midnight) == "2026-11"


def test_sum_month_on_real_clashking_response():
    # Recorded from api.clashk.ing: rolling window from Oct 12 to Nov 11.
    assert sum_month(FIXTURE, month_start(NOW)) == (234, 355)


def test_sum_month_excludes_previous_month():
    payload = {"days": [
        {"from": "2026-10-31T00:00:00.000Z", "donated": "500", "received": "50"},
        {"from": "2026-11-01T00:00:00.000Z", "donated": "10", "received": "1"},
        {"from": "2026-11-02T00:00:00.000Z", "donated": "20", "received": "2"},
    ]}
    assert sum_month(payload, month_start(NOW)) == (30, 3)


def test_sum_month_tolerates_bad_days():
    payload = {"days": [
        {"from": "2026-11-01T00:00:00.000Z", "donated": "10", "received": "1"},
        {"from": "garbage", "donated": "999", "received": "999"},
        {"donated": "999"},
        {"from": "2026-11-02T00:00:00.000Z", "donated": None, "received": "4"},
    ]}
    assert sum_month(payload, month_start(NOW)) == (10, 5)
    assert sum_month({}, month_start(NOW)) == (0, 0)


# --- the job, against a real Postgres (skipped without TEST_DATABASE_URL) ------

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

ROSTER = [["#2Y0VPJUVJ", "HarshMain", "Harsh"], ["#28VUPJRPU", "HarshAlt", "Harsh"],
          ["#QQQQQQQQ", "BobAcc", "Bob"]]


class FakeClashKing:
    """Returns the recorded fixture for every player, except tags in `fail`."""
    def __init__(self, fail=()):
        self.fail, self.paths = set(fail), []

    async def get_json(self, path, params=None):
        self.paths.append(path)
        if any(t.replace("#", "%23") in path for t in self.fail):
            raise ClashKingError("HTTP 503 after retries")
        return FIXTURE


def with_db(test_fn):
    async def inner():
        pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
        try:
            await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await run_migrations(pool)
            await RosterRepository(pool).import_player_tags(ROSTER)
            await test_fn(pool, RosterRepository(pool), DonationsRepository(pool), JobRunsRepository(pool))
        finally:
            await pool.close()
    asyncio.run(inner())


@needs_db
def test_job_writes_month_totals_and_records_run():
    async def t(pool, roster, donations, jobs):
        client = FakeClashKing()
        result = await run_donations_job(client, roster, donations, jobs, now=NOW)
        assert (result.status, result.items_ok, result.items_failed) == ("ok", 3, 0)
        assert "/v2/player/%232Y0VPJUVJ/activity-summary" in client.paths
        board = await donations.leaderboard("2026-11")
        assert [(e.name, e.donated, e.received) for e in board] == [("Harsh", 468, 710), ("Bob", 234, 355)]
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
        every = {"#2Y0VPJUVJ", "#28VUPJRPU", "#QQQQQQQQ"}
        result = await run_donations_job(FakeClashKing(fail=every), roster, donations, jobs, now=NOW)
        assert result.status == "failed"
        # Leaderboard still serves the last good numbers.
        assert len(await donations.leaderboard("2026-11")) == 2
        assert await jobs.last_success("donations") is not None
    with_db(t)


@needs_db
def test_rerun_never_shrinks_a_month_total():
    async def t(pool, roster, donations, jobs):
        await donations.upsert_season("2026-11", [DonationRow("#QQQQQQQQ", 500, 50)])
        await donations.upsert_season("2026-11", [DonationRow("#QQQQQQQQ", 300, 60)])  # narrower window
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
