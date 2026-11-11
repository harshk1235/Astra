import logging
import os

import discord
from discord.ext import commands

from astra.config import Settings
from astra.db import create_pool, run_migrations
from astra.sheets import SheetsClient

log = logging.getLogger("astra")


class Astra(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings  # cogs read config from here
        # One Sheets client shared by all cogs (no network call happens here).
        self.sheets = SheetsClient(settings.google_credentials_json, settings.google_sheet_url)
        self.db = None  # asyncpg pool, set in setup_hook when DATABASE_URL is configured

    async def setup_hook(self) -> None:
        if self.settings.database_url:
            # Fail fast: if the database is unreachable, crash at startup
            # (Docker restarts us) rather than fail on the first command.
            self.db = await create_pool(self.settings.database_url)
            applied = await run_migrations(self.db)
            log.info("Database ready (%d new migration(s) applied)", len(applied))
        else:
            log.warning("DATABASE_URL not set; running in Sheets-only mode")

        # Runs once, before connecting. on_ready can fire again after every
        # reconnect, which used to re-load cogs (ExtensionAlreadyLoaded errors)
        # and re-sync slash commands each time.
        for filename in sorted(os.listdir("./cogs")):
            if filename.endswith(".py"):
                cog_name = filename[:-3]
                try:
                    await self.load_extension(f"cogs.{cog_name}")
                    log.info("Loaded cog: %s", cog_name)
                except Exception:
                    log.exception("Failed to load cog: %s", cog_name)

        guild = discord.Object(id=self.settings.guild_id)
        # Slash commands are defined globally in the cogs; copying them to the
        # guild makes them appear there immediately (global sync can take ~1h).
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d slash command(s) to guild %s", len(synced), self.settings.guild_id)
        except Exception:
            log.exception("Slash command sync failed")

    async def close(self) -> None:
        await super().close()
        if self.db is not None:
            await self.db.close()

    async def on_ready(self) -> None:
        log.info("%s is online.", self.user)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    bot = Astra(settings)

    @bot.command()
    async def ping(ctx):
        await ctx.send("Pong!")

    bot.run(settings.discord_token, log_handler=None)  # keep our logging config


if __name__ == "__main__":
    main()
