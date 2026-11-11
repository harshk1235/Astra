"""Run a data job once, by hand, and print the result.

    python -m scripts.run_job donations          fetch, then print the leaderboard
    python -m scripts.run_job show-donations     print the leaderboard only

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
from astra.jobs.donations import run_donations_job, season_key


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.run_job")
    parser.add_argument("job", choices=["donations", "show-donations"])
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.database_url:
        raise ConfigError("DATABASE_URL is not set")
    pool = await create_pool(settings.database_url)
    try:
        await run_migrations(pool)
        donations = DonationsRepository(pool)
        if args.job == "donations":
            async with ClashKingClient(settings.clashking_base_url) as client:
                result = await run_donations_job(
                    client, RosterRepository(pool), donations, JobRunsRepository(pool)
                )
            print(f"Job {result.status}: {result.items_ok} ok, {result.items_failed} failed")

        season = season_key(datetime.now(timezone.utc))
        board = await donations.leaderboard(season)
        print(f"\nDonations {season} ({len(board)} people)")
        print(f"{'#':>3}  {'Name':<24}{'Donated':>9}{'Received':>10}")
        for i, e in enumerate(board, 1):
            print(f"{i:>3}  {e.name[:24]:<24}{e.donated:>9}{e.received:>10}")
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
