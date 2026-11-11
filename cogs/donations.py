import discord
from discord.ext import commands
from discord import app_commands
import logging

from astra.db.donations import DonationsRepository
from astra.db.jobs import JobRunsRepository
from astra.exports import donation_rows
from astra.month_option import MonthError, month_autocomplete, resolve_month
from astra.months import month_key, month_label
from astra.timing import timed

log = logging.getLogger(__name__)


class Donations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def hello(self,ctx):
        await ctx.send("Hello from donations cog")
    
    @commands.command(name="donos")
    @commands.has_permissions(administrator=True)
    async def donos_prefix(self, ctx, month: str = None):
        await self.send_donos_embed(ctx, month)

    @app_commands.command(name="donos", description="Displays donation leaderboard")
    @app_commands.describe(month="Which month (default: this month)")
    @app_commands.autocomplete(month=month_autocomplete)
    async def donos_slash(self, interaction: discord.Interaction, month: str = None):
        await self.send_donos_embed(interaction, month)

    async def send_donos_embed(self, source, month=None):
        is_slash = isinstance(source, discord.Interaction)
        if is_slash:
            # Discord fails a slash command that gets no response within 3 seconds.
            # The sheet read can take longer, so acknowledge first and reply after.
            await source.response.defer()
        send = source.followup.send if is_slash else source.send
        try:
            start = resolve_month(self.bot.settings, month)
        except MonthError as e:
            return await send(str(e))
        try:
            with timed("donos.load"):
                data, updated_at = await self.load_rows(start)
            if not data:
                return await send(f"No donation data for {month_label(start)}.")
            heading = "`Donated\tReceived`\n\n"
            text = ""

            for row in data:
                name = row[0]
                donated = row[1]
                received = row[2]
                text += f"`{donated:^7}\t{received:^8}`   {name}\n"

            embed = discord.Embed(
                title=f"📤 Donation Leaderboard · {month_label(start)} 📥",
                description="\n\n💰 Here's a look at who’s fueling the clan with generosity this season! Check out the top donors and receivers below. \n\n" + heading + text,
                color=discord.Color.from_rgb(0, 0, 0)
            )
            embed.set_footer(
                text="Phoenix Reborn",
                icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png?ex=68500f97&is=684ebe17&hm=8dde095ff75caa82b9e8a2ded1b35682220e8a47ba0cf3026c64f491b89ea8d3&=&format=webp&quality=lossless"
            )

            if updated_at is not None:
                embed.timestamp = updated_at  # Discord shows it as "Last updated" local time

            if is_slash:
                await source.followup.send(embed=embed)
            else:
                await source.send(embed=embed)

        except Exception:
            log.exception("Failed to build donation leaderboard")
            if is_slash:
                await source.followup.send("Failed to fetch donation stats.")
            else:
                await source.send("Failed to fetch donation stats.")

    async def load_rows(self, start):
        """(rows without header, last-updated time). Reads Postgres when the
        bot has a database, otherwise the Donations sheet as before (the sheet
        only has the current month)."""
        if self.bot.db is None:
            return (await self.bot.sheets.get_values("Donations"))[1:], None
        entries = await DonationsRepository(self.bot.db).leaderboard(month_key(start))
        rows = [[str(c) for c in r] for r in donation_rows(entries)[1:]]
        return rows, await JobRunsRepository(self.bot.db).last_success("donations")


async def setup(bot):
    cog = Donations(bot)
    await bot.add_cog(cog)
    
