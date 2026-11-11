"""Run a data job once, by hand, and print the result.

    python -m scripts.run_job donations            fetch donations, print leaderboard
    python -m scripts.run_job wars                 fetch regular wars, print war stats
    python -m scripts.run_job show                 print both leaderboards from Postgres only
    python -m scripts.run_job export               write both leaderboards to the Google Sheet
    add --month 2026-10 to fetch, show or export a past month (backfill)

With Docker:  docker compose run --rm bot python -m scripts.run_job donations
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from astra.clashking import ClashKingClient
from astra.config import ConfigError, Settings
from astra.db import create_pool, run_migrations
from astra.db.donations import DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.db.wars import WarsRepository
from astra.exports import export_donations, export_war_stats, next_month_start
from astra.jobs.donations import month_start, run_donations_job, season_key
from astra.jobs.wars import run_wars_job
from astra.sheets import SheetsClient


async def print_donations(repo: DonationsRepository, now: datetime) -> None:
    board = await repo.leaderboard(season_key(now))
    print(f"\nDonations {season_key(now)} ({len(board)} people)")
    print(f"{'#':>3}  {'Name':<24}{'Donated':>9}{'Received':>10}")
    for i, e in enumerate(board, 1):
        print(f"{i:>3}  {e.name[:24]:<24}{e.donated:>9}{e.received:>10}")


async def print_wars(repo: WarsRepository, now: datetime) -> None:
    board = await repo.war_stats(month_start(now), next_month_start(now))
    print(f"\nWar stats {season_key(now)} ({len(board)} accounts, regular wars only)")
    print(f"{'#':>3}  {'Player':<22}{'TH':>3}{'Atk':>5}{'3*':>5}{'HR%':>5}{'Dest':>8}")
    for i, e in enumerate(board, 1):
        print(f"{i:>3}  {e.name[:22]:<22}{e.th:>3}{e.attacks:>5}{e.triples:>5}{e.hit_rate:>5}{e.destruction:>8.0f}")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.run_job")
    parser.add_argument("job", choices=["donations", "wars", "show", "export"])
    parser.add_argument("--month", help="YYYY-MM to fetch/show/export (default: current month)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.month:
        now = datetime.strptime(args.month + "-15", "%Y-%m-%d").replace(tzinfo=timezone.utc)

    settings = Settings.from_env()
    if not settings.database_url:
        raise ConfigError("DATABASE_URL is not set")
    pool = await create_pool(settings.database_url)
    try:
        await run_migrations(pool)
        roster, jobs = RosterRepository(pool), JobRunsRepository(pool)
        donations, wars = DonationsRepository(pool), WarsRepository(pool)

        if args.job in ("donations", "wars"):
            async with ClashKingClient(settings.clashking_base_url) as client:
                if args.job == "donations":
                    result = await run_donations_job(client, roster, donations, jobs, now=now)
                else:
                    result = await run_wars_job(client, roster, wars, jobs, now=now)
            print(f"Job {result.status}: {result.items_ok} ok, {result.items_failed} failed")

        if args.job in ("donations", "show"):
            await print_donations(donations, now)
        if args.job in ("wars", "show"):
            await print_wars(wars, now)

        if args.job == "export":
            sheets = SheetsClient(settings.google_credentials_json, settings.google_sheet_url, writable=True)
            n = await export_donations(sheets, donations, now)
            m = await export_war_stats(sheets, wars, now)
            print(f"Wrote {n} rows to 'Donations' and {m} rows to 'War Stats'")
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
