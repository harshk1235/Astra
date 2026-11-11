import discord
from discord.ext import commands
from discord import app_commands
import json
import logging
from typing import Optional

from astra.db.wars import WarsRepository
from astra.exports import war_stats_rows
from astra.month_option import MonthError, month_autocomplete, resolve_month
from astra.months import month_label, next_month_start
from astra.timing import timed

log = logging.getLogger(__name__)

class WarlordCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Load emojis from file
        with open("emoji.json", "r", encoding="utf-8") as f:
            self.emojis = json.load(f)

    # Hybrid: works as !warstats [#channel] [month] and as /warstats.
    @commands.hybrid_command(name="warstats", description="War leaderboard (regular wars) for a month")
    @app_commands.describe(channel="Where to post it (default: here)",
                           month="Which month (default: this month)")
    @app_commands.autocomplete(month=month_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def warstats(self, ctx, channel: Optional[discord.TextChannel] = None, month: Optional[str] = None):
        try:
            await ctx.defer()   # slash commands must be acknowledged within 3 seconds
            try:
                start = resolve_month(self.bot.settings, month)
            except MonthError as e:
                return await ctx.send(str(e), ephemeral=True)
            target = channel or ctx.channel

            with timed("warstats.load"):
                data = await self.load_rows(start)
            if len(data) <= 1:
                # Used to send nothing at all, which looked like the bot was broken.
                await ctx.send(f"No war stats for {month_label(start)} yet.")
                return

            MAX_LENGTH = 4096
            intro = f"\n**Greetings, Clashers!**\n\nJoin us in celebrating the noble feats of our champions as we unveil the war leaderboard for **{month_label(start)}**\n\n"
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
    
            if target == ctx.channel:
                for embed in embeds:
                    await ctx.send(embed=embed)
            else:
                for embed in embeds:
                    await target.send(embed=embed)
                await ctx.send(f"Posted the leaderboard in {target.mention}.", ephemeral=True)

        except Exception:
            log.exception("Error in !warstats")
            await ctx.send("❌ An error occurred while retrieving war stats.")

    async def load_rows(self, start):
        """Rows including the header, in the War Stats sheet layout. Reads
        Postgres when the bot has a database, otherwise the sheet as before
        (the sheet only has the current month)."""
        if self.bot.db is None:
            return await self.bot.sheets.get_values("War Stats")
        entries = await WarsRepository(self.bot.db).war_stats(start, next_month_start(start))
        return [[str(c) for c in r] for r in war_stats_rows(entries)]


async def setup(bot: commands.Bot):
    await bot.add_cog(WarlordCog(bot))
