"""What the leader commands do, with no Discord code in here.

Each function checks permissions (astra.roles.rules), changes the database,
writes the audit log, and returns a Result: a message for the channel and the
Discord users whose roles need syncing. The cog turns slash commands into
these calls and does the role sync; keeping the two apart means every rule
here is tested without Discord.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astra.db.membership import Clan, MembershipRepository
from astra.jobs.roster import InGameMember
from astra.roles.rules import (MANAGER_MIN_RANK, PermissionDenied, Rank, check_can_accept, check_can_remove,
                               check_rank_change)
from astra.tags import normalize_tag

RANK_NAMES = {Rank.MEMBER: "Member", Rank.ELDER: "Elder", Rank.CO_LEADER: "Co-leader", Rank.LEADER: "Leader"}


class LeaderError(Exception):
    """A problem with the request (not a permission issue). Safe to show in Discord."""


@dataclass(frozen=True)
class Actor:
    discord_id: int
    is_admin: bool          # Administrator permission or server owner


@dataclass(frozen=True)
class Target:
    discord_id: int
    display_name: str       # e.g. "Voidmx | PR"; the part before " | " is used as their name


@dataclass
class Result:
    message: str                                    # one-line summary (also used for the log channel)
    sync_discord_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # What happened, so the command can render a nicer message:
    kind: str = ""                                  # joined | linked | unlinked | removed | promoted | demoted
    target_id: int | None = None
    clan: Clan | None = None
    rank: Rank | None = None                        # new rank (joined/linked/promoted/demoted)
    account: tuple[str, str] | None = None          # (tag, account name)
    no_clans_left: bool = False                     # removed from their last tracked clan


def person_name(display_name: str) -> str:
    return display_name.split(" | ")[0].strip() or display_name


async def _clan(repo: MembershipRepository, alias: str) -> Clan:
    clan = await repo.find_clan(alias)
    if clan is None:
        names = ", ".join(c.alias for c in await repo.list_clans()) or "none yet (use /clan add)"
        raise LeaderError(f"Unknown clan `{alias}`. Clans: {names}.")
    return clan


async def _actor_rank(repo: MembershipRepository, actor: Actor, clan_tag: str) -> Rank | None:
    return await repo.get_rank(await repo.member_id_for(actor.discord_id), clan_tag)


def _ingame_note(tag: str, clan: Clan, ingame: dict[str, InGameMember], clans: list[Clan]) -> str | None:
    """A warning if the account isn't in `clan` in-game right now."""
    m = ingame.get(tag)
    if m and m.clan_tag == clan.tag:
        return None
    if m:
        other = next((c.alias for c in clans if c.tag == m.clan_tag), m.clan_tag)
        return f"{tag} is in {other} in-game, not {clan.alias}."
    return (f"{tag} isn't in {clan.alias}'s member list yet. If they just joined, "
            "it can take a few minutes to update.")


async def joined(repo: MembershipRepository, actor: Actor, target: Target, tag: str, clan_alias: str,
                 ingame: dict[str, InGameMember]) -> Result:
    """A new member joined a clan: link their account and add them as Member."""
    clan = await _clan(repo, clan_alias)
    check_can_accept(await _actor_rank(repo, actor, clan.tag), actor_is_admin=actor.is_admin)
    tag = _tag(tag)

    member_id = await _link(repo, actor, target, tag, ingame)
    if await repo.get_rank(member_id, clan.tag) is not None:
        raise LeaderError(f"<@{target.discord_id}> is already in {clan.alias}. Use /promote or /demote to change rank.")
    await repo.set_membership(member_id, clan.tag, Rank.MEMBER, approved_by=actor.discord_id)
    await repo.log_event(action="joined", actor_discord_id=actor.discord_id, member_id=member_id,
                         clan_tag=clan.tag, account_tag=tag, to_rank=Rank.MEMBER)
    note = _ingame_note(tag, clan, ingame, await repo.list_clans())
    return Result(f"✅ <@{target.discord_id}> joined **{clan.alias}** as Member with `{tag}`.",
                  [target.discord_id], [note] if note else [], kind="joined", target_id=target.discord_id,
                  clan=clan, rank=Rank.MEMBER, account=(tag, await _account_name(repo, tag)))


