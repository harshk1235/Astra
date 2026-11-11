import discord
from discord.ext import commands
from discord import app_commands
import json
import logging

from datetime import datetime, timezone

from astra.db.wars import WarsRepository
from astra.exports import next_month_start, war_stats_rows
from astra.jobs.donations import month_start
from astra.timing import timed

log = logging.getLogger(__name__)

class WarlordCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Load emojis from file
        with open("emoji.json", "r", encoding="utf-8") as f:
            self.emojis = json.load(f)

    @commands.command(name="warstats", help="Displays the war leaderboard")
    @commands.has_permissions(administrator=True)
    async def warstats(self, ctx, channel: discord.TextChannel = None):
        try:
            if channel is None:
                channel = ctx.channel
    
            with timed("warstats.load"):
                data = await self.load_rows()
            if len(data) <= 1:
                # Used to send nothing at all, which looked like the bot was broken.
                await ctx.send("No war stats yet this month.")
                return
    
            MAX_LENGTH = 4096
            intro = "\n**Greetings, Clashers!**\n\nJoin us in celebrating the noble feats of our champions as we unveil the monthly war leaderboard for Phoenix Reborn\n\n"
            heading = "<:blank_space:1229767163221381173><:blank_space:1229767163221381173>`Atk\tTrp\tH/R%\t Dest`\n\n"
            body = ""
            embeds = []
    
            for row in data[1:]:
                th = f"th{row[1]}"
                emoji = self.emojis.get(th, "")
                line = f"{emoji}<:blank_space:1229767163221381173>`{row[2]:^3}\t{row[3]:^3}\t{row[4]:>3}%\t {row[5]:>4}` \t {row[0]}\n"
    
                # Calculate length with intro/heading for first embed only
                limit = MAX_LENGTH - len(heading) - (len(intro) if not embeds else 0)
    
                if len(body) + len(line) >= limit:
                    embed = discord.Embed(
                        title="🏆 War Leaderboard 🏆" if not embeds else None,
                        description=intro + heading + body if not embeds else body,
                        color=0x000000
                    )
                    embed.set_footer(
                        text="Phoenix Reborn",
                        icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png"
                    )
                    embeds.append(embed)
                    body = ""
    
                body += line
    
            # Add final embed
            if body:
                embed = discord.Embed(
                    title="🏆 War Leaderboard 🏆" if not embeds else None,
                    description=intro + heading + body if not embeds else body,
                    color=0x000000
                )
                embed.set_footer(
                    text="Phoenix Reborn",
                    icon_url="https://media.discordapp.net/attachments/1178548363948462100/1187010410767994880/phoenix.png"
                )
                embeds.append(embed)
    
            for embed in embeds:
                await channel.send(embed=embed)
    
        except Exception:
            log.exception("Error in !warstats")
            await ctx.send("❌ An error occurred while retrieving war stats.")

    async def load_rows(self):
        """Rows including the header, in the War Stats sheet layout. Reads
        Postgres when the bot has a database, otherwise the sheet as before."""
        if self.bot.db is None:
            return await self.bot.sheets.get_values("War Stats")
        now = datetime.now(timezone.utc)
        entries = await WarsRepository(self.bot.db).war_stats(month_start(now), next_month_start(now))
        return [[str(c) for c in r] for r in war_stats_rows(entries)]


async def setup(bot: commands.Bot):
    await bot.add_cog(WarlordCog(bot))
