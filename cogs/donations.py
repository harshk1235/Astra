import discord
from discord.ext import commands
from discord import app_commands
import logging

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
    async def donos_prefix(self, ctx):
        await self.send_donos_embed(ctx)

    @app_commands.command(name="donos", description="Displays donation leaderboard")
    async def donos_slash(self, interaction: discord.Interaction):
        await self.send_donos_embed(interaction)

    async def send_donos_embed(self, source):
        is_slash = isinstance(source, discord.Interaction)
        if is_slash:
            # Discord fails a slash command that gets no response within 3 seconds.
            # The sheet read can take longer, so acknowledge first and reply after.
            await source.response.defer()
        try:
            with timed("donos.load"):
                data = (await self.bot.sheets.get_values("Donations"))[1:]  # skip header row
            heading = "`Donated\tReceived`\n\n"
            text = ""

            for row in data:
                name = row[0]
                donated = row[1]
                received = row[2]
                text += f"`{donated:^7}\t{received:^8}`   {name}\n"

            embed = discord.Embed(
                title="📤 Donation Leaderboard 📥",
                description="\n\n💰 Here's a look at who’s fueling the clan with generosity this season! Check out the top donors and receivers below. \n\n" + heading + text,
                color=discord.Color.from_rgb(0, 0, 0)
            )
            embed.set_footer(
                text="Phoenix Reborn",
                icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png?ex=68500f97&is=684ebe17&hm=8dde095ff75caa82b9e8a2ded1b35682220e8a47ba0cf3026c64f491b89ea8d3&=&format=webp&quality=lossless"
            )

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

async def setup(bot):
    cog = Donations(bot)
    await bot.add_cog(cog)
    