async def link(repo: MembershipRepository, actor: Actor, target: Target, tag: str,
               ingame: dict[str, InGameMember], rank: Rank | None = None) -> Result:
    """Register an account for someone who's already in a clan (or an alt in no clan).
    If the account is in a tracked clan in-game and the person has no membership
    there yet, they're added at `rank` (default Member)."""
    tag = _tag(tag)
    clans = await repo.list_clans()
    m = ingame.get(tag)
    clan = next((c for c in clans if m and c.tag == m.clan_tag), None)

    if clan is None:
        # An alt, or a player outside the tracked clans: linking only, no membership.
        if not actor.is_admin:
            best = await repo.best_rank_anywhere(await repo.member_id_for(actor.discord_id))
            if best is None or best < MANAGER_MIN_RANK:
                raise PermissionDenied("Only co-leaders and leaders can link accounts.")
        member_id = await _link(repo, actor, target, tag, ingame)
        return Result(f"🔗 Linked `{tag}` to <@{target.discord_id}> (not in a tracked clan, so no clan roles).",
                      [target.discord_id], kind="linked", target_id=target.discord_id,
                      account=(tag, await _account_name(repo, tag)))

    rank = rank or Rank.MEMBER
    if rank == Rank.LEADER:
        raise PermissionDenied("Leader is assigned manually in the server, not through the bot.")
    actor_rank = await _actor_rank(repo, actor, clan.tag)
    if not actor.is_admin:
        check_can_accept(actor_rank)
        if rank > actor_rank:
            raise PermissionDenied("You can't add someone above your own rank.")
    member_id = await _link(repo, actor, target, tag, ingame)
    existing = await repo.get_rank(member_id, clan.tag)
    if existing is None:
        await repo.set_membership(member_id, clan.tag, rank, approved_by=actor.discord_id)
        await repo.log_event(action="joined", actor_discord_id=actor.discord_id, member_id=member_id,
                             clan_tag=clan.tag, account_tag=tag, to_rank=rank)
        msg = f"🔗 Linked `{tag}` ({m.name}) to <@{target.discord_id}> as **{RANK_NAMES[rank]}** in **{clan.alias}**."
    else:
        msg = (f"🔗 Linked `{tag}` ({m.name}) to <@{target.discord_id}>. They're already "
               f"{RANK_NAMES[existing]} in **{clan.alias}**.")
    return Result(msg, [target.discord_id], kind="linked", target_id=target.discord_id, clan=clan,
                  rank=existing or rank, account=(tag, m.name))


async def _link(repo: MembershipRepository, actor: Actor, target: Target, tag: str,
                ingame: dict[str, InGameMember]) -> int:
    account = await repo.get_account(tag)
    if account and account.owner_discord_id not in (None, target.discord_id):
        raise LeaderError(f"`{tag}` is already linked to <@{account.owner_discord_id}>. "
                          "Use /unlink first if it changed owner.")
    member_id = await repo.get_or_create_member(target.discord_id, person_name(target.display_name))
    m = ingame.get(tag)
    name = m.name if m else (account.name if account else tag)
    await repo.link_account(tag, member_id, name, m.th if m else None, m.clan_tag if m else None)
    if not account or account.member_id != member_id:
        await repo.log_event(action="linked", actor_discord_id=actor.discord_id, member_id=member_id,
                             account_tag=tag)
    return member_id


