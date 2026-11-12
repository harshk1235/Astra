"""Run data jobs by hand.

    python -m scripts.run_job donations        fetch donations, print leaderboard
    python -m scripts.run_job wars             fetch regular wars, print war stats
    python -m scripts.run_job cwl              fetch CWL, print CWL leaderboard
    python -m scripts.run_job backfill         fetch everything for every month the bot keeps
    python -m scripts.run_job show             print all leaderboards from Postgres only
    python -m scripts.run_job export           write all five tabs to the Google Sheet
    python -m scripts.run_job rebuild-sheet --yes
                                               write the five tabs and DELETE every other tab
    python -m scripts.run_job cleanup          delete data older than the retention window
    add --month 2026-10 to fetch, show or export a specific month

With Docker:  docker compose run --rm bot python -m scripts.run_job backfill
(after pulling new code, rebuild first: docker compose build)
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
from astra.db.retention import delete_before
from astra.db.roster import RosterRepository
from astra.db.wars import WarsRepository
from astra.exports import SHEET_TABS, export_everything, rebuild_sheet
from astra.jobs.donations import run_donations_job
from astra.jobs.wars import run_wars_job
from astra.months import (available_months, month_key, month_label, month_start, next_month_start,
                          parse_month, retention_cutoff)
from astra.sheets import SheetsClient


async def print_donations(repo: DonationsRepository, now: datetime) -> None:
    board = await repo.leaderboard(month_key(now))
    print(f"\nDonations {month_label(now)} ({len(board)} people)")
    print(f"{'#':>3}  {'Name':<24}{'Donated':>9}{'Received':>10}")
    for i, e in enumerate(board, 1):
        print(f"{i:>3}  {e.name[:24]:<24}{e.donated:>9}{e.received:>10}")


async def print_wars(repo: WarsRepository, now: datetime) -> None:
    board = await repo.war_stats(month_start(now), next_month_start(now))
    print(f"\nWar stats {month_label(now)} ({len(board)} accounts, regular wars only)")
    print(f"{'#':>3}  {'Player':<22}{'TH':>3}{'Atk':>5}{'3*':>5}{'HR%':>5}{'Dest':>8}")
    for i, e in enumerate(board, 1):
        print(f"{i:>3}  {e.name[:22]:<22}{e.th:>3}{e.attacks:>5}{e.triples:>5}{e.hit_rate:>5}{e.destruction:>8.0f}")


async def print_cwl(repo: WarsRepository, now: datetime) -> None:
    board = await repo.cwl_stats(month_start(now), next_month_start(now))
    print(f"\nCWL {month_label(now)} ({len(board)} accounts)")
    print(f"{'#':>3}  {'Account':<20}{'Clan':<16}{'TH':>3}{'Hits':>5}{'3*':>4}{'Stars':>6}  Days (stars)")
    for i, e in enumerate(board, 1):
        days = " ".join(str(e.days[d][0]) if d in e.days else "-" for d in range(1, 8))
        print(f"{i:>3}  {e.name[:20]:<20}{e.clan[:16]:<16}{e.th:>3}{e.hits:>5}{e.triples:>4}{e.stars:>6}  {days}")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.run_job")
    parser.add_argument("job", choices=["donations", "wars", "cwl", "backfill", "show", "export", "rebuild-sheet",
                                        "cleanup"])
    parser.add_argument("--month", help="YYYY-MM (default: current month)")
    parser.add_argument("--yes", action="store_true", help="confirm rebuild-sheet (deletes tabs)")
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.database_url:
        raise ConfigError("DATABASE_URL is not set")
    today = datetime.now(timezone.utc)
    now = parse_month(args.month).replace(day=15) if args.month else today

    pool = await create_pool(settings.database_url)
    try:
        await run_migrations(pool)
        roster, jobs = RosterRepository(pool), JobRunsRepository(pool)
        donations, wars = DonationsRepository(pool), WarsRepository(pool)

        async def fetch(job: str, when: datetime) -> None:
            async with ClashKingClient(settings.clashking_base_url) as client:
                if job == "donations":
                    r = await run_donations_job(client, roster, donations, jobs, now=when)
                else:
                    r = await run_wars_job(client, roster, wars, jobs,
                                           war_type="regular" if job == "wars" else "cwl", now=when)
            print(f"{job:<9} {month_label(when):<15} {r.status}: {r.items_ok} ok, {r.items_failed} failed")

        if args.job in ("donations", "wars", "cwl"):
            await fetch(args.job, now)
        elif args.job == "backfill":
            months = available_months(today, settings.data_retention_months, settings.data_start_month)
            for m in reversed(months):
                for job in ("wars", "cwl", "donations"):
                    await fetch(job, m.replace(day=15))
            print("(ClashKing's donation history starts around 5 Nov 2026; earlier months show no donations.)")

        if args.job in ("donations", "show"):
            await print_donations(donations, now)
        if args.job in ("wars", "show"):
            await print_wars(wars, now)
        if args.job in ("cwl", "show"):
            await print_cwl(wars, now)

        if args.job == "export":
            sheets = SheetsClient(settings.google_credentials_json, settings.google_sheet_url, writable=True)
            written = await export_everything(sheets, pool, now)
            print(f"Wrote {month_label(now)}: " + ", ".join(f"{tab} {n} rows" for tab, n in written.items()))

        if args.job == "rebuild-sheet":
            if not args.yes:
                raise SystemExit("This deletes every tab except " + ", ".join(SHEET_TABS)
                                 + ". Make a copy of the sheet first (File > Make a copy), then add --yes.")
            sheets = SheetsClient(settings.google_credentials_json, settings.google_sheet_url, writable=True)
            written, deleted = await rebuild_sheet(sheets, pool, now)
            print("Wrote: " + ", ".join(f"{tab} {n} rows" for tab, n in written.items()))
            print(f"Deleted {len(deleted)} old tabs: " + ", ".join(deleted))

        if args.job == "cleanup":
            cutoff = retention_cutoff(today, settings.data_retention_months, settings.data_start_month)
            r = await delete_before(pool, cutoff)
            print(f"Removed data before {cutoff.date()}: {r.wars} wars, {r.donation_rows} donation rows, "
                  f"{r.job_runs} old job runs")
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
