"""Scheduled data jobs. Runs only when the bot has a database (DATABASE_URL).

Every hour: fetch from ClashKing into Postgres, then export the leaderboards
to the Google Sheet (unless SHEETS_EXPORT=false). Each step is recorded in
job_runs, so a failing export shows up even though the fetch succeeded.
"""
import logging

from discord.ext import commands, tasks

from astra.clashking import ClashKingClient
from astra.db.donations import DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.db.wars import WarsRepository
from astra.exports import export_donations, export_war_stats
from astra.jobs.donations import run_donations_job
from astra.jobs.wars import run_wars_job

log = logging.getLogger(__name__)


class Jobs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client: ClashKingClient | None = None

    async def cog_load(self):
        if self.bot.db is None:
            log.info("No database configured; scheduled jobs are disabled")
            return
        self.client = ClashKingClient(self.bot.settings.clashking_base_url)
        await self.client.__aenter__()
        self.donations_loop.start()
        self.wars_loop.start()

    async def cog_unload(self):
        self.donations_loop.cancel()
        self.wars_loop.cancel()
        if self.client is not None:
            await self.client.close()

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
    async def donations_loop(self):
        try:
            repo = DonationsRepository(self.bot.db)
            result = await run_donations_job(
                self.client, RosterRepository(self.bot.db), repo, JobRunsRepository(self.bot.db)
            )
            if result.status in ("ok", "partial"):
                await self._export("donations", export_donations, repo)
        except Exception:
            log.exception("donations job crashed")

    @tasks.loop(minutes=60)
    async def wars_loop(self):
        try:
            repo = WarsRepository(self.bot.db)
            result = await run_wars_job(
                self.client, RosterRepository(self.bot.db), repo, JobRunsRepository(self.bot.db)
            )
            if result.status in ("ok", "partial"):
                await self._export("war_stats", export_war_stats, repo)
        except Exception:
            log.exception("wars job crashed")


async def setup(bot):
    await bot.add_cog(Jobs(bot))
