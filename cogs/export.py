"""/export [month] [clan]: every number the bot has, one row per account, as a
CSV file. Made for leaders who track stats in a spreadsheet or an AI tool
instead of running several commands."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from astra.alldata import all_data_rows
from astra.clan_option import clan_autocomplete, resolve_filters
from astra.csv_export import safe_filename, to_csv_file
from astra.db.membership import MembershipRepository
from astra.month_option import MonthError, month_autocomplete
from astra.months import month_key, month_label
from astra.roles.rules import MANAGER_MIN_RANK

log = logging.getLogger(__name__)


@app_commands.guild_only()
class Export(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="export", description="Download every stat for a month as one CSV table")
    @app_commands.describe(month="Which month (default: this month)", clan="One clan only (default: all clans)")
    @app_commands.autocomplete(month=month_autocomplete, clan=clan_autocomplete)
    async def export(self, interaction: discord.Interaction, month: str | None = None, clan: str | None = None):
        if self.bot.db is None:
            return await interaction.response.send_message("This needs the database.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        is_admin = interaction.guild.owner_id == user.id or user.guild_permissions.administrator
        if not is_admin:
            repo = MembershipRepository(self.bot.db)
            best = await repo.best_rank_anywhere(await repo.member_id_for(user.id))
            if best is None or best < MANAGER_MIN_RANK:
                return await interaction.followup.send("❌ Only co-leaders and leaders can export data.")
        try:
            start, clan_ = await resolve_filters(self.bot, month, clan)
        except MonthError as e:
            return await interaction.followup.send(str(e))
        rows = await all_data_rows(self.bot.db, start, clan_.tag if clan_ else None)
        name = safe_filename("astra", clan_.alias if clan_ else "all", month_key(start))
        await interaction.followup.send(
            f"📊 {len(rows) - 1} accounts · {month_label(start)} · {clan_.name if clan_ else 'all clans'}. "
            "One row per account: owner, ranks, TH, donations, war stats and CWL day by day.",
            file=to_csv_file(rows, name))


async def setup(bot):
    await bot.add_cog(Export(bot))
