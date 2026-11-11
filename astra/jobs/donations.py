"""Donations job: ClashKing -> Postgres.

Source: GET /v2/player/{tag}/activity-summary, which returns per-day
donated/received totals for the rolling past 30 days. Summing the days that
start in the current calendar month (UTC) gives the month's totals, the same
"donations this month" the old Donations sheet showed.

Edge case: on the 31st of a month, the rolling 30-day window starts partway
through the 1st, so the morning of the 1st falls outside it. Because a
month's totals can only go up, the repository keeps the larger of the stored
and fetched values, so an earlier full reading is never overwritten by a
smaller one.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from astra.db.donations import DonationRow, DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.tags import url_tag

log = logging.getLogger(__name__)

JOB_NAME = "donations"


class JsonClient(Protocol):
    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


# --- pure helpers (unit tested without network or database) -----------------

def month_start(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def season_key(now: datetime) -> str:
    return month_start(now).strftime("%Y-%m")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sum_month(payload: dict[str, Any], start: datetime) -> tuple[int, int]:
    """(donated, received) over the days of `payload` that start on/after `start`.
    ClashKing sends the numbers as strings; missing or bad values count as 0."""
    donated = received = 0
    for day in payload.get("days") or []:
        try:
            if _parse_ts(day["from"]) >= start:
                donated += int(day.get("donated") or 0)
                received += int(day.get("received") or 0)
        except (KeyError, TypeError, ValueError):
            continue  # one malformed day shouldn't sink the whole account
    return donated, received


# --- the job -------------------------------------------------------------------

@dataclass(frozen=True)
class JobResult:
    status: str          # ok | partial | failed | skipped
    items_ok: int = 0
    items_failed: int = 0


async def run_donations_job(
    client: JsonClient,
    roster: RosterRepository,
    donations: DonationsRepository,
    jobs: JobRunsRepository,
    *,
    now: datetime | None = None,
) -> JobResult:
    async with jobs.exclusive(JOB_NAME) as got_lock:
        if not got_lock:
            log.info("donations job already running elsewhere; skipping this tick")
            return JobResult("skipped")

        now = now or datetime.now(timezone.utc)
        start, season = month_start(now), season_key(now)
        tags = await roster.list_account_tags()
        run_id = await jobs.start(JOB_NAME)

        async def fetch_one(tag: str) -> DonationRow:
            payload = await client.get_json(f"/v2/player/{url_tag(tag)}/activity-summary")
            donated, received = sum_month(payload, start)
            return DonationRow(tag, donated, received)

        # The client limits concurrency; one failed account doesn't stop the rest.
        results = await asyncio.gather(*(fetch_one(t) for t in tags), return_exceptions=True)
        rows = [r for r in results if isinstance(r, DonationRow)]
        errors = [(t, r) for t, r in zip(tags, results) if isinstance(r, BaseException)]
        for tag, err in errors[:10]:
            log.warning("donations: %s failed: %s", tag, err)

        try:
            await donations.upsert_season(season, rows)
        except Exception as e:
            await jobs.finish(run_id, "failed", items_ok=0, items_failed=len(tags), error=repr(e)[:500])
            raise

        if not errors:
            status = "ok"
        elif rows:
            status = "partial"
        else:
            status = "failed"
        summary = "; ".join(f"{t}: {e}" for t, e in errors[:5])[:500] or None
        await jobs.finish(run_id, status, items_ok=len(rows), items_failed=len(errors), error=summary)
        log.info("donations job %s: %d ok, %d failed (season %s)", status, len(rows), len(errors), season)
        return JobResult(status, len(rows), len(errors))
