"""Leader commands: /joined /link /unlink /remove /promote /demote,
/refreshroles, /clanmembers, /clanlinked. Also restores roles when a
registered member rejoins the server.

The rules (who may do what) live in astra.leaders and astra.roles.rules and
are checked against ranks in the database, not Discord permissions, so a
co-leader of PR can manage PR whatever their Discord roles. Server admins
and the owner can do everything. How messages look is in astra.messages.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from astra import leaders
from astra.clan_option import clan_autocomplete
from astra.db.membership import MembershipRepository
from astra.jobs.roster import fetch_clan_members, fetch_ingame
from astra.leaders import Actor, LeaderError, Result, Target
from astra.messages import clan_linked_embeds, clan_members_embeds, render_result
from astra.roles.rules import MANAGER_MIN_RANK, PermissionDenied, Rank
from astra.roles.sync import RoleSyncError, change_status_roles, sync_member

log = logging.getLogger(__name__)

# Leader isn't offered: it's assigned by hand in the server.
RANK_CHOICES = [app_commands.Choice(name=n, value=v) for n, v in
                (("Member", "member"), ("Elder", "elder"), ("Co-leader", "co_leader"))]
RANKS = {"member": Rank.MEMBER, "elder": Rank.ELDER, "co_leader": Rank.CO_LEADER}
NO_PINGS = discord.AllowedMentions(users=False, roles=False, everyone=False)


def _actor(interaction: discord.Interaction) -> Actor:
    user = interaction.user
    is_admin = interaction.guild is not None and (
        interaction.guild.owner_id == user.id or getattr(user.guild_permissions, "administrator", False))
    return Actor(user.id, is_admin)


def _target(member: discord.Member) -> Target:
    return Target(member.id, member.display_name)


@app_commands.guild_only()
class Leaders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def repo(self) -> MembershipRepository:
        return MembershipRepository(self.bot.db)

    async def _ingame(self) -> tuple[dict, list[str]]:
        """Current in-game member lists of the tracked clans. If ClashKing is down,
        the command still works; it just can't check the tag."""
        clans = [c.tag for c in await self.repo().list_clans()]
        try:
            return await fetch_ingame(self.bot.clashking, clans), []
        except Exception:
            log.warning("ClashKing member lists unavailable", exc_info=True)
            return {}, ["Couldn't check the tag against the clans' member lists right now."]

    async def sync_people(self, guild: discord.Guild, discord_ids: list[int], reason: str):
        """Sync roles for these people. Returns (roles added, roles removed, notes)."""
        repo = self.repo()
        role_map, managed, common = await repo.role_map(), await repo.all_managed_role_ids(), await repo.common_roles()
        added, removed, notes = [], [], []
        for discord_id in discord_ids:
            member_id = await repo.member_id_for(discord_id)
            memberships = await repo.memberships_of(member_id) if member_id else []
            try:
                outcome = await sync_member(guild, discord_id, memberships, role_map, managed,
                                            common_roles=common, reason=reason)
            except RoleSyncError as e:
                notes.append(str(e))
                continue
            if not outcome.found:
                notes.append(f"<@{discord_id}> isn't in this server, so no roles were changed.")
            added += outcome.added_roles
            removed += outcome.removed_roles
        if not role_map:
            notes.append("No clan roles are set up yet, so none were given. Use `/clan roles`.")
        return added, removed, notes

    async def _run(self, interaction: discord.Interaction, action, *args, reason: str) -> None:
        """Shared flow: check the database exists, run the action, sync roles,
        reply, and post to the log channel."""
        if self.bot.db is None:
            return await interaction.response.send_message("This needs the database (DATABASE_URL).",
                                                           ephemeral=True)
        await interaction.response.defer()
        try:
            result: Result = await action(self.repo(), _actor(interaction), *args)
        except (PermissionDenied, LeaderError) as e:
            return await interaction.followup.send(f"❌ {e}", allowed_mentions=NO_PINGS)
        except Exception:
            log.exception("%s failed", reason)
            return await interaction.followup.send("❌ Something went wrong; it's in the bot's logs.")

        added, removed, notes = await self.sync_people(interaction.guild, result.sync_discord_ids,
                                                       f"{reason} by {interaction.user}")
        # One-off status roles: joining clears "Just Joined" / "Ex Member";
        # leaving the last clan gives "Ex Member".
        if result.target_id and result.kind in ("joined", "removed"):
            repo = self.repo()
            ex, just = await repo.status_role("ex_member"), await repo.status_role("just_joined")
            change = ({"remove": [ex, just]} if result.kind == "joined"
                      else {"add": [ex]} if result.no_clans_left else None)
            if change:
                try:
                    a, r = await change_status_roles(interaction.guild, result.target_id,
                                                     reason=f"{reason} by {interaction.user}", **change)
                    added, removed = added + a, removed + r
                except RoleSyncError as e:
                    notes.append(str(e))
        content, embed = render_result(result, added, removed, result.warnings + notes,
                                       actor=interaction.user.display_name)
        if embed is not None and result.target_id:
            target = interaction.guild.get_member(result.target_id)
            if target is not None:
                embed.set_thumbnail(url=target.display_avatar.url)
        # Ping the new member in the welcome message; nobody else.
        pings = (discord.AllowedMentions(users=[discord.Object(result.target_id)], roles=False, everyone=False)
                 if result.kind == "joined" and result.target_id else NO_PINGS)
        await interaction.followup.send(content=content, embed=embed, allowed_mentions=pings)
        await self._log(f"{result.message} · by {interaction.user.mention}")

    async def _log(self, text: str) -> None:
        channel_id = self.bot.settings.log_channel_id
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel is not None:
            try:
                await channel.send(text, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.warning("couldn't post to the log channel", exc_info=True)

    # --- a registered member rejoins the server ----------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if self.bot.db is None or member.bot or member.guild.id != self.bot.settings.guild_id:
            return
        try:
            if await self.repo().member_id_for(member.id) is None:
                return
            added, _, notes = await self.sync_people(member.guild, [member.id], "rejoined the server")
            if added:
                log.info("restored %d role(s) for %s on rejoin", len(added), member)
                await self._log(f"🔁 Restored {', '.join(f'**{r}**' for r in added)} for {member.mention} "
                                "(rejoined the server)")
            for note in notes:
                log.warning("rejoin sync for %s: %s", member, note)
        except Exception:
            log.exception("rejoin role restore failed for %s", member)

    # --- membership changes ---------------------------------------------------------

    @app_commands.command(name="joined", description="Approve a new member: link their account and add them to a clan")
    @app_commands.describe(user="The Discord member", tag="Their player tag", clan="The clan they joined")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def joined(self, interaction: discord.Interaction, user: discord.Member, tag: str, clan: str):
        ingame, notes = await self._ingame() if self.bot.db else ({}, [])
        async def action(repo, actor):
            r = await leaders.joined(repo, actor, _target(user), tag, clan, ingame)
            r.warnings += notes
            return r
        await self._run(interaction, action, reason="joined")

    @app_commands.command(name="link", description="Register an account for someone already in a clan (or an alt)")
    @app_commands.describe(user="The Discord member", tag="Their player tag",
                           rank="Their rank in that clan, if they're not registered there yet (default Member)")
    @app_commands.choices(rank=RANK_CHOICES)
    async def link(self, interaction: discord.Interaction, user: discord.Member, tag: str,
                   rank: app_commands.Choice[str] | None = None):
        ingame, notes = await self._ingame() if self.bot.db else ({}, [])
        async def action(repo, actor):
            r = await leaders.link(repo, actor, _target(user), tag, ingame, RANKS[rank.value] if rank else None)
            r.warnings += notes
            return r
        await self._run(interaction, action, reason="link")

    @app_commands.command(name="unlink", description="Detach an account from whoever it's linked to")
    @app_commands.describe(tag="The player tag")
    async def unlink(self, interaction: discord.Interaction, tag: str):
        await self._run(interaction, leaders.unlink, tag, reason="unlink")

    @app_commands.command(name="remove", description="Remove someone from a clan (they left or were kicked)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def remove(self, interaction: discord.Interaction, user: discord.Member, clan: str):
        await self._run(interaction, leaders.remove, _target(user), clan, reason="remove")

    @app_commands.command(name="promote", description="Promote someone one rank in a clan")
    @app_commands.describe(clan="Needed only if they're in more than one clan")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def promote(self, interaction: discord.Interaction, user: discord.Member, clan: str | None = None):
        await self._run(interaction, leaders.change_rank, _target(user), clan, +1, reason="promote")

    @app_commands.command(name="demote", description="Demote someone one rank in a clan")
    @app_commands.describe(clan="Needed only if they're in more than one clan")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def demote(self, interaction: discord.Interaction, user: discord.Member, clan: str | None = None):
        await self._run(interaction, leaders.change_rank, _target(user), clan, -1, reason="demote")

    # --- maintenance and lists ---------------------------------------------------------

    async def _require_manager(self, interaction: discord.Interaction) -> bool:
        actor = _actor(interaction)
        if actor.is_admin:
            return True
        repo = self.repo()
        best = await repo.best_rank_anywhere(await repo.member_id_for(actor.discord_id))
        if best is None or best < MANAGER_MIN_RANK:
            await interaction.followup.send("❌ Only co-leaders and leaders can do that.")
            return False
        return True

    @app_commands.command(name="refreshroles", description="Re-apply clan roles to every registered member")
    async def refreshroles(self, interaction: discord.Interaction):
        if self.bot.db is None:
            return await interaction.response.send_message("This needs the database.", ephemeral=True)
        await interaction.response.defer()
        if not await self._require_manager(interaction):
            return
        repo = self.repo()
        people, role_map = await repo.all_people_memberships(), await repo.role_map()
        managed, common = await repo.all_managed_role_ids(), await repo.common_roles()
        if not role_map:
            return await interaction.followup.send("No clan roles are set yet. Use `/clan roles` first.")
        added = removed = missing = 0
        for discord_id, memberships in people.items():
            try:
                outcome = await sync_member(interaction.guild, discord_id, memberships, role_map, managed,
                                            common_roles=common, reason=f"refreshroles by {interaction.user}")
            except RoleSyncError as e:
                return await interaction.followup.send(f"⚠️ Stopped: {e}")
            if not outcome.found:
                missing += 1
            added, removed = added + outcome.added, removed + outcome.removed
        msg = (f"🔄 Checked {len(people)} registered people: {added} roles added, {removed} removed"
               + (f", {missing} not in this server." if missing else "."))
        await interaction.followup.send(msg)
        await self._log(f"{msg} · by {interaction.user.mention}")

    async def _clan_for_list(self, interaction: discord.Interaction, clan: str):
        if self.bot.db is None:
            await interaction.response.send_message("This needs the database.", ephemeral=True)
            return None
        await interaction.response.defer()
        if not await self._require_manager(interaction):
            return None
        found = await self.repo().find_clan(clan)
        if found is None:
            await interaction.followup.send(f"Unknown clan `{clan}`.")
        return found

    @app_commands.command(name="clanmembers", description="Everyone in a clan in-game, and whether they're linked")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def clanmembers(self, interaction: discord.Interaction, clan: str):
        found = await self._clan_for_list(interaction, clan)
        if found is None:
            return
        try:
            ingame = await fetch_clan_members(self.bot.clashking, found.tag)
        except Exception:
            log.exception("clan member list failed")
            return await interaction.followup.send("❌ Couldn't get the clan's member list right now. Try again later.")
        accounts = await self.repo().accounts_by_tag([m.tag for m in ingame])
        await interaction.followup.send(embeds=clan_members_embeds(found, ingame, accounts),
                                        allowed_mentions=NO_PINGS)

    @app_commands.command(name="clanlinked", description="Everyone registered in a clan, flagging who has left in-game")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def clanlinked(self, interaction: discord.Interaction, clan: str):
        found = await self._clan_for_list(interaction, clan)
        if found is None:
            return
        try:
            in_clan = {m.tag for m in await fetch_clan_members(self.bot.clashking, found.tag)}
        except Exception:
            log.exception("clan member list failed")
            return await interaction.followup.send("❌ Couldn't get the clan's member list right now. Try again later.")
        people = await self.repo().clan_people(found.tag)
        await interaction.followup.send(embeds=clan_linked_embeds(found, people, in_clan), allowed_mentions=NO_PINGS)


async def setup(bot):
    await bot.add_cog(Leaders(bot))
