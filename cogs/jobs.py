"""Scheduled data jobs. Runs only when the bot has a database (DATABASE_URL).

Every hour: fetch donations, regular wars and CWL from ClashKing into
Postgres, then export the leaderboards to the Google Sheet (unless
SHEETS_EXPORT=false). Every day: delete data older than the retention
window. Each step is recorded in job_runs, so a failing export shows up even
though the fetch succeeded.
"""
import logging
from datetime import datetime, timezone

from discord.ext import commands, tasks

from astra.db.donations import DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.db.membership import MembershipRepository
from astra.db.roster import RosterRepository
from astra.db.wars import WarsRepository
from astra.db.retention import delete_before
from astra.exports import export_all_data, export_cwl, export_donations, export_members, export_war_stats
from astra.months import retention_cutoff
from astra.jobs.donations import run_donations_job
from astra.jobs.roster import sync_roster
from astra.jobs.wars import run_wars_job

log = logging.getLogger(__name__)


class Jobs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def client(self):
        return self.bot.clashking   # shared with the leader commands

    async def cog_load(self):
        if self.bot.db is None:
            log.info("No database configured; scheduled jobs are disabled")
            return
        self.roster_loop.start()
        self.donations_loop.start()
        self.wars_loop.start()
        self.cleanup_loop.start()

    async def cog_unload(self):
        self.roster_loop.cancel()
        self.donations_loop.cancel()
        self.wars_loop.cancel()
        self.cleanup_loop.cancel()

    async def _export(self, name: str, export, repo) -> None:
        if not self.bot.settings.sheets_export:
            log.info("export:%s skipped (SHEETS_EXPORT=false)", name)
            return
        jobs = JobRunsRepository(self.bot.db)
        run_id = await jobs.start(f"export:{name}")
        try:
            rows = await export(self.bot.sheets, repo)
            await jobs.finish(run_id, "ok", items_ok=rows, items_failed=0)
            log.info("export:%s ok: %d rows written to the sheet", name, rows)
        except Exception as e:
            log.exception("export:%s failed", name)
            await jobs.finish(run_id, "failed", items_ok=0, items_failed=1, error=repr(e)[:500])

    # One bad run must never kill a loop: log it (it's also in job_runs) and retry next hour.

    @tasks.loop(minutes=60)
    async def roster_loop(self):
        """Keep linked accounts' names, TH and in-game clan current."""
        try:
            seen, left = await sync_roster(self.client, MembershipRepository(self.bot.db))
            log.info("roster: %d accounts in tracked clans, %d linked accounts left one", seen, left)
            await self._export("members", export_members, self.bot.db)
        except Exception:
            log.exception("roster sync crashed")

    @tasks.loop(minutes=60)
    async def donations_loop(self):
        try:
            repo = DonationsRepository(self.bot.db)
            result = await run_donations_job(
                self.client, RosterRepository(self.bot.db), repo, JobRunsRepository(self.bot.db)
            )
            if result.status in ("ok", "partial"):
                await self._export("donations", export_donations, repo)
                await self._export("all_data", export_all_data, self.bot.db)
        except Exception:
            log.exception("donations job crashed")

    @tasks.loop(minutes=60)
    async def wars_loop(self):
        repo = WarsRepository(self.bot.db)
        for war_type, name, export in (("regular", "war_stats", export_war_stats), ("cwl", "cwl", export_cwl)):
            try:
                result = await run_wars_job(
                    self.client, RosterRepository(self.bot.db), repo, JobRunsRepository(self.bot.db),
                    war_type=war_type,
                )
                if result.status in ("ok", "partial"):
                    await self._export(name, export, repo)
            except Exception:
                log.exception("%s wars job crashed", war_type)
        await self._export("all_data", export_all_data, self.bot.db)

    @tasks.loop(hours=24)
    async def cleanup_loop(self):
        try:
            s = self.bot.settings
            cutoff = retention_cutoff(datetime.now(timezone.utc), s.data_retention_months, s.data_start_month)
            result = await delete_before(self.bot.db, cutoff)
            log.info("cleanup: removed data before %s (%d wars, %d donation rows, %d job runs)",
                     cutoff.date(), result.wars, result.donation_rows, result.job_runs)
        except Exception:
            log.exception("cleanup crashed")


async def setup(bot):
    await bot.add_cog(Jobs(bot))
