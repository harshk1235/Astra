"""Donations job: ClashKing -> Postgres.

Counts only donations made inside the tracked clans (the clans table: PR and
DOP). A player who donated 2,000 in some other clan before joining PR gets
credit only for what they donated after joining.

Source: GET /v2/player/{tag}/history/stats?type=donated|received
Each item is one change of the in-game counter, tagged with the clan the
player was in at the time:
    {"eventTime": "...", "clanTag": "#28VUPJRPU", "delta": 58, ...}
We sum the deltas since the start of the calendar month (UTC), keeping only
events in tracked clans. Results come newest first, so when a page is full
we ask again for events before the oldest one we saw.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from astra.clashking import ClashKingError
from astra.db.donations import DonationRow, DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.tags import url_tag

log = logging.getLogger(__name__)

JOB_NAME = "donations"
PAGE_SIZE = 500
MAX_PAGES = 20   # 10,000 events per account per month is far beyond any real player


class JsonClient(Protocol):
    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


# --- pure helpers (unit tested without network or database) -----------------

def month_start(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def season_key(now: datetime) -> str:
    return month_start(now).strftime("%Y-%m")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sum_events(items: Iterable[dict[str, Any]], clan_tags: set[str]) -> int:
    """Total of positive deltas for events in `clan_tags`.
    Negative or zero deltas (e.g. the in-game counter resetting at season
    end) and malformed items are ignored."""
    total = 0
    for e in items:
        try:
            delta = int(e.get("delta") or 0)
        except (TypeError, ValueError):
            continue
        if delta > 0 and e.get("clanTag") in clan_tags:
            total += delta
    return total


async def fetch_month_events(client: JsonClient, tag: str, stat: str, start: datetime) -> list[dict[str, Any]]:
    """All `stat` events for `tag` since `start`, following pages as needed."""
    path = f"/v2/player/{url_tag(tag)}/history/stats"
    events: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    before: str | None = None
    for _ in range(MAX_PAGES):
        params = {"type": stat, "time[after]": _iso(start), "limit": PAGE_SIZE}
        if before:
            params["time[before]"] = before
        items = (await client.get_json(path, params=params) or {}).get("items") or []
        for e in items:
            key = (e.get("eventTime"), e.get("previousValue"), e.get("clanTag"))
            if key not in seen:   # pages overlap if two events share a timestamp
                seen.add(key)
                events.append(e)
        if len(items) < PAGE_SIZE:
            return events
        before = items[-1].get("eventTime")
        if not before:
            break
    raise ClashKingError(f"{tag}: more than {MAX_PAGES} pages of {stat} events")


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
        clan_tags = await roster.list_tracked_clan_tags()
        if not clan_tags:
            log.warning("donations job: no tracked clans (add one with scripts.seed add-clan)")
            return JobResult("skipped")
        # Every linked account, not just those in a tracked clan right now:
        # someone who left PR mid-month still donated in PR before leaving.
        tags = await roster.list_account_tags()
        run_id = await jobs.start(JOB_NAME)

        async def fetch_one(tag: str) -> DonationRow:
            donated_events, received_events = await asyncio.gather(
                fetch_month_events(client, tag, "donated", start),
                fetch_month_events(client, tag, "received", start),
            )
            return DonationRow(tag, sum_events(donated_events, clan_tags), sum_events(received_events, clan_tags))

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
        log.info("donations job %s: %d ok, %d failed (season %s, clans %s)",
                 status, len(rows), len(errors), season, ", ".join(sorted(clan_tags)))
        return JobResult(status, len(rows), len(errors))
