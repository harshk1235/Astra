"""Leader commands: /joined /link /unlink /remove /promote /demote,
/refreshroles, /clanmembers, /clanlinked.

The rules (who may do what) live in astra.leaders and astra.roles.rules and
are checked against ranks in the database, not Discord permissions, so a
co-leader of PR can manage PR whatever their Discord roles. Server admins
and the owner can do everything.
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
from astra.roles.rules import MANAGER_MIN_RANK, PermissionDenied, Rank
from astra.roles.sync import RoleSyncError, sync_member

log = logging.getLogger(__name__)

RANK_CHOICES = [app_commands.Choice(name=n, value=v) for n, v in
                (("Member", "member"), ("Elder", "elder"), ("Co-leader", "co_leader"), ("Leader", "leader"))]
RANKS = {"member": Rank.MEMBER, "elder": Rank.ELDER, "co_leader": Rank.CO_LEADER, "leader": Rank.LEADER}
NO_PINGS = discord.AllowedMentions(users=False, roles=False, everyone=False)


def _actor(interaction: discord.Interaction) -> Actor:
    user = interaction.user
    is_admin = interaction.guild is not None and (
        interaction.guild.owner_id == user.id or getattr(user.guild_permissions, "administrator", False))
    return Actor(user.id, is_admin)


def _target(member: discord.Member) -> Target:
    return Target(member.id, member.display_name)


def _chunk_embeds(title: str, lines: list[str], header: str = "") -> list[discord.Embed]:
    """Split long lists into embeds under Discord's 4096-character limit."""
    embeds, body = [], header
    for line in lines:
        if len(body) + len(line) + 1 > 4000:
            embeds.append(discord.Embed(title=title if not embeds else None, description=body, color=0x000000))
            body = ""
        body += line + "\n"
    embeds.append(discord.Embed(title=title if not embeds else None, description=body or "—", color=0x000000))
    return embeds[:10]


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
            return {}, ["Couldn't reach ClashKing to check the tag against the clans' member lists."]

    async def _sync(self, guild: discord.Guild, discord_ids: list[int], reason: str) -> list[str]:
        repo = self.repo()
        role_map, managed = await repo.role_map(), await repo.all_managed_role_ids()
        notes = []
        for discord_id in discord_ids:
            member_id = await repo.member_id_for(discord_id)
            memberships = await repo.memberships_of(member_id) if member_id else []
            try:
                outcome = await sync_member(guild, discord_id, memberships, role_map, managed, reason=reason)
                if not outcome.found:
                    notes.append(f"<@{discord_id}> isn't in this server, so no roles were changed.")
            except RoleSyncError as e:
                notes.append(f"⚠️ {e}")
        return notes

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
        notes = result.warnings + await self._sync(interaction.guild, result.sync_discord_ids,
                                                   f"{reason} by {interaction.user}")
        text = result.message + "".join(f"\n> {n}" for n in notes)
        await interaction.followup.send(text, allowed_mentions=NO_PINGS)
        await self._log(f"{result.message} · by {interaction.user.mention}")

    async def _log(self, text: str) -> None:
        channel_id = self.bot.settings.log_channel_id
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel is not None:
            try:
                await channel.send(text, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.warning("couldn't post to the log channel", exc_info=True)

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
        people, role_map, managed = (await repo.all_people_memberships(), await repo.role_map(),
                                     await repo.all_managed_role_ids())
        if not role_map:
            return await interaction.followup.send("No clan roles are set yet. Use `/clan roles` first.")
        added = removed = missing = 0
        for discord_id, memberships in people.items():
            try:
                outcome = await sync_member(interaction.guild, discord_id, memberships, role_map, managed,
                                            reason=f"refreshroles by {interaction.user}")
            except RoleSyncError as e:
                return await interaction.followup.send(f"⚠️ Stopped: {e}")
            if not outcome.found:
                missing += 1
            added, removed = added + outcome.added, removed + outcome.removed
        msg = (f"🔄 Checked {len(people)} registered people: {added} roles added, {removed} removed"
               + (f", {missing} not in this server." if missing else "."))
        await interaction.followup.send(msg)
        await self._log(f"{msg} · by {interaction.user.mention}")

    @app_commands.command(name="clanmembers", description="Everyone in a clan in-game, and whether they're linked")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def clanmembers(self, interaction: discord.Interaction, clan: str):
        if self.bot.db is None:
            return await interaction.response.send_message("This needs the database.", ephemeral=True)
        await interaction.response.defer()
        if not await self._require_manager(interaction):
            return
        repo = self.repo()
        found = await repo.find_clan(clan)
        if found is None:
            return await interaction.followup.send(f"Unknown clan `{clan}`.")
        try:
            ingame = await fetch_clan_members(self.bot.clashking, found.tag)
        except Exception:
            log.exception("clan member list failed")
            return await interaction.followup.send("❌ Couldn't get the member list from ClashKing. Try again later.")
        accounts = await repo.accounts_by_tag([m.tag for m in ingame])
        ingame.sort(key=lambda m: (-(m.th or 0), m.name.lower()))
        linked = [m for m in ingame if accounts.get(m.tag) and accounts[m.tag].owner_discord_id]
        unlinked = [m for m in ingame if m not in linked]
        lines = [f"**Not linked ({len(unlinked)})** · use `/link`"]
        lines += [f"❌ TH{m.th} **{m.name}** `{m.tag}`" for m in unlinked] or ["Everyone is linked 🎉"]
        lines += ["", f"**Linked ({len(linked)})**"]
        lines += [f"✅ TH{m.th} {m.name} → <@{accounts[m.tag].owner_discord_id}>" for m in linked]
        header = f"{len(ingame)} accounts in **{found.name}** right now.\n\n"
        await interaction.followup.send(embeds=_chunk_embeds(f"{found.alias} members (in-game)", lines, header),
                                        allowed_mentions=NO_PINGS)

    @app_commands.command(name="clanlinked", description="Everyone registered in a clan, flagging who has left in-game")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def clanlinked(self, interaction: discord.Interaction, clan: str):
        if self.bot.db is None:
            return await interaction.response.send_message("This needs the database.", ephemeral=True)
        await interaction.response.defer()
        if not await self._require_manager(interaction):
            return
        repo = self.repo()
        found = await repo.find_clan(clan)
        if found is None:
            return await interaction.followup.send(f"Unknown clan `{clan}`.")
        try:
            in_clan = {m.tag for m in await fetch_clan_members(self.bot.clashking, found.tag)}
        except Exception:
            log.exception("clan member list failed")
            return await interaction.followup.send("❌ Couldn't get the member list from ClashKing. Try again later.")
        people = await repo.clan_people(found.tag)
        gone = [p for p in people if not any(a.tag in in_clan for a in p.accounts)]
        lines = []
        if gone:
            lines.append(f"**⚠️ No account in {found.alias} in-game ({len(gone)})** · `/remove` if they left")
            lines += [f"• <@{p.discord_user_id}> ({p.display_name})" if p.discord_user_id else f"• {p.display_name}"
                      for p in gone]
            lines.append("")
        lines.append(f"**Registered ({len(people)})**")
        for p in people:
            who = f"<@{p.discord_user_id}>" if p.discord_user_id else p.display_name
            accs = ", ".join(f"{a.name}{' ✅' if a.tag in in_clan else ''}" for a in p.accounts) or "no accounts"
            lines.append(f"{leaders.RANK_NAMES[p.rank]} · {who}: {accs}")
        header = "✅ = that account is in the clan in-game right now.\n\n"
        await interaction.followup.send(embeds=_chunk_embeds(f"{found.alias} registered members", lines, header),
                                        allowed_mentions=NO_PINGS)


async def setup(bot):
    await bot.add_cog(Leaders(bot))