async def unlink(repo: MembershipRepository, actor: Actor, tag: str) -> Result:
    """Detach an account from its owner (wrong link, or the account changed hands)."""
    tag = _tag(tag)
    account = await repo.get_account(tag)
    if account is None or account.member_id is None:
        raise LeaderError(f"`{tag}` isn't linked to anyone.")
    if not actor.is_admin:
        best = await repo.best_rank_anywhere(await repo.member_id_for(actor.discord_id))
        if best is None or best < MANAGER_MIN_RANK:
            raise PermissionDenied("Only co-leaders and leaders can unlink accounts.")
    await repo.unlink_account(tag)
    await repo.log_event(action="unlinked", actor_discord_id=actor.discord_id, member_id=account.member_id,
                         account_tag=tag)
    owner = f"<@{account.owner_discord_id}>" if account.owner_discord_id else account.owner_name
    return Result(f"✂️ Unlinked `{tag}` ({account.name}) from {owner}. Their clan memberships are unchanged.",
                  [account.owner_discord_id] if account.owner_discord_id else [], kind="unlinked",
                  target_id=account.owner_discord_id, account=(tag, account.name))


async def remove(repo: MembershipRepository, actor: Actor, target: Target, clan_alias: str) -> Result:
    """Someone left or was kicked: end their membership (accounts stay linked)."""
    clan = await _clan(repo, clan_alias)
    member_id = await repo.member_id_for(target.discord_id)
    target_rank = await repo.get_rank(member_id, clan.tag)
    if target_rank is None:
        raise LeaderError(f"<@{target.discord_id}> isn't in {clan.alias}.")
    check_can_remove(await _actor_rank(repo, actor, clan.tag), target_rank,
                     actor_is_target=actor.discord_id == target.discord_id, actor_is_admin=actor.is_admin)
    await repo.delete_membership(member_id, clan.tag)
    await repo.log_event(action="left", actor_discord_id=actor.discord_id, member_id=member_id,
                         clan_tag=clan.tag, from_rank=target_rank)
    return Result(f"👋 Removed <@{target.discord_id}> from **{clan.alias}**.", [target.discord_id],
                  kind="removed", target_id=target.discord_id, clan=clan, rank=target_rank,
                  no_clans_left=not await repo.memberships_of(member_id))


async def change_rank(repo: MembershipRepository, actor: Actor, target: Target, clan_alias: str | None,
                      step: int) -> Result:
    """step=+1 promote, -1 demote. Clan can be left out if the person is in only one."""
    member_id = await repo.member_id_for(target.discord_id)
    if member_id is None:
        raise LeaderError(f"<@{target.discord_id}> isn't registered. Use /joined or /link first.")
    if clan_alias:
        clan = await _clan(repo, clan_alias)
    else:
        memberships = await repo.memberships_of(member_id)
        if len(memberships) != 1:
            raise LeaderError(f"<@{target.discord_id}> is in {len(memberships)} clans; say which one.")
        clan = next(c for c in await repo.list_clans() if c.tag == memberships[0].clan_tag)

    current = await repo.get_rank(member_id, clan.tag)
    if current is None:
        raise LeaderError(f"<@{target.discord_id}> isn't in {clan.alias}.")
    if not Rank.MEMBER <= current + step <= Rank.LEADER:
        raise LeaderError(f"<@{target.discord_id}> is already {RANK_NAMES[current]} in {clan.alias}.")
    new = Rank(current + step)
    check_rank_change(await _actor_rank(repo, actor, clan.tag), current, new,
                      actor_is_target=actor.discord_id == target.discord_id, actor_is_admin=actor.is_admin)
    await repo.set_membership(member_id, clan.tag, new, approved_by=actor.discord_id)
    await repo.log_event(action="promoted" if step > 0 else "demoted", actor_discord_id=actor.discord_id,
                         member_id=member_id, clan_tag=clan.tag, from_rank=current, to_rank=new)
    verb = "Promoted" if step > 0 else "Demoted"
    return Result(f"{'⬆️' if step > 0 else '⬇️'} {verb} <@{target.discord_id}> to **{RANK_NAMES[new]}** "
                  f"in **{clan.alias}**.", [target.discord_id], kind="promoted" if step > 0 else "demoted",
                  target_id=target.discord_id, clan=clan, rank=new)


async def _account_name(repo: MembershipRepository, tag: str) -> str:
    account = await repo.get_account(tag)
    return account.name if account else tag


def _tag(raw: str) -> str:
    try:
        return normalize_tag(raw)
    except ValueError as e:
        raise LeaderError(str(e))
