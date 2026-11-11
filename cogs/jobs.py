"""Scheduled data jobs. Runs only when the bot has a database (DATABASE_URL)."""
import logging

from discord.ext import commands, tasks

from astra.clashking import ClashKingClient
from astra.db.donations import DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.db.roster import RosterRepository
from astra.jobs.donations import run_donations_job

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

    async def cog_unload(self):
        self.donations_loop.cancel()
        if self.client is not None:
            await self.client.close()

    @tasks.loop(minutes=60)
    async def donations_loop(self):
        # Never let one bad run kill the loop: log it and try again next hour.
        # The failure is also recorded in job_runs.
        try:
            await run_donations_job(
                self.client,
                RosterRepository(self.bot.db),
                DonationsRepository(self.bot.db),
                JobRunsRepository(self.bot.db),
            )
        except Exception:
            log.exception("donations job crashed")


async def setup(bot):
    await bot.add_cog(Jobs(bot))
