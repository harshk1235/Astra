"""/profile [user]: someone's linked accounts with TH, heroes and roles."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from astra.db.membership import MembershipRepository
from astra.profile import from_database, from_official, mark_default, profile_embeds
from astra.tags import url_tag

log = logging.getLogger(__name__)


@app_commands.guild_only()
class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="profile", description="Show someone's linked accounts")
    @app_commands.describe(user="Whose profile (default: yours)")
    async def profile(self, interaction: discord.Interaction, user: discord.Member | None = None):
        if self.bot.db is None:
            return await interaction.response.send_message("This needs the database.", ephemeral=True)
        await interaction.response.defer()
        user = user or interaction.user
        repo = MembershipRepository(self.bot.db)
        member_id = await repo.member_id_for(user.id)
        accounts = await repo.accounts_of(member_id) if member_id else []
        ranks = {m.clan_tag: m.rank for m in await repo.memberships_of(member_id)} if member_id else {}
        clans = {c.tag: c for c in await repo.list_clans()}

        note = None
        if self.bot.coc is not None and accounts:
            async def one(a):
                try:
                    return from_official(a, await self.bot.coc.get_json(f"/players/{url_tag(a.tag)}"))
                except Exception:
                    log.warning("official API failed for %s", a.tag, exc_info=True)
                    return from_database(a, ranks, clans)
            shown = await asyncio.gather(*(one(a) for a in accounts))
        else:
            shown = [from_database(a, ranks, clans) for a in accounts]
            if accounts:
                note = "Hero levels appear once the official Clash of Clans API is set up."
        embeds = profile_embeds(user.display_name, user.id, user.name, user.display_avatar.url,
                                mark_default(list(shown)), note)
        await interaction.followup.send(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Profile(bot))
