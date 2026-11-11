"""/clan add | roles | list | remove: admins manage which clans the bot tracks.

A clan's alias (e.g. PR) is how every other command refers to it. Adding a
clan makes the fetchers, leaderboards, leader commands and role sync pick it
up immediately; nothing is hard-coded.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from astra.clan_option import clan_autocomplete
from astra.db.membership import MembershipRepository
from astra.roles.rules import Rank
from astra.tags import InvalidTag, normalize_tag, url_tag

log = logging.getLogger(__name__)

RANK_CHOICES = [
    app_commands.Choice(name="Member", value="member"),
    app_commands.Choice(name="Elder", value="elder"),
    app_commands.Choice(name="Co-leader", value="co_leader"),
    app_commands.Choice(name="Leader (shared by all clans)", value="leader"),
]
RANKS = {"member": Rank.MEMBER, "elder": Rank.ELDER, "co_leader": Rank.CO_LEADER, "leader": Rank.LEADER}


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class ClanCommands(commands.GroupCog, group_name="clan", group_description="Manage the clans Astra tracks"):
    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    async def _ready(self, interaction: discord.Interaction) -> MembershipRepository | None:
        if self.bot.db is None:
            await interaction.response.send_message("This needs the database (DATABASE_URL).", ephemeral=True)
            return None
        return MembershipRepository(self.bot.db)

    @app_commands.command(name="add", description="Track a clan (or rename / re-activate one)")
    @app_commands.describe(tag="Clan tag, e.g. #28VUPJRPU", alias="Short name used in commands and roles, e.g. PR",
                           member_role="Role for Members", elder_role="Role for Elders",
                           coleader_role="Role for Co-leaders", leader_role="Shared Leader role (all clans)")
    async def add(self, interaction: discord.Interaction, tag: str, alias: str,
                  member_role: discord.Role | None = None, elder_role: discord.Role | None = None,
                  coleader_role: discord.Role | None = None, leader_role: discord.Role | None = None):
        repo = await self._ready(interaction)
        if repo is None:
            return
        await interaction.response.defer()
        try:
            tag = normalize_tag(tag)
        except InvalidTag as e:
            return await interaction.followup.send(str(e))
        alias = alias.strip()
        if not alias or len(alias) > 10 or " " in alias:
            return await interaction.followup.send("Use a short alias without spaces, e.g. `PR`.")
        try:
            data = await self.bot.clashking.get_json(f"/v2/clan/{url_tag(tag)}/cached")
            name, members = data["name"], len(data.get("members") or [])
        except Exception:
            log.exception("clan lookup failed")
            return await interaction.followup.send(f"Couldn't find clan `{tag}` on ClashKing. Check the tag.")
        try:
            clan = await repo.add_clan(tag, name, alias)
        except ValueError as e:
            return await interaction.followup.send(str(e))
        for rank, role in ((Rank.MEMBER, member_role), (Rank.ELDER, elder_role), (Rank.CO_LEADER, coleader_role)):
            if role:
                await repo.set_clan_role(clan.tag, rank, role.id)
        if leader_role:
            await repo.set_leader_role(leader_role.id)
        await interaction.followup.send(
            f"✅ Tracking **{clan.name}** (`{clan.tag}`, {members} members) as **{clan.alias}**.\n"
            f"Its wars, CWL and donations are fetched from the next hourly run. "
            f"Run `/clan list` to check its roles, then `/link` or `/joined` to register members.")

    @app_commands.command(name="roles", description="Set which Discord role a clan rank gets")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @app_commands.choices(rank=RANK_CHOICES)
    async def roles(self, interaction: discord.Interaction, clan: str, rank: app_commands.Choice[str],
                    role: discord.Role):
        repo = await self._ready(interaction)
        if repo is None:
            return
        found = await repo.find_clan(clan)
        if found is None:
            return await interaction.response.send_message(f"Unknown clan `{clan}`.", ephemeral=True)
        if rank.value == "leader":
            await repo.set_leader_role(role.id)
            msg = f"✅ {role.mention} is now the Leader role for every clan."
        else:
            await repo.set_clan_role(found.tag, RANKS[rank.value], role.id)
            msg = f"✅ {found.alias} {rank.name}s now get {role.mention}."
        await interaction.response.send_message(msg + " Run `/refreshroles` to apply it to everyone.",
                                                allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="list", description="Show tracked clans and their roles")
    async def list_(self, interaction: discord.Interaction):
        repo = await self._ready(interaction)
        if repo is None:
            return
        clans, role_map = await repo.list_clans(), await repo.role_map()
        if not clans:
            return await interaction.response.send_message("No clans yet. Add one with `/clan add`.")
        lines = []
        for c in clans:
            roles = role_map.get(c.tag, {})
            fmt = lambda r: f"<@&{roles[r]}>" if r in roles else "*not set*"
            lines.append(f"**{c.alias}** · {c.name} `{c.tag}`\n"
                         f"Member {fmt(Rank.MEMBER)} · Elder {fmt(Rank.ELDER)} · "
                         f"Co-leader {fmt(Rank.CO_LEADER)} · Leader {fmt(Rank.LEADER)}")
        await interaction.response.send_message(
            embed=discord.Embed(title="Tracked clans", description="\n\n".join(lines), color=0x000000),
            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="remove", description="Stop tracking a clan (its history is kept)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def remove(self, interaction: discord.Interaction, clan: str):
        repo = await self._ready(interaction)
        if repo is None:
            return
        found = await repo.find_clan(clan)
        if found is None:
            return await interaction.response.send_message(f"Unknown clan `{clan}`.", ephemeral=True)
        await repo.deactivate_clan(found.tag)
        await interaction.response.send_message(
            f"✅ Stopped tracking **{found.alias}**. Run `/refreshroles` to remove its roles from members. "
            f"`/clan add` with the same tag brings it back.")


async def setup(bot):
    await bot.add_cog(ClanCommands(bot))
